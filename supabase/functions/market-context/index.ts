import "jsr:@supabase/functions-js/edge-runtime.d.ts";
import { jsonResponse as json } from "../_shared/http.ts";
import { createClient } from "https://esm.sh/@supabase/supabase-js@2";
import { calculateMarketContext, type MarketCandle } from "../_shared/market/market-indicators.ts";

Deno.serve(async (request) => {
  if (request.method !== "POST") return json({ error: "POST 요청만 허용됩니다." }, 405);
  try {
    const url = Deno.env.get("SUPABASE_URL");
    const serviceRole = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY");
    if (!url || !serviceRole) throw new Error("Supabase 서버 설정이 없습니다.");

    const supabase = createClient(url, serviceRole);
    const { data, error } = await supabase
      .from("market_index_prices")
      .select("market_date,open,high,low,close,volume")
      .eq("index_code", "KOSPI")
      .order("market_date", { ascending: false })
      .limit(100);
    if (error) throw error;
    if (!data?.length) throw new Error("저장된 KOSPI 일봉이 없습니다.");

    const history: MarketCandle[] = data.map((row) => ({
      date: String(row.market_date),
      open: Number(row.open),
      high: Number(row.high),
      low: Number(row.low),
      close: Number(row.close),
      volume: row.volume === null ? null : Number(row.volume),
    }));
    const context = calculateMarketContext(history);
    return json({
      source: "market_index_prices:KOSPI",
      observations: history.length,
      market_context: context,
    });
  } catch (error) {
    return json({ error: error instanceof Error ? error.message : String(error) }, 500);
  }
});
