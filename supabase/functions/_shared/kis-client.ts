const KIS_REAL_BASE_URL = "https://openapi.koreainvestment.com:9443";
const DAILY_PRICE_PATH = "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice";
const DAILY_INDEX_PATH = "/uapi/domestic-stock/v1/quotations/inquire-daily-indexchartprice";
const MARKET_INVESTOR_PATH = "/uapi/domestic-stock/v1/quotations/inquire-investor-daily-by-market";

export type KisDailyPrice = {
  marketDate: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number | null;
};

export type KisDailyPriceBundle = { instrumentName: string; prices: KisDailyPrice[] };
export type KisEtfHolding = { ticker: string | null; name: string | null; weightPct: number | null };
export type KisMarketDay = { marketDate: string; tradingValue: number };

type KisCredentials = { appKey: string; appSecret: string };
type KisTokenStore = { from: (table: string) => any };
type KisIssuedToken = { accessToken: string; expiresAt: string };

const KIS_TOKEN_CACHE_KEY = "kis_access_token_prod";
const TOKEN_EXPIRY_MARGIN_MS = 10 * 60_000;

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

export async function fetchKisDailyPriceBundle(
  credentials: KisCredentials,
  accessToken: string,
  ticker: string,
  start: Date,
  end: Date,
): Promise<KisDailyPriceBundle> {
  if (!/^\d{6}$/.test(ticker)) throw new Error("ETF 종목코드는 6자리 숫자여야 합니다.");
  const params = new URLSearchParams({
    FID_COND_MRKT_DIV_CODE: "J",
    FID_INPUT_ISCD: ticker,
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
  if (!/^\d{6}$/.test(ticker)) throw new Error("ETF 종목코드는 6자리 숫자여야 합니다.");
  const params = new URLSearchParams({
    FID_COND_MRKT_DIV_CODE: "J",
    FID_INPUT_ISCD: ticker,
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
