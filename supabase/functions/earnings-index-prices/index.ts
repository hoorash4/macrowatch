import "jsr:@supabase/functions-js/edge-runtime.d.ts";
import { createClient } from "https://esm.sh/@supabase/supabase-js@2";
import {
  createKisRequestRunner,
  fetchKisDomesticIndexPrices,
  getKisAccessToken,
  loadKisCredentials,
  type KisIndexPrice,
} from "../_shared/kis-client.ts";

type IndexObservation = KisIndexPrice & { indexCode: string; source: string };

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), {
  status,
  headers: { "Content-Type": "application/json; charset=utf-8" },
});

function requiredSecret(name: string) {
  const value = Deno.env.get(name)?.trim();
  if (!value) throw new Error(`${name}가 설정되지 않았습니다.`);
  return value;
}

function latestCompletedQuarterEnd(now = new Date()) {
  const year = now.getUTCFullYear(), month = now.getUTCMonth() + 1;
  const completedMonth = Math.floor((month - 1) / 3) * 3;
  if (completedMonth === 0) return new Date(Date.UTC(year - 1, 11, 31));
  return new Date(Date.UTC(year, completedMonth, 0));
}

function selectQuarterEnds(rows: IndexObservation[], completedThrough: string) {
  const selected = new Map<string, IndexObservation>();
  rows.forEach((row) => {
    if (row.marketDate > completedThrough) return;
    const month = Number(row.marketDate.slice(5, 7));
    if (![3, 6, 9, 12].includes(month)) return;
    const key = `${row.indexCode}:${row.marketDate.slice(0, 4)}Q${month / 3}`;
    const previous = selected.get(key);
    if (!previous || row.marketDate > previous.marketDate) selected.set(key, row);
  });
  return [...selected.values()].sort((a, b) =>
    a.indexCode.localeCompare(b.indexCode) || a.marketDate.localeCompare(b.marketDate)
  );
}

async function fetchFredDaily(seriesId: string, apiKey: string, start: string, end: string) {
  const url = new URL("https://api.stlouisfed.org/fred/series/observations");
  url.searchParams.set("series_id", seriesId);
  url.searchParams.set("api_key", apiKey);
  url.searchParams.set("file_type", "json");
  url.searchParams.set("observation_start", start);
  url.searchParams.set("observation_end", end);
  url.searchParams.set("limit", "100000");
  const response = await fetch(url, { signal: AbortSignal.timeout(45_000) });
  const payload = await response.json();
  if (!response.ok) throw new Error(`FRED ${seriesId} 조회 실패 (${response.status})`);
  return (Array.isArray(payload.observations) ? payload.observations : []).flatMap((raw: Record<string, unknown>) => {
    const marketDate = String(raw.date || ""), close = Number(raw.value);
    return /^\d{4}-\d{2}-\d{2}$/.test(marketDate) && Number.isFinite(close) && close > 0
      ? [{ marketDate, open: null, high: null, low: null, close, volume: null } satisfies KisIndexPrice]
      : [];
  });
}

async function fetchKoreanHistory(
  admin: ReturnType<typeof createClient>,
  startYear: number,
  end: Date,
) {
  const credentials = loadKisCredentials();
  const token = await getKisAccessToken(credentials, admin);
  const runRequest = createKisRequestRunner();
  const definitions = [
    { indexCode: "KOSPI200", kisCode: "2001" },
    { indexCode: "KOSDAQ150", kisCode: "2203" },
  ];
  const observations: IndexObservation[] = [];
  for (const definition of definitions) {
    // KIS 한 응답의 개수 제한을 넘지 않도록 5년 단위 월봉으로 나눈다.
    for (let chunkStart = startYear; chunkStart <= end.getUTCFullYear(); chunkStart += 5) {
      const chunkEnd = Math.min(chunkStart + 4, end.getUTCFullYear());
      const chunkEndDate = chunkEnd === end.getUTCFullYear()
        ? end
        : new Date(Date.UTC(chunkEnd, 11, 31));
      const rows = await fetchKisDomesticIndexPrices(
        credentials,
        token,
        definition.kisCode,
        new Date(Date.UTC(chunkStart, 0, 1)),
        chunkEndDate,
        "M",
        runRequest,
      );
      observations.push(...rows.map((row) => ({ ...row, indexCode: definition.indexCode, source: "kis_open_api" })));
    }
  }
  return observations;
}

Deno.serve(async (request) => {
  if (request.method !== "POST") return json({ error: "POST 요청만 허용됩니다." }, 405);
  try {
    const body = await request.json().catch(() => ({})) as Record<string, unknown>;
    const currentYear = new Date().getUTCFullYear();
    const requestedStart = Number(body.start_year ?? currentYear - 10);
    if (!Number.isInteger(requestedStart) || requestedStart < 1990 || requestedStart > currentYear) {
      return json({ error: "start_year가 올바르지 않습니다." }, 400);
    }
    const startYear = requestedStart;
    const completedEnd = latestCompletedQuarterEnd();
    const completedThrough = completedEnd.toISOString().slice(0, 10);
    const start = `${startYear}-01-01`;
    const supabaseUrl = requiredSecret("SUPABASE_URL");
    const serviceRole = requiredSecret("SUPABASE_SERVICE_ROLE_KEY");
    const admin = createClient(supabaseUrl, serviceRole);

    const observations = await fetchKoreanHistory(admin, startYear, completedEnd);
    const fredKey = requiredSecret("FRED_API_KEY");
    for (const definition of [
      { indexCode: "NASDAQ100", seriesId: "NASDAQ100" },
      { indexCode: "SP500", seriesId: "SP500" },
    ]) {
      const rows = await fetchFredDaily(definition.seriesId, fredKey, start, completedThrough);
      observations.push(...rows.map((row) => ({ ...row, indexCode: definition.indexCode, source: "fred" })));
    }

    const quarterEnds = selectQuarterEnds(observations, completedThrough);
    const updatedAt = new Date().toISOString();
    const { error } = await admin.from("market_index_prices").upsert(quarterEnds.map((row) => ({
      index_code: row.indexCode,
      market_date: row.marketDate,
      open: row.open,
      high: row.high,
      low: row.low,
      close: row.close,
      volume: row.volume,
      source: row.source,
      is_quarter_end: true,
      updated_at: updatedAt,
    })), { onConflict: "index_code,market_date" });
    if (error) throw error;

    const counts = Object.fromEntries(["KOSPI200", "KOSDAQ150", "NASDAQ100", "SP500"].map((code) => [
      code,
      quarterEnds.filter((row) => row.indexCode === code).length,
    ]));
    return json({ ok: true, start_year: startYear, completed_through: completedThrough, stored: quarterEnds.length, counts });
  } catch (error) {
    return json({ error: error instanceof Error ? error.message : String(error) }, 500);
  }
});
