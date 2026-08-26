import "jsr:@supabase/functions-js/edge-runtime.d.ts";
import { fetchKisDailyPrices, issueKisAccessToken, loadKisCredentials } from "../_shared/kis-client.ts";

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8" },
  });
}

Deno.serve(async (request) => {
  if (request.method !== "POST") return json({ error: "POST 요청만 허용됩니다." }, 405);
  try {
    const credentials = loadKisCredentials();
    const token = await issueKisAccessToken(credentials);
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
