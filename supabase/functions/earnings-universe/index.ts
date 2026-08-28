import "jsr:@supabase/functions-js/edge-runtime.d.ts";
import { createClient } from "https://esm.sh/@supabase/supabase-js@2";
import {
  fetchKoreanMarketCapUniverse,
  fetchNasdaqOperatingSymbols,
  fetchSecListedCompanies,
  fetchSp500Companies,
  normalizedUsTicker,
  type KoreanMarketCapRow,
  type UsListedCompany,
} from "../_shared/earnings-universe-sources.ts";
import {
  fetchKisOverseasMarketCapRanking,
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

type UsUniverseRow = KisMarketCapRow & { cik: string };

function kisExchangeName(exchange: string) {
  return exchange === "NAS" ? "NASDAQ" : exchange === "NYS" ? "NYSE" : "AMEX";
}

function deduplicateUsCompanies(rows: UsUniverseRow[], limit: number) {
  const byCik = new Map<string, UsUniverseRow>();
  for (const row of rows.sort((a, b) => b.marketCap - a.marketCap || a.ticker.localeCompare(b.ticker))) {
    if (!byCik.has(row.cik)) byCik.set(row.cik, row);
  }
  const result = [...byCik.values()].slice(0, limit)
    .map((row, index) => ({ ...row, rank: index + 1 }));
  if (result.length !== limit) throw new Error(`미국 시총 기업이 ${result.length}/${limit}개만 확인되었습니다.`);
  return result;
}

function attachUsIdentity(
  rankings: KisMarketCapRow[],
  companies: Map<string, UsListedCompany>,
) {
  return rankings.flatMap((row) => {
    const ticker = normalizedUsTicker(row.ticker);
    const company = companies.get(ticker);
    return company ? [{
      ...row,
      ticker,
      name: company.name || row.name,
      exchange: kisExchangeName(row.exchange),
      cik: company.cik,
    }] : [];
  });
}

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8" },
  });
}

function kstDate() {
  return new Date(Date.now() + 9 * 3_600_000).toISOString().slice(0, 10);
}

function snapshotRows(rows: KoreanMarketCapRow[]) {
  return rows.map((row) => ({
    ticker: row.ticker,
    name: row.name,
    rank: row.rank,
    market_cap: row.marketCap,
  }));
}

function usSnapshotRows(rows: UsUniverseRow[]) {
  return rows.map((row) => ({
    ticker: row.ticker,
    name: row.name,
    rank: row.rank,
    market_cap: row.marketCap,
    exchange: row.exchange,
    sec_cik: row.cik,
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
    const targets = new Set(requested === "ALL"
      ? ["ALL_KR", "ALL_US"]
      : [requested]);
    const domesticTargets = targets.has("ALL_KR")
      ? DOMESTIC_UNIVERSES
      : DOMESTIC_UNIVERSES.filter((item) => targets.has(item.indexId));
    const wantsSp = targets.has("ALL_US") || targets.has("SP100");
    const wantsNasdaq = targets.has("ALL_US") || targets.has("NASDAQ100");
    if (!domesticTargets.length && !wantsSp && !wantsNasdaq) {
      throw new Error(`지원하지 않는 시총 유니버스입니다: ${requested}`);
    }

    // Use the caller's server credential instead of comparing it with the Edge
    // runtime's possibly older key string. A service-role-only RPC proves the
    // caller's capability before any provider request or database mutation occurs.
    const admin = createClient(supabaseUrl, apiKey, {
      auth: { persistSession: false, autoRefreshToken: false },
      global: { headers: { Authorization: `Bearer ${bearer}` } },
    });
    const { data: authorized, error: authorizationError } = await admin.rpc("authorize_earnings_ingestion");
    if (authorizationError || authorized !== true) throw new Error("서비스 역할 요청만 허용됩니다.");
    const results: unknown[] = [];

    // Each provider result is validated to its exact target count before the
    // atomic database function is called. A short or malformed provider page can
    // therefore never evict valid members from yesterday's universe.
    for (const target of domesticTargets) {
      const ranking = await fetchKoreanMarketCapUniverse(
        target.market,
        target.limit,
      );
      const { data, error } = await admin.rpc("sync_earnings_market_cap_universe", {
        p_index_id: target.indexId,
        p_observed_on: observedOn,
        p_constituents: snapshotRows(ranking),
        p_source: "KIS downloadable market master",
        p_source_reference: `${target.market}:${observedOn}`,
      });
      if (error) throw new Error(`${target.indexId} 저장 실패: ${error.message}`);
      results.push(data);
    }


    if (wantsSp || wantsNasdaq) {
      const credentials = loadKisCredentials();
      const accessToken = await getKisAccessToken(credentials, admin);
      const secCompanies = await fetchSecListedCompanies();
      // These calls share the same KIS app-key quota. Keep exchanges serial so
      // the universe refresh cannot collide with itself at the per-second limit.
      const nasRankings = await fetchKisOverseasMarketCapRanking(
        credentials, accessToken, "NAS", 300,
      );
      await new Promise((resolve) => setTimeout(resolve, 500));
      const nysRankings = await fetchKisOverseasMarketCapRanking(
        credentials, accessToken, "NYS", 300,
      );
      await new Promise((resolve) => setTimeout(resolve, 500));
      const amsRankings = await fetchKisOverseasMarketCapRanking(
        credentials, accessToken, "AMS", 100,
      );

      if (wantsSp) {
        const spCompanies = await fetchSp500Companies();
        const ranking = deduplicateUsCompanies(attachUsIdentity(
          [...nasRankings, ...nysRankings, ...amsRankings], spCompanies,
        ), 100);
        const { data, error } = await admin.rpc("sync_earnings_market_cap_universe", {
          p_index_id: "SP100",
          p_observed_on: observedOn,
          p_constituents: usSnapshotRows(ranking),
          p_source: "S&P 500 constituents + KIS market cap",
          p_source_reference: `SP500+KIS:${observedOn}`,
        });
        if (error) throw new Error(`SP100 저장 실패: ${error.message}`);
        results.push(data);
      }

      if (wantsNasdaq) {
        const operatingSymbols = await fetchNasdaqOperatingSymbols();
        const eligibleCompanies = new Map(
          [...secCompanies].filter(([ticker]) => operatingSymbols.has(ticker)),
        );
        const ranking = deduplicateUsCompanies(
          attachUsIdentity(nasRankings, eligibleCompanies), 100,
        );
        const { data, error } = await admin.rpc("sync_earnings_market_cap_universe", {
          p_index_id: "NASDAQ100",
          p_observed_on: observedOn,
          p_constituents: usSnapshotRows(ranking),
          p_source: "Nasdaq listed non-ETF stocks + KIS market cap",
          p_source_reference: `NASDAQ+KIS:${observedOn}`,
        });
        if (error) throw new Error(`NASDAQ100 저장 실패: ${error.message}`);
        results.push(data);
      }
    }

    return json({ ok: true, observed_on: observedOn, universes: results });
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    return json({ ok: false, error: message }, message.includes("허용") ? 403 : 500);
  }
});
