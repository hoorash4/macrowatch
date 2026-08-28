import "jsr:@supabase/functions-js/edge-runtime.d.ts";
import { createClient } from "https://esm.sh/@supabase/supabase-js@2";
import {
  createKisRequestRunner,
  fetchKisDomesticAdjustedMonthlyPrices,
  fetchKisOverseasAdjustedMonthlyPrices,
  getKisAccessToken,
  loadKisCredentials,
  type KisQuarterlyPriceSource,
} from "../_shared/kis-client.ts";

type PriceCompany = {
  company_id: string;
  country: "KR" | "US";
  ticker: string;
  exchange: string | null;
  currency: string;
};

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), {
  status, headers: { "Content-Type": "application/json; charset=utf-8" },
});

function requiredSecret(name: string) {
  const value = Deno.env.get(name)?.trim();
  if (!value) throw new Error(`${name}가 설정되지 않았습니다.`);
  return value;
}

function completedQuarterEnd(now = new Date()) {
  const year = now.getUTCFullYear(), month = now.getUTCMonth() + 1;
  const completedMonth = Math.floor((month - 1) / 3) * 3;
  return completedMonth === 0
    ? new Date(Date.UTC(year - 1, 11, 31))
    : new Date(Date.UTC(year, completedMonth, 0));
}

function quarterRows(rows: KisQuarterlyPriceSource[], end: Date) {
  const endText = end.toISOString().slice(0, 10);
  const selected = new Map<string, KisQuarterlyPriceSource>();
  rows.forEach((row) => {
    if (row.marketDate > endText) return;
    const year = Number(row.marketDate.slice(0, 4));
    const month = Number(row.marketDate.slice(5, 7));
    const quarter = Math.floor((month - 1) / 3) + 1;
    const key = `${year}Q${quarter}`;
    const prior = selected.get(key);
    if (!prior || row.marketDate > prior.marketDate) selected.set(key, row);
  });
  return [...selected.entries()].map(([key, row]) => ({
    marketYear: Number(key.slice(0, 4)), marketQuarter: Number(key.slice(5)), ...row,
  })).sort((a, b) => a.marketDate.localeCompare(b.marketDate));
}

function exchangeCandidates(value: string | null) {
  const exchange = String(value || "").toUpperCase();
  const preferred = exchange.includes("NAS") ? "NAS"
    : exchange.includes("NYS") || exchange.includes("NYSE") ? "NYS"
    : exchange.includes("AMS") || exchange.includes("AMEX") ? "AMS" : null;
  const all = ["NAS", "NYS", "AMS"] as const;
  return preferred ? [preferred, ...all.filter((item) => item !== preferred)] : [...all];
}

Deno.serve(async (request) => {
  if (request.method !== "POST") return json({ error: "POST 요청만 허용됩니다." }, 405);
  try {
    const input = await request.json().catch(() => ({})) as Record<string, unknown>;
    const offset = Math.max(0, Number(input.offset ?? 0));
    const limit = Math.min(10, Math.max(1, Number(input.limit ?? 5)));
    const currentYear = new Date().getUTCFullYear();
    const startYear = Number(input.start_year ?? currentYear - 10);
    if (!Number.isInteger(offset) || !Number.isInteger(limit) || !Number.isInteger(startYear)) {
      return json({ error: "offset, limit 또는 start_year가 올바르지 않습니다." }, 400);
    }

    const admin = createClient(requiredSecret("SUPABASE_URL"), requiredSecret("SUPABASE_SERVICE_ROLE_KEY"));
    const { data, error } = await admin.rpc("list_current_earnings_price_companies");
    if (error) throw error;
    const companies = (Array.isArray(data) ? data : []) as PriceCompany[];
    const batch = companies.slice(offset, offset + limit);
    const credentials = loadKisCredentials();
    const token = await getKisAccessToken(credentials, admin);
    const runRequest = createKisRequestRunner();
    const end = completedQuarterEnd();
    const start = new Date(Date.UTC(startYear, 0, 1));
    const stored: Record<string, number> = {};
    const failures: Array<{ company_id: string; ticker: string; error: string }> = [];

    for (const company of batch) {
      try {
        let sourceRows: KisQuarterlyPriceSource[] = [];
        let source = "kis_open_api_domestic_adjusted";
        if (company.country === "KR") {
          // Five-year chunks stay below KIS monthly-bar response limits.
          for (let year = startYear; year <= end.getUTCFullYear(); year += 5) {
            const chunkEndYear = Math.min(year + 4, end.getUTCFullYear());
            const chunkEnd = chunkEndYear === end.getUTCFullYear()
              ? end : new Date(Date.UTC(chunkEndYear, 11, 31));
            sourceRows.push(...await fetchKisDomesticAdjustedMonthlyPrices(
              credentials, token, company.ticker,
              new Date(Date.UTC(year, 0, 1)), chunkEnd, runRequest,
            ));
          }
        } else {
          source = "kis_open_api_overseas_adjusted";
          let lastError: unknown = null;
          for (const exchange of exchangeCandidates(company.exchange)) {
            try {
              sourceRows = await fetchKisOverseasAdjustedMonthlyPrices(
                credentials, token, company.ticker, exchange, start, end, runRequest,
              );
              if (sourceRows.length) break;
            } catch (error) { lastError = error; }
          }
          if (!sourceRows.length && lastError) throw lastError;
        }
        const rows = quarterRows(sourceRows, end).map((row) => ({
          company_id: company.company_id,
          market_year: row.marketYear,
          market_quarter: row.marketQuarter,
          price_date: row.marketDate,
          adjusted_close: row.close,
          currency: company.currency,
          source,
          updated_at: new Date().toISOString(),
        }));
        if (!rows.length) throw new Error("수정주가 응답에 유효한 분기 가격이 없습니다.");
        const { error: upsertError } = await admin.from("earnings_company_quarterly_prices")
          .upsert(rows, { onConflict: "company_id,market_year,market_quarter" });
        if (upsertError) throw upsertError;
        stored[company.company_id] = rows.length;
      } catch (error) {
        failures.push({
          company_id: company.company_id, ticker: company.ticker,
          error: error instanceof Error ? error.message.slice(0, 240) : String(error).slice(0, 240),
        });
      }
    }
    return json({
      ok: failures.length === 0,
      offset, processed: batch.length, total: companies.length,
      next_offset: offset + batch.length < companies.length ? offset + batch.length : null,
      stored, failures,
    });
  } catch (error) {
    return json({ error: error instanceof Error ? error.message : String(error) }, 500);
  }
});
