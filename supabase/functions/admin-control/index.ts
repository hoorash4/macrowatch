import { ADMIN_CARD_IDS, validateUsername, validatePassword, internalEmail, validateSectorEtf, validateNewSectorEtf, validateAdminCardOrder, validateExtremeNewsRule } from "./validation.ts";
import { BRANCH, deleteAutomationTime, deleteScheduledWorkflow, githubRequest, latestRun, scheduledWorkflows, setWorkflowEnabled, updateAutomationTime } from "./github.ts";
import { refreshArticleSentiment, excludeUncertainArticle } from "./news-review.ts";
import { issuerFromEtfName, rebuildSectorRankings } from "./sector-registry.ts";
import "jsr:@supabase/functions-js/edge-runtime.d.ts";
import { createClient } from "https://esm.sh/@supabase/supabase-js@2";
import { listPolicyReviews, resolvePolicyReview } from "../_shared/policy/policy-admin.ts";
import { createKisRequestRunner, fetchKisDailyPriceBundle, fetchKisEtfTopHoldings, getKisAccessToken, loadKisCredentials } from "../_shared/market/kis-client.ts";
import { incompletePriceHistoryIds } from "../_shared/market/sector-flow.ts";

const ALLOWED_ORIGIN = "https://hoorash4.github.io";
const CHECK_WORKFLOW = "check-targets.yml";
const BACKUP_WORKFLOW = "backup-database.yml";
const NEWS_WORKFLOW = "news-pipeline.yml";
const EARNINGS_V2_WORKFLOW = "earnings-v2-korea.yml";

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

async function authenticatedUser(supabaseUrl: string, anonKey: string, jwt: string) {
  const response = await fetch(`${supabaseUrl}/auth/v1/user`, {
    headers: { apikey: anonKey, Authorization: `Bearer ${jwt}` },
  });
  if (!response.ok) return null;
  const user = await response.json().catch(() => null);
  return user?.id ? user : null;
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
        const { data: duplicate, error: duplicateError } = await admin.from("user_accounts")
          .select("user_id").ilike("username", username).maybeSingle();
        if (duplicateError) throw duplicateError;
        if (duplicate) return json({ error: "이미 사용 중인 아이디입니다." }, 409, origin);
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
          check,
          backup,
          news,
          { count: total, error: totalError },
          { count: active, error: activeError },
          { count: errorCount, error: errorCountError },
          { data: errors, error: errorsError },
        ] = await Promise.all([
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

      if (action === "list_automation_schedules") {
        return json({ items: await scheduledWorkflows(githubToken) }, 200, origin);
      }

      if (action === "update_automation_time") {
        const workflowId = String(body?.workflow_id || "");
        const cron = String(body?.cron || "");
        const time = String(body?.time || "");
        if (!/^[\w.-]+\.yml$/.test(workflowId) || cron.trim().split(/\s+/).length !== 5 || !/^\d{2}:\d{2}$/.test(time)) {
          return json({ error: "자동수집 시간 입력이 올바르지 않습니다." }, 400, origin);
        }
        await updateAutomationTime(workflowId, cron, time, githubToken);
        return json({ updated: true }, 200, origin);
      }

      if (action === "set_automation_enabled") {
        const workflowId = String(body?.workflow_id || "");
        if (!/^[\w.-]+\.yml$/.test(workflowId) || typeof body?.enabled !== "boolean") {
          return json({ error: "자동수집 상태 입력이 올바르지 않습니다." }, 400, origin);
        }
        await setWorkflowEnabled(workflowId, body.enabled, githubToken);
        return json({ enabled: body.enabled }, 200, origin);
      }

      if (action === "delete_automation_schedule") {
        const workflowId = String(body?.workflow_id || "");
        if (!/^[\w.-]+\.yml$/.test(workflowId)) return json({ error: "삭제할 자동수집 항목이 올바르지 않습니다." }, 400, origin);
        await deleteScheduledWorkflow(workflowId, githubToken);
        return json({ deleted: true }, 200, origin);
      }

      if (action === "delete_automation_time") {
        const workflowId = String(body?.workflow_id || ""), cron = String(body?.cron || "");
        if (!/^[\w.-]+\.yml$/.test(workflowId) || cron.trim().split(/\s+/).length !== 5) {
          return json({ error: "삭제할 실행 시간이 올바르지 않습니다." }, 400, origin);
        }
        await deleteAutomationTime(workflowId, cron, githubToken);
        return json({ deleted: true }, 200, origin);
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

      if (action === "get_admin_card_order") {
        const key = `admin_card_order_${user.id}`;
        const { data, error } = await admin.from("app_settings")
          .select("value").eq("key", key).maybeSingle();
        if (error) throw error;
        const stored = data?.value && typeof data.value === "object"
          ? (data.value as Record<string, unknown>).order
          : [];
        return json({
          order: Array.isArray(stored)
            ? stored.filter((id) => ADMIN_CARD_IDS.has(String(id))).map(String)
            : [],
        }, 200, origin);
      }

      if (action === "save_admin_card_order") {
        const order = validateAdminCardOrder(body?.order);
        const { error } = await admin.from("app_settings").upsert({
          key: `admin_card_order_${user.id}`,
          value: { order },
          updated_at: new Date().toISOString(),
          updated_by: user.id,
        }, { onConflict: "key" });
        if (error) throw error;
        return json({ order }, 200, origin);
      }

      if (action === "list_uncertain_news") {
        const { data, error } = await admin.from("news_article_sentiments")
          .select("id,published_at,source_name,derived_keywords,uncertain_summary")
          .eq("ai_sentiment", "uncertain").is("admin_sentiment", null)
          .order("published_at", { ascending: false }).limit(100);
        if (error) throw error;
        return json({ items: data || [] }, 200, origin);
      }

      if (action === "list_earnings_v2_pending") {
        const { data, error } = await admin.rpc("earnings_v2_list_pending");
        if (error) throw error;
        return json({ items: data || [] }, 200, origin);
      }

      if (action === "resolve_earnings_v2_pending") {
        const companyId = String(body?.company_id || "").trim();
        const fiscalYear = Number(body?.fiscal_year);
        const fiscalQuarter = Number(body?.fiscal_quarter);
        const amount = (value: unknown, label: string) => {
          const text = String(value ?? "").trim().replaceAll(",", "");
          if (!/^-?\d+(?:\.\d{1,4})?$/.test(text)) throw new Error(`${label}을(를) 숫자로 입력하세요.`);
          return text;
        };
        if (!companyId || !Number.isInteger(fiscalYear) || ![1, 2, 3, 4].includes(fiscalQuarter)) {
          throw new Error("기업과 분기 정보가 올바르지 않습니다.");
        }
        const { data, error } = await admin.rpc("earnings_v2_resolve_pending", {
          p_company_id: companyId,
          p_fiscal_year: fiscalYear,
          p_fiscal_quarter: fiscalQuarter,
          p_top_line: amount(body?.top_line, "매출"),
          p_operating_income: amount(body?.operating_income, "영업이익"),
          p_net_income: amount(body?.net_income, "순이익"),
        });
        let recalculationDispatched = false;
        try {
          await githubRequest(`/actions/workflows/${EARNINGS_V2_WORKFLOW}/dispatches`, githubToken, {
            method: "POST",
            body: JSON.stringify({
              ref: BRANCH,
              inputs: {
                year: String(fiscalYear), quarter: String(fiscalQuarter),
                write: "true", recalculate_only: "true",
              },
            }),
          });
          recalculationDispatched = true;
        } catch (_) {
          // The manual fact is already durable. A dispatch failure must not make
          // the administrator repeat the same write or see a false save error.
        }
        return json({ item: data, recalculation_dispatched: recalculationDispatched }, 200, origin);
      }

      if (action === "list_policy_reviews") {
        return json({ items: await listPolicyReviews(admin, Array.isArray(body?.meeting_dates) ? body.meeting_dates.map(String) : []) }, 200, origin);
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
        const runKisRequest = createKisRequestRunner();
        const end = new Date(), start = new Date(end.getTime() - 10 * 7 * 86_400_000);
        let bundle = await runKisRequest(() => fetchKisDailyPriceBundle(credentials, token, input.etf_ticker, start, end));
        const historyStart = start.toISOString().slice(0, 10);
        const { data: referenceRows, error: referenceError } = await admin.from("market_sector_etf_prices")
          .select("etf_id,market_date").gte("market_date", historyStart);
        if (referenceError) throw referenceError;
        const candidateId = "new-etf";
        const coverageRows = [
          ...(referenceRows || []).map((row) => ({ etfId: row.etf_id, marketDate: row.market_date })),
          ...bundle.prices.map((price) => ({ etfId: candidateId, marketDate: price.marketDate })),
        ];
        // KIS가 일시적으로 일부 일봉만 반환하면 등록 전에 한 번 더 요청해 합칩니다.
        if (incompletePriceHistoryIds([candidateId], coverageRows).has(candidateId)) {
          const retryBundle = await runKisRequest(() => fetchKisDailyPriceBundle(credentials, token, input.etf_ticker, start, end));
          const pricesByDate = new Map(bundle.prices.map((price) => [price.marketDate, price]));
          retryBundle.prices.forEach((price) => pricesByDate.set(price.marketDate, price));
          bundle = {
            instrumentName: bundle.instrumentName || retryBundle.instrumentName,
            prices: [...pricesByDate.values()].sort((a, b) => a.marketDate.localeCompare(b.marketDate)),
          };
        }
        const topHoldings = await runKisRequest(() => fetchKisEtfTopHoldings(credentials, token, input.etf_ticker, 3));
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
            latest_price: price.close, price_stage: "close",
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
          const finalCoverageRows = [
            ...(referenceRows || []).map((row) => ({ etfId: row.etf_id, marketDate: row.market_date })),
            ...bundle.prices.map((price) => ({ etfId: candidateId, marketDate: price.marketDate })),
          ];
          return json({
            item: data, price_rows: bundle.prices.length, holding_rows: topHoldings.length,
            history_backfill_pending: incompletePriceHistoryIds([candidateId], finalCoverageRows).has(candidateId),
          }, 201, origin);
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

