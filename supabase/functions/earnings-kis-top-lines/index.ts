import "jsr:@supabase/functions-js/edge-runtime.d.ts";
import { createClient } from "https://esm.sh/@supabase/supabase-js@2";
import {
  createKisRequestRunner,
  fetchKisDomesticQuarterTopLine,
  getKisAccessToken,
  loadKisCredentials,
} from "../_shared/kis-client.ts";
import { isTrustedServerRequest } from "../_shared/server-key-auth.ts";

const HUNDRED_MILLION_KRW = 100_000_000;
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), {
  status,
  headers: { "Content-Type": "application/json; charset=utf-8" },
});

function requiredSecret(name: string) {
  const value = Deno.env.get(name)?.trim();
  if (!value) throw new Error(`${name}가 설정되지 않았습니다.`);
  return value;
}

Deno.serve(async (request) => {
  if (request.method !== "POST") return json({ error: "POST 요청만 허용됩니다." }, 405);
  try {
    const serviceRole = requiredSecret("SUPABASE_SERVICE_ROLE_KEY");
    if (!isTrustedServerRequest(request)) {
      return json({ error: "서비스 역할 호출만 허용됩니다." }, 403);
    }
    const body = await request.json().catch(() => ({})) as Record<string, unknown>;
    const year = Number(body.year), quarter = Number(body.quarter);
    const tickers = Array.isArray(body.tickers)
      ? [...new Set(body.tickers.map((value) => String(value).trim()))]
      : [];
    if (!Number.isInteger(year) || year < 2018 || year > 2200) return json({ error: "사업연도가 올바르지 않습니다." }, 400);
    if (![1, 2, 3, 4].includes(quarter)) return json({ error: "분기는 1~4여야 합니다." }, 400);
    if (!tickers.length || tickers.length > 100 || tickers.some((ticker) => !/^\d{6}$/.test(ticker))) {
      return json({ error: "종목코드는 숫자 6자리이며 한 번에 100개까지 허용됩니다." }, 400);
    }

    const supabaseUrl = requiredSecret("SUPABASE_URL");
    const admin = createClient(supabaseUrl, serviceRole);
    const credentials = loadKisCredentials();
    const token = await getKisAccessToken(credentials, admin);
    const runRequest = createKisRequestRunner();
    const values: Record<string, { top_line: string; source: string }> = {};
    const errors: Record<string, string> = {};
    for (const ticker of tickers) {
      try {
        const period = await fetchKisDomesticQuarterTopLine(
          credentials,
          token,
          ticker,
          year,
          quarter as 1 | 2 | 3 | 4,
          runRequest,
        );
        if (!period) {
          errors[ticker] = "KIS 분기 매출액이 제공되지 않습니다.";
          continue;
        }
        values[ticker] = {
          top_line: String(Math.round(period.topLineHundredMillionKrw * HUNDRED_MILLION_KRW)),
          source: "kis_income_statement",
        };
      } catch (error) {
        errors[ticker] = (error instanceof Error ? error.message : String(error)).slice(0, 240);
      }
    }
    return json({ values, errors, requested: tickers.length, resolved: Object.keys(values).length });
  } catch (error) {
    return json({ error: error instanceof Error ? error.message : String(error) }, 500);
  }
});
