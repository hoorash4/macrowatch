import { createClient } from "https://esm.sh/@supabase/supabase-js@2";
import { calculateMarketContext, type MarketContext } from "./market-indicators.ts";

export async function loadMarketContext() {
  const url = Deno.env.get("SUPABASE_URL"), key = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY");
  if (!url || !key) return { context: null as MarketContext | null, warning: "시장 데이터 서버 설정이 없습니다." };
  const supabase = createClient(url, key);
  const { data, error } = await supabase.from("market_index_prices").select("market_date,open,high,low,close,volume").eq("index_code", "KOSPI").order("market_date", { ascending: false }).limit(100);
  if (error) return { context: null as MarketContext | null, warning: `시장 데이터 조회 실패: ${error.message}` };
  const context = calculateMarketContext((data || []).map((row) => ({ date: row.market_date, open: Number(row.open), high: Number(row.high), low: Number(row.low), close: Number(row.close), volume: row.volume === null ? null : Number(row.volume) })));
  return { context, warning: context ? null : "시장 지표 계산에 필요한 일봉 수가 아직 부족합니다." };
}
