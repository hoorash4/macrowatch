import "jsr:@supabase/functions-js/edge-runtime.d.ts";
import { createClient } from "https://esm.sh/@supabase/supabase-js@2";
import { listPolicyReviews, resolvePolicyReview } from "../_shared/policy-admin.ts";
import { fetchKisDailyPriceBundle, fetchKisEtfTopHoldings, getKisAccessToken, loadKisCredentials } from "../_shared/kis-client.ts";

const ALLOWED_ORIGIN = "https://hoorash4.github.io";
const REPOSITORY = "hoorash4/macrowatch";
const BRANCH = "main";
const CHECK_WORKFLOW = "check-targets.yml";
const BACKUP_WORKFLOW = "backup-database.yml";
const NEWS_WORKFLOW = "news-pipeline.yml";

function corsHeaders(origin: string | null) {
  return {
    "Access-Control-Allow-Origin": origin === ALLOWED_ORIGIN ? origin : ALLOWED_ORIGIN,
    "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Vary": "Origin",
  };
}

function json(body: unknown, status: number, origin: string | null) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { ...corsHeaders(origin), "Content-Type": "application/json; charset=utf-8" },
  });
}

function githubHeaders(token: string) {
  return {
    "Authorization": `Bearer ${token}`,
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    "Content-Type": "application/json",
  };
}

async function githubRequest(path: string, token: string, init: RequestInit = {}) {
  const response = await fetch(`https://api.github.com/repos/${REPOSITORY}${path}`, {
    ...init,
    headers: { ...githubHeaders(token), ...(init.headers || {}) },
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(error.message || `GitHub 요청 실패 (${response.status})`);
  }
  if (response.status === 204) return null;
  return response.json();
}

async function latestRun(workflow: string, token: string) {
  const data = await githubRequest(
    `/actions/workflows/${workflow}/runs?per_page=20`,
    token,
  );
  // Ordinary site pushes intentionally skip this workflow. They are not failed
  // news runs and must not replace the latest dispatched or scheduled result.
  const run = data?.workflow_runs?.find((item: { conclusion?: string | null }) => item.conclusion !== "skipped");
  if (!run) return null;
  return {
    id: run.id,
    status: run.status,
    conclusion: run.conclusion,
    created_at: run.created_at,
    run_started_at: run.run_started_at,
    updated_at: run.updated_at,
    html_url: run.html_url,
  };
}

async function authenticatedUser(supabaseUrl: string, anonKey: string, jwt: string) {
  const response = await fetch(`${supabaseUrl}/auth/v1/user`, {
    headers: { apikey: anonKey, Authorization: `Bearer ${jwt}` },
  });
  if (!response.ok) return null;
  const user = await response.json().catch(() => null);
  return user?.id ? user : null;
}

function validateTimes(value: unknown) {
  if (!Array.isArray(value) || value.length < 1 || value.length > 4) {
    throw new Error("확인 시간은 하루 1회부터 4회까지 설정할 수 있습니다.");
  }
  const times = value.map((item) => String(item));
  if (times.some((item) => !/^([01]\d|2[0-3]):[0-5]\d$/.test(item))) {
    throw new Error("시간 형식이 올바르지 않습니다.");
  }
  if (new Set(times).size !== times.length) {
    throw new Error("서로 다른 시간을 입력해 주세요.");
  }
  return times.sort();
}

function requiredText(value: unknown, label: string, maxLength: number) {
  const text = String(value || "").trim();
  if (!text || text.length > maxLength) {
    throw new Error(`${label}을(를) ${maxLength}자 이내로 입력해 주세요.`);
  }
  return text;
}

function validateUsername(value: unknown) {
  const username = String(value || "").trim().toLowerCase();
  if (!/^[a-z0-9._-]{4,32}$/.test(username)) {
    throw new Error("아이디는 영문 소문자, 숫자, 마침표, 밑줄, 하이픈으로 4~32자여야 합니다.");
  }
  return username;
}

function validatePassword(value: unknown) {
  const password = String(value || "");
  if (password.length < 6 || password.length > 72) {
    throw new Error("비밀번호는 6~72자로 입력해 주세요.");
  }
  return password;
}

function internalEmail(username: string) {
  return `id-${username}@users.macrowatch.invalid`;
}

function validateSectorEtf(body: Record<string, unknown>) {
  return {
    sector_name: requiredText(body.sector_name, "섹터명", 80),
    etf_name: requiredText(body.etf_name, "ETF명", 120),
    etf_ticker: requiredText(body.etf_ticker, "ETF 코드", 24).toUpperCase(),
    issuer: requiredText(body.issuer, "운용사", 80),
    is_active: true,
  };
}

function validateNewSectorEtf(body: Record<string, unknown>) {
  const ticker = requiredText(body.etf_ticker, "ETF 코드", 6);
  if (!/^\d{6}$/.test(ticker)) throw new Error("ETF 코드는 6자리 숫자여야 합니다.");
  return { sector_name: requiredText(body.sector_name, "섹터명", 80), etf_ticker: ticker };
}

function issuerFromEtfName(name: string) {
  const brands: Array<[string, string]> = [
    ["KODEX", "삼성자산운용"], ["TIGER", "미래에셋자산운용"], ["RISE", "KB자산운용"],
    ["ACE", "한국투자신탁운용"], ["PLUS", "한화자산운용"], ["HANARO", "NH-Amundi자산운용"],
    ["SOL", "신한자산운용"], ["KOSEF", "키움투자자산운용"], ["KIWOOM", "키움투자자산운용"],
    ["TIMEFOLIO", "타임폴리오자산운용"], ["BNK", "BNK자산운용"], ["1Q", "하나자산운용"],
  ];
  const matched = brands.find(([brand]) => name.toUpperCase().startsWith(`${brand} `) || name.toUpperCase() === brand);
  if (!matched) throw new Error(`ETF명에서 운용사를 자동 확인하지 못했습니다: ${name}`);
  return matched[1];
}

async function rebuildSectorRankings(supabaseUrl: string, serviceRoleKey: string) {
  const response = await fetch(`${supabaseUrl}/functions/v1/sector-flow`, {
    method: "POST",
    headers: { apikey: serviceRoleKey, Authorization: `Bearer ${serviceRoleKey}`, "Content-Type": "application/json" },
    body: JSON.stringify({ stage: "close", rebuild_only: true }),
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok || payload?.ok !== true) throw new Error(payload?.error || "섹터 순위 재계산에 실패했습니다.");
}

function validateExtremeNewsRule(body: Record<string, unknown>) {
  return { signal: "decisive", phrase: requiredText(body?.phrase, "기준 문장", 300), is_active: true };
}

function kstTimeToCron(time: string) {
  const [hour, minute] = time.split(":").map(Number);
  const utcMinutes = (hour * 60 + minute - 9 * 60 + 24 * 60) % (24 * 60);
  return `${utcMinutes % 60} ${Math.floor(utcMinutes / 60)} * * *`;
}

function encodeBase64(value: string) {
  const bytes = new TextEncoder().encode(value);
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary);
}

async function refreshArticleSentiment(admin: any, articleDate: string) {
  const { data, error } = await admin.from("news_article_sentiments")
    .select("ai_sentiment,admin_sentiment").eq("article_date", articleDate);
  if (error) throw error;
  const counts = { positive: 0, negative: 0, neutral: 0, uncertain: 0 };
  for (const row of data || []) counts[(row.admin_sentiment || row.ai_sentiment) as keyof typeof counts] += 1;
  const { error: upsertError } = await admin.from("news_daily_article_sentiment").upsert({
    article_date: articleDate, positive_count: counts.positive, negative_count: counts.negative,
    neutral_count: counts.neutral, uncertain_count: counts.uncertain,
    analyzed_article_count: (data || []).length, generated_at: new Date().toISOString(),
  });
  if (upsertError) throw upsertError;
}

async function excludeUncertainArticle(admin: any, id: string) {
  const { data, error } = await admin.from("news_article_sentiments")
    .delete()
    .eq("id", id).eq("ai_sentiment", "uncertain").is("admin_sentiment", null)
    .select("article_date").maybeSingle();
  if (error) throw error;
  if (!data) throw new Error("이미 처리되었거나 존재하지 않는 항목입니다.");

  const { data: daily, error: dailyError } = await admin.from("news_daily_article_sentiment")
    .select("excluded_count").eq("article_date", data.article_date).maybeSingle();
  if (dailyError) throw dailyError;

  await refreshArticleSentiment(admin, data.article_date);
  const { error: excludedError } = await admin.from("news_daily_article_sentiment").update({
    excluded_count: (daily?.excluded_count || 0) + 1,
    generated_at: new Date().toISOString(),
  }).eq("article_date", data.article_date);
  if (excludedError) throw excludedError;
}

async function updateWorkflowSchedule(times: string[], token: string) {
  const path = "/contents/.github/workflows/check-targets.yml";
  const file = await githubRequest(`${path}?ref=${BRANCH}`, token);
  const current = atob(String(file.content || "").replace(/\s/g, ""));
  const cronLines = times.map((time) => `    - cron: "${kstTimeToCron(time)}"`).join("\n");
  const next = current.replace(
    /  schedule:\r?\n[\s\S]*?  workflow_dispatch:/,
    `  schedule:\n    # GitHub Actions cron uses UTC. Managed from MacroWatch admin.\n${cronLines}\n  workflow_dispatch:`,
  );
  if (next === current) return;

  await githubRequest(path, token, {
    method: "PUT",
    body: JSON.stringify({
      message: `Update target check schedule to ${times.join(", ")} KST`,
      content: encodeBase64(next),
      sha: file.sha,
      branch: BRANCH,
    }),
  });
}

export default {
  async fetch(request: Request) {
    const origin = request.headers.get("Origin");
    if (request.method === "OPTIONS") return new Response("ok", { headers: corsHeaders(origin) });
    if (request.method !== "POST") return json({ error: "Method not allowed" }, 405, origin);

    try {
      const jwt = (request.headers.get("Authorization") || "").replace(/^Bearer\s+/i, "");
      if (!jwt) return json({ error: "로그인이 필요합니다." }, 401, origin);

      const supabaseUrl = Deno.env.get("SUPABASE_URL")!;
      const serviceRoleKey = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!;
      const anonKey = Deno.env.get("SUPABASE_ANON_KEY")!;
      const githubToken = Deno.env.get("GITHUB_ADMIN_TOKEN")!;
      if (!githubToken) return json({ error: "GitHub 관리자 토큰이 없습니다." }, 500, origin);

      const admin = createClient(supabaseUrl, serviceRoleKey, {
        auth: { persistSession: false, autoRefreshToken: false },
      });
      const requestApiKey = request.headers.get("apikey") || anonKey;
      const user = await authenticatedUser(supabaseUrl, requestApiKey, jwt);
      if (!user) {
        return json({ error: "로그인 정보가 유효하지 않습니다." }, 401, origin);
      }
      const { data: account } = await admin
        .from("user_accounts")
        .select("is_admin")
        .eq("user_id", user.id)
        .maybeSingle();
      if (account?.is_admin !== true) {
        return json({ error: "관리자 권한이 필요합니다." }, 403, origin);
      }

      const body = await request.json();
      const action = String(body?.action || "");

      if (action === "list_members") {
        const { data, error } = await admin.from("user_accounts")
          .select("user_id,username,kakao_user_id,is_admin,created_at,updated_at")
          .order("created_at", { ascending: true });
        if (error) throw error;
        return json({ items: (data || []).map((item) => ({
          ...item,
          kakao_connected: Boolean(item.kakao_user_id),
          kakao_user_id: undefined,
          is_current: item.user_id === user.id,
        })) }, 200, origin);
      }

      if (action === "create_member") {
        const username = validateUsername(body?.username);
        const password = validatePassword(body?.password);
        const { data: created, error: createError } = await admin.auth.admin.createUser({
          email: internalEmail(username), password, email_confirm: true,
          user_metadata: { auth_provider: "password", username },
        });
        if (createError || !created.user) throw createError || new Error("회원을 만들지 못했습니다.");
        const { error: accountError } = await admin.from("user_accounts").insert({
          user_id: created.user.id, username, is_admin: body?.is_admin === true,
        });
        if (accountError) {
          await admin.auth.admin.deleteUser(created.user.id);
          throw accountError;
        }
        return json({ created: true }, 201, origin);
      }

      if (action === "update_member") {
        const memberId = String(body?.user_id || "");
        if (!memberId) return json({ error: "회원 식별자가 필요합니다." }, 400, origin);
        const username = validateUsername(body?.username);
        const passwordChanged = Boolean(String(body?.password || ""));
        const authValues: Record<string, unknown> = {
          email: internalEmail(username), email_confirm: true,
          user_metadata: { auth_provider: "password", username },
        };
        if (passwordChanged) authValues.password = validatePassword(body.password);
        const { error: authError } = await admin.auth.admin.updateUserById(memberId, authValues);
        if (authError) throw authError;
        const { error } = await admin.from("user_accounts").update({
          username,
          // 현재 관리자가 실수로 자신의 권한을 제거해 관리 화면에서 잠기는 것을 막는다.
          is_admin: memberId === user.id ? true : body?.is_admin === true,
          updated_at: new Date().toISOString(),
        }).eq("user_id", memberId);
        if (error) throw error;
        return json({
          updated: true,
          // 본인 비밀번호 변경은 기존 세션을 무효화하므로 클라이언트가 목록을 재요청하면 안 된다.
          requires_reauthentication: memberId === user.id && passwordChanged,
        }, 200, origin);
      }

      if (action === "delete_member") {
        const memberId = String(body?.user_id || "");
        if (!memberId) return json({ error: "회원 식별자가 필요합니다." }, 400, origin);
        if (memberId === user.id) return json({ error: "현재 로그인한 관리자 계정은 여기서 삭제할 수 없습니다." }, 400, origin);
        const { error } = await admin.auth.admin.deleteUser(memberId);
        if (error) throw error;
        return json({ deleted: true }, 200, origin);
      }

      if (action === "workflow_status") {
        const kind = String(body?.kind || "");
        if (kind !== "check" && kind !== "backup" && kind !== "news") {
          return json({ error: "확인할 작업 종류가 올바르지 않습니다." }, 400, origin);
        }
        const workflow = kind === "check" ? CHECK_WORKFLOW : kind === "backup" ? BACKUP_WORKFLOW : NEWS_WORKFLOW;
        return json({ run: await latestRun(workflow, githubToken) }, 200, origin);
      }

      if (action === "status") {
        const [
          { data: settings },
          check,
          backup,
          news,
          { count: total, error: totalError },
          { count: active, error: activeError },
          { count: errorCount, error: errorCountError },
          { data: errors, error: errorsError },
        ] = await Promise.all([
          admin.from("app_settings").select("value, updated_at").eq("key", "target_check_schedule").maybeSingle(),
          latestRun(CHECK_WORKFLOW, githubToken),
          latestRun(BACKUP_WORKFLOW, githubToken),
          latestRun(NEWS_WORKFLOW, githubToken),
          admin.from("targets").select("id", { count: "exact", head: true }),
          admin.from("targets").select("id", { count: "exact", head: true }).eq("is_active", true),
          admin.from("targets").select("id", { count: "exact", head: true }).not("last_error", "is", null).neq("last_error", ""),
          admin.from("targets")
            .select("id,title,last_error,last_checked_at")
            .not("last_error", "is", null)
            .neq("last_error", "")
            .order("last_checked_at", { ascending: false, nullsFirst: false })
            .limit(100),
        ]);
        const databaseError = totalError || activeError || errorCountError || errorsError;
        if (databaseError) throw databaseError;
        return json({
          schedule: settings?.value || { times: ["08:00", "18:00"], timezone: "Asia/Seoul" },
          schedule_updated_at: settings?.updated_at || null,
          check,
          backup,
          news,
          database: {
            total: total || 0,
            active: active || 0,
            error_count: errorCount || 0,
            errors: errors || [],
          },
        }, 200, origin);
      }

      if (action === "run_check" || action === "run_backup" || action === "run_news") {
        const workflow = action === "run_check" ? CHECK_WORKFLOW : action === "run_backup" ? BACKUP_WORKFLOW : NEWS_WORKFLOW;
        const requestedAt = new Date().toISOString();
        await githubRequest(`/actions/workflows/${workflow}/dispatches`, githubToken, {
          method: "POST",
          body: JSON.stringify({
            ref: BRANCH,
            ...(action === "run_news" ? { inputs: { dry_run: "true" } } : {}),
          }),
        });
        return json({ requested_at: requestedAt }, 202, origin);
      }

      if (action === "update_schedule") {
        const times = validateTimes(body?.times);
        await updateWorkflowSchedule(times, githubToken);
        const { error } = await admin.from("app_settings").upsert({
          key: "target_check_schedule",
          value: { times, timezone: "Asia/Seoul" },
          updated_at: new Date().toISOString(),
          updated_by: user.id,
        });
        if (error) throw error;
        return json({ schedule: { times, timezone: "Asia/Seoul" } }, 200, origin);
      }

      if (action === "list_uncertain_news") {
        const { data, error } = await admin.from("news_article_sentiments")
          .select("id,published_at,source_name,derived_keywords,uncertain_summary")
          .eq("ai_sentiment", "uncertain").is("admin_sentiment", null)
          .order("published_at", { ascending: false }).limit(100);
        if (error) throw error;
        return json({ items: data || [] }, 200, origin);
      }

      if (action === "list_policy_reviews") {
        return json({ items: await listPolicyReviews(admin) }, 200, origin);
      }

      if (action === "resolve_policy_review") {
        return json({ item: await resolvePolicyReview(admin, user.id, body || {}) }, 200, origin);
      }

      if (action === "list_sector_etfs") {
        const { data, error } = await admin.from("market_sector_etfs")
          .select("id,sector_name,etf_name,etf_ticker,issuer,created_at,updated_at")
          .order("sector_name", { ascending: true });
        if (error) throw error;
        return json({ items: data || [] }, 200, origin);
      }

      if (action === "list_extreme_news_rules") {
        const { data, error } = await admin.from("news_extreme_rules")
          .select("id,signal,phrase,created_at,updated_at")
          .order("created_at", { ascending: true });
        if (error) throw error;
        return json({ items: data || [] }, 200, origin);
      }

      if (action === "save_extreme_news_rule") {
        const values = validateExtremeNewsRule(body || {});
        const id = String(body?.id || "").trim();
        if (id) {
          const { data, error } = await admin.from("news_extreme_rules")
            .update({ ...values, updated_at: new Date().toISOString() })
            .eq("id", id)
            .select("id,signal,phrase,is_active,updated_at")
            .maybeSingle();
          if (error) throw error;
          if (!data) return json({ error: "기준 항목을 찾을 수 없습니다." }, 404, origin);
          return json({ item: data }, 200, origin);
        }
        const { data, error } = await admin.from("news_extreme_rules")
          .insert(values)
          .select("id,signal,phrase,is_active,created_at,updated_at")
          .single();
        if (error) throw error;
        return json({ item: data }, 201, origin);
      }

      if (action === "delete_extreme_news_rule") {
        const id = String(body?.id || "").trim();
        if (!id) return json({ error: "기준 항목 식별자가 필요합니다." }, 400, origin);
        const { data, error } = await admin.from("news_extreme_rules")
          .delete()
          .eq("id", id)
          .select("id")
          .maybeSingle();
        if (error) throw error;
        if (!data) return json({ error: "기준 항목을 찾을 수 없습니다." }, 404, origin);
        return json({ deleted: true }, 200, origin);
      }

      if (action === "save_sector_etf") {
        const id = String(body?.id || "").trim();
        if (id) {
          const values = validateSectorEtf(body || {});
          const { data, error } = await admin.from("market_sector_etfs")
            .update({ ...values, updated_at: new Date().toISOString() })
            .eq("id", id)
            .select("id,sector_name,etf_name,etf_ticker,issuer,is_active,updated_at")
            .maybeSingle();
          if (error) throw error;
          if (!data) return json({ error: "등록 항목을 찾을 수 없습니다." }, 404, origin);
          return json({ item: data }, 200, origin);
        }
        const input = validateNewSectorEtf(body || {});
        const credentials = loadKisCredentials(), token = await getKisAccessToken(credentials, admin);
        const end = new Date(), start = new Date(end.getTime() - 10 * 7 * 86_400_000);
        const bundle = await fetchKisDailyPriceBundle(credentials, token, input.etf_ticker, start, end);
        const topHoldings = await fetchKisEtfTopHoldings(credentials, token, input.etf_ticker, 3);
        if (!bundle.instrumentName) throw new Error("KIS에서 ETF 정식명을 확인하지 못했습니다.");
        if (!bundle.prices.length) throw new Error("KIS에서 최근 10주 가격을 확인하지 못했습니다.");
        const values = {
          ...input,
          etf_name: bundle.instrumentName,
          issuer: issuerFromEtfName(bundle.instrumentName),
          is_active: true,
        };
        const { data, error } = await admin.from("market_sector_etfs")
          .insert(values)
          .select("id,sector_name,etf_name,etf_ticker,issuer,is_active,updated_at")
          .single();
        if (error) throw error;
        try {
          const { error: priceError } = await admin.from("market_sector_etf_prices").upsert(bundle.prices.map((price) => ({
            etf_id: data.id, market_date: price.marketDate, open_price: price.open, close_price: price.close,
            volume: price.volume, updated_at: new Date().toISOString(),
          })), { onConflict: "etf_id,market_date" });
          if (priceError) throw priceError;
          if (topHoldings.length) {
            const { error: holdingError } = await admin.from("market_sector_etf_holdings").insert(topHoldings.map((holding, index) => ({
              etf_id: data.id, holding_ticker: holding.ticker, holding_name: holding.name,
              weight_pct: holding.weightPct, weight_rank: index + 1, updated_at: new Date().toISOString(),
            })));
            if (holdingError) throw holdingError;
          }
          await rebuildSectorRankings(supabaseUrl, serviceRoleKey);
          return json({ item: data, price_rows: bundle.prices.length, holding_rows: topHoldings.length }, 201, origin);
        } catch (registrationError) {
          await admin.from("market_sector_etfs").delete().eq("id", data.id);
          throw registrationError;
        }
      }

      if (action === "delete_sector_etf") {
        const id = String(body?.id || "").trim();
        if (!id) return json({ error: "등록 항목 식별자가 필요합니다." }, 400, origin);
        const { data, error } = await admin.from("market_sector_etfs")
          .delete()
          .eq("id", id)
          .select("id")
          .maybeSingle();
        if (error) throw error;
        if (!data) return json({ error: "등록 항목을 찾을 수 없습니다." }, 404, origin);
        return json({ deleted: true }, 200, origin);
      }

      if (action === "resolve_uncertain_news") {
        const id = String(body?.article_id || "");
        const sentiment = String(body?.sentiment || "");
        if (!id || !["positive", "negative", "neutral"].includes(sentiment)) {
          return json({ error: "분류값이 올바르지 않습니다." }, 400, origin);
        }
        const { data, error } = await admin.from("news_article_sentiments")
          .update({ admin_sentiment: sentiment, admin_resolved_at: new Date().toISOString(), updated_at: new Date().toISOString() })
          .eq("id", id).eq("ai_sentiment", "uncertain").is("admin_sentiment", null)
          .select("article_date").maybeSingle();
        if (error) throw error;
        if (!data) return json({ error: "이미 처리되었거나 존재하지 않는 항목입니다." }, 409, origin);
        await refreshArticleSentiment(admin, data.article_date);
        return json({ resolved: true }, 200, origin);
      }

      if (action === "exclude_uncertain_news") {
        const id = String(body?.article_id || "");
        if (!id) return json({ error: "기사 식별자가 필요합니다." }, 400, origin);
        await excludeUncertainArticle(admin, id);
        return json({ excluded: true }, 200, origin);
      }

      return json({ error: "지원하지 않는 요청입니다." }, 400, origin);
    } catch (error) {
      return json({ error: error instanceof Error ? error.message : "요청 처리에 실패했습니다." }, 500, origin);
    }
  },
};

