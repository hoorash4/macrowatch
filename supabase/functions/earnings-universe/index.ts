import "jsr:@supabase/functions-js/edge-runtime.d.ts";
import { createClient } from "https://esm.sh/@supabase/supabase-js@2";
import {
  fetchKisDomesticMarketCapRanking,
  getKisAccessToken,
  loadKisCredentials,
  type KisMarketCapRow,
} from "../_shared/kis-client.ts";

type DomesticUniverse = {
  indexId: "KOSPI100" | "KOSDAQ50";
  market: "KOSPI" | "KOSDAQ";
  limit: number;
};

const DOMESTIC_UNIVERSES: DomesticUniverse[] = [
  { indexId: "KOSPI100", market: "KOSPI", limit: 100 },
  { indexId: "KOSDAQ50", market: "KOSDAQ", limit: 50 },
];

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8" },
  });
}

function kstDate() {
  return new Date(Date.now() + 9 * 3_600_000).toISOString().slice(0, 10);
}

function snapshotRows(rows: KisMarketCapRow[]) {
  return rows.map((row) => ({
    ticker: row.ticker,
    name: row.name,
    rank: row.rank,
    market_cap: row.marketCap,
  }));
}

Deno.serve(async (request) => {
  if (request.method !== "POST") return json({ error: "POST 요청만 허용됩니다." }, 405);
  try {
    const supabaseUrl = Deno.env.get("SUPABASE_URL")?.trim() || "";
    if (!supabaseUrl) throw new Error("Supabase 서버 환경변수가 없습니다.");
    const bearer = (request.headers.get("Authorization") || "").replace(/^Bearer\s+/i, "");
    const apiKey = request.headers.get("apikey")?.trim() || "";
    if (!bearer || !apiKey) throw new Error("서비스 역할 요청만 허용됩니다.");

    const body = await request.json().catch(() => ({})) as Record<string, unknown>;
    const observedOn = typeof body.observed_on === "string" && /^\d{4}-\d{2}-\d{2}$/.test(body.observed_on)
      ? body.observed_on
      : kstDate();
    const requested = typeof body.index_id === "string" ? body.index_id : "ALL_KR";
    const targets = requested === "ALL_KR"
      ? DOMESTIC_UNIVERSES
      : DOMESTIC_UNIVERSES.filter((item) => item.indexId === requested);
    if (!targets.length) throw new Error(`지원하지 않는 한국 시총 유니버스입니다: ${requested}`);

    // Use the caller's server credential instead of comparing it with the Edge
    // runtime's possibly older key string. A service-role-only RPC proves the
    // caller's capability before any KIS request or database mutation occurs.
    const admin = createClient(supabaseUrl, apiKey, {
      auth: { persistSession: false, autoRefreshToken: false },
      global: { headers: { Authorization: `Bearer ${bearer}` } },
    });
    const { data: authorized, error: authorizationError } = await admin.rpc("authorize_earnings_ingestion");
    if (authorizationError || authorized !== true) throw new Error("서비스 역할 요청만 허용됩니다.");
    const credentials = loadKisCredentials();
    const accessToken = await getKisAccessToken(credentials, admin);
    const results: unknown[] = [];

    // Each provider result is validated to its exact target count before the
    // atomic database function is called. A short or duplicated KIS page can
    // therefore never evict valid members from yesterday's universe.
    for (const target of targets) {
      const ranking = await fetchKisDomesticMarketCapRanking(
        credentials,
        accessToken,
        target.market,
        target.limit,
      );
      const { data, error } = await admin.rpc("sync_earnings_market_cap_universe", {
        p_index_id: target.indexId,
        p_observed_on: observedOn,
        p_constituents: snapshotRows(ranking),
        p_source: "KIS domestic market-cap ranking",
        p_source_reference: `${target.market}:${observedOn}`,
      });
      if (error) throw new Error(`${target.indexId} 저장 실패: ${error.message}`);
      results.push(data);
    }

    return json({ ok: true, observed_on: observedOn, universes: results });
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    return json({ ok: false, error: message }, message.includes("허용") ? 403 : 500);
  }
});
