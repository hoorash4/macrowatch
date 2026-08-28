const KIS_REAL_BASE_URL = "https://openapi.koreainvestment.com:9443";
const DAILY_PRICE_PATH = "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice";
const ETF_CURRENT_PRICE_PATH = "/uapi/etfetn/v1/quotations/inquire-price";
const DAILY_INDEX_PATH = "/uapi/domestic-stock/v1/quotations/inquire-daily-indexchartprice";
const MARKET_INVESTOR_PATH = "/uapi/domestic-stock/v1/quotations/inquire-investor-daily-by-market";
const OVERSEAS_MARKET_CAP_PATH = "/uapi/overseas-stock/v1/ranking/market-cap";

export type KisDailyPrice = {
  marketDate: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number | null;
};

export type KisDailyPriceBundle = { instrumentName: string; prices: KisDailyPrice[] };
export type KisEtfCurrentPrice = { current: number; open: number; volume: number | null };
export type KisEtfHolding = { ticker: string | null; name: string | null; weightPct: number | null };
export type KisMarketDay = { marketDate: string; tradingValue: number };
export type KisMarketCapRow = {
  ticker: string;
  name: string;
  rank: number;
  marketCap: number;
  exchange: string;
};

type KisCredentials = { appKey: string; appSecret: string };
type KisTokenStore = { from: (table: string) => any };
type KisIssuedToken = { accessToken: string; expiresAt: string };

const KIS_TOKEN_CACHE_KEY = "kis_access_token_prod";
const TOKEN_EXPIRY_MARGIN_MS = 10 * 60_000;
// 가격·구성종목·수급 등 모든 KIS 호출이 같은 앱 키 한도를 공유한다. 호출자는
// 이 실행기를 통해 요청 간격과 일시적인 초당 제한 재시도를 동일하게 적용한다.
export const KIS_REQUEST_INTERVAL_MS = 500;
export const KIS_RATE_LIMIT_RETRY_DELAYS_MS = [1_200, 2_500, 5_000];

function normalizeEtfTicker(value: string) {
  const ticker = value.trim().toUpperCase();
  if (!/^[A-Z0-9]{6}$/.test(ticker)) {
    throw new Error("ETF 종목코드는 영문 대문자와 숫자로 구성된 6자리여야 합니다.");
  }
  return ticker;
}

function requiredSecret(name: string) {
  const value = Deno.env.get(name)?.trim();
  if (!value) throw new Error(`${name}가 설정되지 않았습니다.`);
  return value;
}

export function loadKisCredentials(): KisCredentials {
  return {
    appKey: requiredSecret("KIS_APP_KEY"),
    appSecret: requiredSecret("KIS_APP_SECRET"),
  };
}

async function readJson(response: Response) {
  const text = await response.text();
  try { return JSON.parse(text) as Record<string, unknown>; }
  catch { throw new Error(`KIS가 JSON이 아닌 응답을 반환했습니다. (${response.status})`); }
}

export async function issueKisAccessToken(credentials: KisCredentials): Promise<KisIssuedToken> {
  const response = await fetch(`${KIS_REAL_BASE_URL}/oauth2/tokenP`, {
    method: "POST",
    headers: { "Content-Type": "application/json; charset=utf-8" },
    body: JSON.stringify({
      grant_type: "client_credentials",
      appkey: credentials.appKey,
      appsecret: credentials.appSecret,
    }),
    signal: AbortSignal.timeout(30_000),
  });
  const payload = await readJson(response);
  const token = typeof payload.access_token === "string" ? payload.access_token : "";
  if (!response.ok || !token) {
    const message = String(payload.error_description || payload.msg1 || payload.error || "인증 실패");
    throw new Error(`KIS 토큰 발급 실패 (${response.status}): ${message}`);
  }
  const expiresIn = Number(payload.expires_in);
  const lifetimeMs = Number.isFinite(expiresIn) && expiresIn > 0 ? expiresIn * 1000 : 24 * 3_600_000;
  return { accessToken: token, expiresAt: new Date(Date.now() + lifetimeMs).toISOString() };
}

// 모든 Edge Function이 같은 토큰을 공유합니다. 만료 직전까지 DB 캐시를 재사용하고,
// 새 토큰은 저장에 성공한 뒤에만 반환해 중복 발급 가능성을 낮춥니다.
export async function getKisAccessToken(credentials: KisCredentials, store: KisTokenStore) {
  const { data, error } = await store.from("app_settings")
    .select("value").eq("key", KIS_TOKEN_CACHE_KEY).maybeSingle();
  if (error) throw new Error(`KIS 토큰 캐시 조회 실패: ${error.message}`);
  const value = data?.value && typeof data.value === "object" ? data.value as Record<string, unknown> : {};
  const cachedToken = typeof value.access_token === "string" ? value.access_token : "";
  const expiresAt = typeof value.expires_at === "string" ? Date.parse(value.expires_at) : Number.NaN;
  if (cachedToken && Number.isFinite(expiresAt) && expiresAt - Date.now() > TOKEN_EXPIRY_MARGIN_MS) {
    return cachedToken;
  }

  const issued = await issueKisAccessToken(credentials);
  const { error: saveError } = await store.from("app_settings").upsert({
    key: KIS_TOKEN_CACHE_KEY,
    value: { access_token: issued.accessToken, expires_at: issued.expiresAt },
    updated_at: new Date().toISOString(),
    updated_by: null,
  }, { onConflict: "key" });
  if (saveError) throw new Error(`KIS 토큰 캐시 저장 실패: ${saveError.message}`);
  return issued.accessToken;
}

function compactDate(date: Date) {
  return date.toISOString().slice(0, 10).replaceAll("-", "");
}

function numberValue(value: unknown) {
  const parsed = Number(String(value ?? "").replaceAll(",", ""));
  return Number.isFinite(parsed) ? parsed : null;
}

function positiveInteger(value: unknown) {
  const parsed = numberValue(value);
  return parsed !== null && Number.isInteger(parsed) && parsed > 0 ? parsed : null;
}

function wait(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export function isKisRateLimitError(error: unknown) {
  const message = error instanceof Error ? error.message : String(error);
  return message.includes("초당 거래건수를 초과");
}

/**
 * 한 Edge Function 실행 안의 KIS 요청을 직렬화한다. 서로 다른 실행이 순간적으로
 * 겹쳐도 초당 제한 응답만 단계적으로 재시도하므로 수동 등록이 실패 상태에 고정되지 않는다.
 */
export function createKisRequestRunner() {
  let lastStartedAt = 0;
  return async <T>(request: () => Promise<T>) => {
    for (let attempt = 0;; attempt += 1) {
      const intervalRemaining = KIS_REQUEST_INTERVAL_MS - (Date.now() - lastStartedAt);
      if (intervalRemaining > 0) await wait(intervalRemaining);
      lastStartedAt = Date.now();
      try {
        return await request();
      } catch (error) {
        if (!isKisRateLimitError(error) || attempt >= KIS_RATE_LIMIT_RETRY_DELAYS_MS.length) throw error;
        await wait(KIS_RATE_LIMIT_RETRY_DELAYS_MS[attempt]);
      }
    }
  };
}

/** Fetch one U.S. exchange's market-cap order for later universe composition. */
export async function fetchKisOverseasMarketCapRanking(
  credentials: KisCredentials,
  accessToken: string,
  exchange: "NAS" | "NYS" | "AMS",
  limit: number,
): Promise<KisMarketCapRow[]> {
  if (!Number.isInteger(limit) || limit < 1) throw new Error("시가총액 순위 개수는 양의 정수여야 합니다.");
  const collected = new Map<string, KisMarketCapRow>();
  let continuation = "";
  let keyBuffer = "";

  for (let page = 0; page < 10 && collected.size < limit; page += 1) {
    const params = new URLSearchParams({ EXCD: exchange, VOL_RANG: "0", KEYB: keyBuffer, AUTH: "" });
    const response = await fetch(`${KIS_REAL_BASE_URL}${OVERSEAS_MARKET_CAP_PATH}?${params}`, {
      headers: {
        authorization: `Bearer ${accessToken}`,
        appkey: credentials.appKey,
        appsecret: credentials.appSecret,
        tr_id: "HHDFS76350100",
        tr_cont: continuation,
        custtype: "P",
      },
      signal: AbortSignal.timeout(30_000),
    });
    const payload = await readJson(response);
    if (!response.ok || String(payload.rt_cd ?? "0") !== "0") {
      throw new Error(`KIS 해외 시가총액 순위 조회 실패 (${response.status}): ${String(payload.msg1 || "알 수 없는 오류")}`);
    }
    const rows = Array.isArray(payload.output2) ? payload.output2 : [];
    const before = collected.size;
    rows.forEach((raw) => {
      const row = raw as Record<string, unknown>;
      const ticker = String(row.symb || "").trim().toUpperCase();
      const name = String(row.name || row.ename || "").trim();
      const rank = positiveInteger(row.rank) ?? collected.size + 1;
      const marketCap = numberValue(row.tomv ?? row.mcap);
      if (!ticker || !name || marketCap === null || marketCap < 0) return;
      collected.set(ticker, { ticker, name, rank, marketCap, exchange });
    });
    const pageInfo = payload.output1 && typeof payload.output1 === "object"
      ? payload.output1 as Record<string, unknown>
      : {};
    keyBuffer = String(pageInfo.keyb || pageInfo.KEYB || "").trim();
    const next = String(response.headers.get("tr_cont") || "").toUpperCase();
    if (!new Set(["M", "F"]).has(next) || collected.size === before) break;
    continuation = "N";
    await new Promise((resolve) => setTimeout(resolve, 350));
  }

  const result = [...collected.values()].sort((a, b) => a.rank - b.rank).slice(0, limit)
    .map((row, index) => ({ ...row, rank: index + 1 }));
  if (result.length !== limit) {
    throw new Error(`KIS ${exchange} 시가총액 순위가 ${result.length}/${limit}개만 반환되었습니다.`);
  }
  return result;
}

export async function fetchKisDailyPriceBundle(
  credentials: KisCredentials,
  accessToken: string,
  ticker: string,
  start: Date,
  end: Date,
): Promise<KisDailyPriceBundle> {
  const normalizedTicker = normalizeEtfTicker(ticker);
  const params = new URLSearchParams({
    FID_COND_MRKT_DIV_CODE: "J",
    FID_INPUT_ISCD: normalizedTicker,
    FID_INPUT_DATE_1: compactDate(start),
    FID_INPUT_DATE_2: compactDate(end),
    FID_PERIOD_DIV_CODE: "D",
    FID_ORG_ADJ_PRC: "0",
  });
  const response = await fetch(`${KIS_REAL_BASE_URL}${DAILY_PRICE_PATH}?${params}`, {
    headers: {
      authorization: `Bearer ${accessToken}`,
      appkey: credentials.appKey,
      appsecret: credentials.appSecret,
      tr_id: "FHKST03010100",
      custtype: "P",
    },
    signal: AbortSignal.timeout(30_000),
  });
  const payload = await readJson(response);
  if (!response.ok || String(payload.rt_cd ?? "0") !== "0") {
    throw new Error(`KIS 일봉 조회 실패 (${response.status}): ${String(payload.msg1 || "알 수 없는 오류")}`);
  }
  const profile = payload.output1 && typeof payload.output1 === "object"
    ? payload.output1 as Record<string, unknown>
    : {};
  const instrumentName = String(profile.hts_kor_isnm || profile.prdt_name || "").trim();
  const rows = Array.isArray(payload.output2) ? payload.output2 : [];
  const prices = rows.flatMap((raw) => {
    const row = raw as Record<string, unknown>;
    const date = String(row.stck_bsop_date || "");
    const open = numberValue(row.stck_oprc), high = numberValue(row.stck_hgpr);
    const low = numberValue(row.stck_lwpr), close = numberValue(row.stck_clpr);
    if (!/^\d{8}$/.test(date) || open === null || high === null || low === null || close === null) return [];
    return [{
      marketDate: `${date.slice(0, 4)}-${date.slice(4, 6)}-${date.slice(6, 8)}`,
      open, high, low, close,
      volume: numberValue(row.acml_vol),
    }];
  });
  return { instrumentName, prices };
}

export async function fetchKisDailyPrices(
  credentials: KisCredentials,
  accessToken: string,
  ticker: string,
  start: Date,
  end: Date,
): Promise<KisDailyPrice[]> {
  return (await fetchKisDailyPriceBundle(credentials, accessToken, ticker, start, end)).prices;
}

/** ETF/ETN 현재가 API로 장중 최신 체결가를 읽습니다. */
export async function fetchKisEtfCurrentPrice(
  credentials: KisCredentials,
  accessToken: string,
  ticker: string,
): Promise<KisEtfCurrentPrice> {
  const normalizedTicker = normalizeEtfTicker(ticker);
  const params = new URLSearchParams({
    FID_COND_MRKT_DIV_CODE: "J",
    FID_INPUT_ISCD: normalizedTicker,
  });
  const response = await fetch(`${KIS_REAL_BASE_URL}${ETF_CURRENT_PRICE_PATH}?${params}`, {
    headers: {
      authorization: `Bearer ${accessToken}`,
      appkey: credentials.appKey,
      appsecret: credentials.appSecret,
      tr_id: "FHPST02400000",
      custtype: "P",
    },
    signal: AbortSignal.timeout(30_000),
  });
  const payload = await readJson(response);
  if (!response.ok || String(payload.rt_cd ?? "0") !== "0") {
    throw new Error(`KIS ETF 현재가 조회 실패 (${response.status}): ${String(payload.msg1 || "알 수 없는 오류")}`);
  }
  const output = payload.output && typeof payload.output === "object"
    ? payload.output as Record<string, unknown>
    : {};
  const current = numberValue(output.stck_prpr), open = numberValue(output.stck_oprc);
  if (current === null || current <= 0 || open === null || open <= 0) {
    throw new Error("KIS ETF 현재가 응답에 유효한 현재가 또는 시가가 없습니다.");
  }
  return { current, open, volume: numberValue(output.acml_vol) };
}

// 코스피 일봉의 거래대금은 외국인 순매수 금액을 시장 규모로 정규화할 때 사용합니다.
export async function fetchKisKospiMarketDays(
  credentials: KisCredentials, accessToken: string, start: Date, end: Date,
): Promise<KisMarketDay[]> {
  const params = new URLSearchParams({
    FID_COND_MRKT_DIV_CODE: "U", FID_INPUT_ISCD: "0001",
    FID_INPUT_DATE_1: compactDate(start), FID_INPUT_DATE_2: compactDate(end), FID_PERIOD_DIV_CODE: "D",
  });
  const response = await fetch(`${KIS_REAL_BASE_URL}${DAILY_INDEX_PATH}?${params}`, {
    headers: { authorization: `Bearer ${accessToken}`, appkey: credentials.appKey, appsecret: credentials.appSecret, tr_id: "FHKUP03500100", custtype: "P" },
    signal: AbortSignal.timeout(30_000),
  });
  const payload = await readJson(response);
  if (!response.ok || String(payload.rt_cd ?? "0") !== "0") throw new Error(`KIS 코스피 일봉 조회 실패 (${response.status}): ${String(payload.msg1 || "알 수 없는 오류")}`);
  return (Array.isArray(payload.output2) ? payload.output2 : []).flatMap((raw) => {
    const row = raw as Record<string, unknown>, date = String(row.stck_bsop_date || ""), tradingValue = numberValue(row.acml_tr_pbmn);
    return /^\d{8}$/.test(date) && tradingValue !== null && tradingValue > 0
      ? [{ marketDate: `${date.slice(0, 4)}-${date.slice(4, 6)}-${date.slice(6, 8)}`, tradingValue }] : [];
  });
}

export async function fetchKisKospiForeignNetBuy(
  credentials: KisCredentials, accessToken: string, marketDate: string,
): Promise<number | null> {
  const compact = marketDate.replaceAll("-", "");
  const params = new URLSearchParams({
    FID_COND_MRKT_DIV_CODE: "U", FID_INPUT_ISCD: "0001", FID_INPUT_DATE_1: compact,
    FID_INPUT_ISCD_1: "KSP", FID_INPUT_DATE_2: compact, FID_INPUT_ISCD_2: "0001",
  });
  const response = await fetch(`${KIS_REAL_BASE_URL}${MARKET_INVESTOR_PATH}?${params}`, {
    headers: { authorization: `Bearer ${accessToken}`, appkey: credentials.appKey, appsecret: credentials.appSecret, tr_id: "FHPTJ04040000", custtype: "P" },
    signal: AbortSignal.timeout(30_000),
  });
  const payload = await readJson(response);
  if (!response.ok || String(payload.rt_cd ?? "0") !== "0") throw new Error(`KIS 외국인 수급 조회 실패 (${response.status}): ${String(payload.msg1 || "알 수 없는 오류")}`);
  const rows = Array.isArray(payload.output) ? payload.output : Array.isArray(payload.output1) ? payload.output1 : [];
  const row = rows.find((item) => String((item as Record<string, unknown>).stck_bsop_date || "") === compact) as Record<string, unknown> | undefined;
  return row ? numberValue(row.frgn_ntby_tr_pbmn) : null;
}

// KIS ETF 구성종목시세에서 국내 상장기업만 남기고 실제 편입비중 상위 종목을 반환합니다.
export async function fetchKisEtfTopHoldings(
  credentials: KisCredentials,
  accessToken: string,
  ticker: string,
  limit = 3,
): Promise<KisEtfHolding[]> {
  const normalizedTicker = normalizeEtfTicker(ticker);
  const params = new URLSearchParams({
    FID_COND_MRKT_DIV_CODE: "J",
    FID_INPUT_ISCD: normalizedTicker,
    FID_COND_SCR_DIV_CODE: "11216",
  });
  const response = await fetch(`${KIS_REAL_BASE_URL}/uapi/etfetn/v1/quotations/inquire-component-stock-price?${params}`, {
    headers: {
      authorization: `Bearer ${accessToken}`,
      appkey: credentials.appKey,
      appsecret: credentials.appSecret,
      tr_id: "FHKST121600C0",
      custtype: "P",
    },
    signal: AbortSignal.timeout(30_000),
  });
  const payload = await readJson(response);
  if (!response.ok || String(payload.rt_cd ?? "0") !== "0") {
    throw new Error(`KIS ETF 구성종목 조회 실패 (${response.status}): ${String(payload.msg1 || "알 수 없는 오류")}`);
  }
  const rows = Array.isArray(payload.output2) ? payload.output2 : [];
  return rows.map((raw) => {
    const row = raw as Record<string, unknown>;
    // KIS 원문을 검증·제외하지 않고 보존하며, 비중이 없는 행은 정렬의 마지막에 둡니다.
    return {
      ticker: String(row.stck_shrn_iscd || "").trim() || null,
      name: String(row.hts_kor_isnm || "").trim() || null,
      weightPct: numberValue(row.etf_cnfg_issu_rlim),
    };
  }).sort((a, b) => (b.weightPct ?? Number.NEGATIVE_INFINITY) - (a.weightPct ?? Number.NEGATIVE_INFINITY)).slice(0, limit);
}
