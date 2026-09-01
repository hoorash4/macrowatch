import "jsr:@supabase/functions-js/edge-runtime.d.ts";
import { createClient } from "https://esm.sh/@supabase/supabase-js@2";
import { fetchKisDailyPrices, fetchKisFinanceDiagnostic, getKisAccessToken, loadKisCredentials } from "../_shared/kis-client.ts";

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8" },
  });
}

Deno.serve(async (request) => {
  if (request.method !== "POST") return json({ error: "POST 요청만 허용됩니다." }, 405);
  try {
    // 배포 기본값인 Supabase 게이트웨이 JWT 검증을 통과한 서버 요청만 여기까지 도달한다.
    const supabaseUrl = Deno.env.get("SUPABASE_URL"), serviceRole = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY");
    if (!supabaseUrl || !serviceRole) throw new Error("Supabase 서버 설정이 없습니다.");
    const admin = createClient(supabaseUrl, serviceRole);
    const credentials = loadKisCredentials();
    const token = await getKisAccessToken(credentials, admin);
    const body = await request.json().catch(() => ({})) as Record<string, unknown>;
    if (body.mode === "finance") {
      const ticker = String(body.ticker || "105560").trim();
      const results = [];
      for (const endpoint of ["income_statement", "profit_ratio"] as const) {
        for (const period of ["annual", "quarterly"] as const) {
          results.push(await fetchKisFinanceDiagnostic(credentials, token, ticker, endpoint, period));
        }
      }
      return json({ ok: true, ticker, results });
    }
    const end = new Date();
    const start = new Date(end.getTime() - 14 * 86_400_000);
    const prices = await fetchKisDailyPrices(credentials, token, "069500", start, end);
    if (!prices.length) throw new Error("KODEX 200의 최근 일봉이 반환되지 않았습니다.");
    const latest = prices.sort((a, b) => b.marketDate.localeCompare(a.marketDate))[0];
    // Credentials and access tokens must never be included in this response.
    return json({ ok: true, ticker: "069500", name: "KODEX 200", observations: prices.length, latest });
  } catch (error) {
    return json({ ok: false, error: error instanceof Error ? error.message : String(error) }, 500);
  }
});
