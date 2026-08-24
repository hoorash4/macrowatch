import "jsr:@supabase/functions-js/edge-runtime.d.ts";
import { createClient } from "https://esm.sh/@supabase/supabase-js@2";
import { calculateMarketContext, type MarketCandle } from "../_shared/market-indicators.ts";

const API_URL = "https://apis.data.go.kr/1160100/service/GetMarketIndexInfoService/getStockMarketIndex";
const BOOTSTRAP_DAYS = 140, REFRESH_DAYS = 10, CONCURRENCY = 1, REQUEST_INTERVAL_MS = 250;

function json(body: unknown, status = 200) { return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json; charset=utf-8" } }); }
function dateKey(date: Date) { return `${date.getUTCFullYear()}${String(date.getUTCMonth() + 1).padStart(2, "0")}${String(date.getUTCDate()).padStart(2, "0")}`; }
function toNumber(value: unknown) { const parsed = Number(String(value ?? "").replaceAll(",", "")); return Number.isFinite(parsed) ? parsed : null; }
function readItems(payload: Record<string, unknown>) { const body = payload.response && typeof payload.response === "object" ? (payload.response as Record<string, unknown>).body : null; const items = body && typeof body === "object" ? (body as Record<string, unknown>).items : null; const item = items && typeof items === "object" ? (items as Record<string, unknown>).item : []; return Array.isArray(item) ? item : item ? [item] : []; }
async function fetchCandle(key: string, date: Date): Promise<MarketCandle | null> {
  // The portal commonly issues an already URL-encoded key. Normalize it before
  // URLSearchParams encodes it once for the outgoing request.
  const params = new URLSearchParams({ serviceKey: decodeURIComponent(key), resultType: "json", pageNo: "1", numOfRows: "10", basDt: dateKey(date), idxNm: "코스피" });
  const response = await fetch(`${API_URL}?${params}`, { signal: AbortSignal.timeout(30_000) });
  if (!response.ok) throw new Error(`공공데이터 API 오류 (${response.status})`);
  const item = readItems(await response.json() as Record<string, unknown>).find((row) => String((row as Record<string, unknown>).idxNm).includes("코스피")) as Record<string, unknown> | undefined;
  if (!item) return null;
  const open = toNumber(item.mkp), high = toNumber(item.hipr), low = toNumber(item.lopr), close = toNumber(item.clpr);
  if (open === null || high === null || low === null || close === null) return null;
  const dateValue = String(item.basDt);
  return { date: `${dateValue.slice(0, 4)}-${dateValue.slice(4, 6)}-${dateValue.slice(6, 8)}`, open, high, low, close, volume: toNumber(item.trqu) };
}
async function mapConcurrent<T, R>(items: T[], mapper: (item: T) => Promise<R>) { const results: R[] = []; let cursor = 0; await Promise.all(Array.from({ length: Math.min(CONCURRENCY, items.length) }, async () => { while (cursor < items.length) { const item = items[cursor++]; results.push(await mapper(item)); if (cursor < items.length) await new Promise((resolve) => setTimeout(resolve, REQUEST_INTERVAL_MS)); } })); return results; }

Deno.serve(async (request) => {
  if (request.method !== "POST") return json({ error: "POST 요청만 허용됩니다." }, 405);
  try {
    const key = Deno.env.get("PUBLIC_DATA_API_KEY"), url = Deno.env.get("SUPABASE_URL"), serviceRole = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY");
    if (!key) throw new Error("PUBLIC_DATA_API_KEY가 설정되지 않았습니다.");
    if (!url || !serviceRole) throw new Error("Supabase 서버 설정이 없습니다.");
    const supabase = createClient(url, serviceRole);
    const { count, error: countError } = await supabase.from("market_index_prices").select("market_date", { count: "exact", head: true }).eq("index_code", "KOSPI");
    if (countError) throw countError;
    const days = (count || 0) >= 80 ? REFRESH_DAYS : BOOTSTRAP_DAYS, today = new Date();
    const dates = Array.from({ length: days }, (_, index) => new Date(Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), today.getUTCDate() - index)));
    const candles = (await mapConcurrent(dates, (date) => fetchCandle(key, date))).filter((candle): candle is MarketCandle => candle !== null);
    if (!candles.length) throw new Error("수집된 KOSPI 일봉이 없습니다. API 활용신청과 키를 확인하세요.");
    const { error: upsertError } = await supabase.from("market_index_prices").upsert(candles.map((candle) => ({ index_code: "KOSPI", market_date: candle.date, open: candle.open, high: candle.high, low: candle.low, close: candle.close, volume: candle.volume, source: "data_go_kr", updated_at: new Date().toISOString() })), { onConflict: "index_code,market_date" });
    if (upsertError) throw upsertError;
    const { data, error: historyError } = await supabase.from("market_index_prices").select("market_date,open,high,low,close,volume").eq("index_code", "KOSPI").order("market_date", { ascending: false }).limit(100);
    if (historyError) throw historyError;
    const context = calculateMarketContext((data || []).map((row) => ({ date: row.market_date, open: Number(row.open), high: Number(row.high), low: Number(row.low), close: Number(row.close), volume: row.volume === null ? null : Number(row.volume) })));
    return json({ fetched: candles.length, mode: days === BOOTSTRAP_DAYS ? "bootstrap" : "refresh", market_context: context });
  } catch (error) { return json({ error: error instanceof Error ? error.message : String(error) }, 500); }
});
