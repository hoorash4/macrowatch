const KIS_REAL_BASE_URL = "https://openapi.koreainvestment.com:9443";
const DAILY_PRICE_PATH = "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice";

export type KisDailyPrice = {
  marketDate: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number | null;
};

export type KisDailyPriceBundle = { instrumentName: string; prices: KisDailyPrice[] };

type KisCredentials = { appKey: string; appSecret: string };

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

export async function issueKisAccessToken(credentials: KisCredentials) {
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
  return token;
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
