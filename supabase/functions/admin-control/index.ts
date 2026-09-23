import { ADMIN_CARD_IDS, validateUsername, validatePassword, internalEmail, validateSectorEtf, validateNewSectorEtf, validateAdminCardOrder, validateExtremeNewsRule } from "./validation.ts";
import { BRANCH, deleteAutomationTime, deleteScheduledWorkflow, githubRequest, latestRun, scheduledWorkflows, setWorkflowEnabled, updateAutomationTime } from "./github.ts";
import { refreshArticleSentiment, excludeUncertainArticle } from "./news-review.ts";
import { issuerFromEtfName, rebuildSectorRankings } from "./sector-registry.ts";
import "jsr:@supabase/functions-js/edge-runtime.d.ts";
import { createClient } from "https://esm.sh/@supabase/supabase-js@2";
import { listPolicyReviews, resolvePolicyReview } from "../_shared/policy/policy-admin.ts";
import { createKisRequestRunner, fetchKisDailyPriceBundle, fetchKisEtfTopHoldings, getKisAccessToken, loadKisCredentials } from "../_shared/market/kis-client.ts";
import { incompletePriceHistoryIds } from "../_shared/market/sector-flow.ts";
import { AI_MODEL_ALERT_SETTINGS_KEY, AI_MODEL_SETTINGS_KEY, availableAiModels, configuredAiModel, selectableAiModels, type AiModelRole } from "../_shared/policy/ai-model-selection.ts";

const ALLOWED_ORIGIN = "https://hoorash4.github.io";
const CHECK_WORKFLOW = "check-targets.yml";
const BACKUP_WORKFLOW = "backup-database.yml";
const NEWS_WORKFLOW = "news-pipeline.yml";
const EARNINGS_V2_WORKFLOW = "earnings-v2-korea.yml";


const HISTORICAL_INDEX_CODES = ["SP500", "NASDAQ_COMPOSITE", "KOSPI"] as const;
type HistoricalIndexCode = typeof HISTORICAL_INDEX_CODES[number];
type HistoricalPoint = { market_date: string; close: number };

function historicalDate(value: unknown, required = true) {
  const text = String(value ?? "").trim();
  if (!text && !required) return null;
  if (!/^\d{4}-\d{2}-\d{2}$/.test(text) || !Number.isFinite(Date.parse(text + "T00:00:00Z"))) {
    throw new Error("관찰기간 날짜를 확인해 주세요.");
  }
  return text;
}

function historicalIndex(value: unknown): HistoricalIndexCode {
  const code = String(value || "") as HistoricalIndexCode;
  if (!HISTORICAL_INDEX_CODES.includes(code)) throw new Error("대표 시장지수를 확인해 주세요.");
  return code;
}

function historicalCaseName(value: unknown) {
  const name = String(value || "").replace(/\s+/g, " ").trim();
  if (!name || name.length > 60) throw new Error("국면명은 1~60자로 입력해 주세요.");
  return name;
}

function historicalSummary(value: unknown) {
  const summary = String(value || "").replace(/\s+/g, " ").trim();
  if (!summary || summary.length > 220) throw new Error("국면 요약은 1~220자로 입력해 주세요.");
  return summary;
}

function historicalCycleCandidate(points: HistoricalPoint[]) {
  const rows = points
    .map((row) => ({ date: String(row.market_date), value: Number(row.close) }))
    .filter((row) => /^\d{4}-\d{2}-\d{2}$/.test(row.date) && Number.isFinite(row.value) && row.value > 0)
    .sort((a, b) => a.date.localeCompare(b.date));
  if (rows.length < 20) throw new Error("자동 피봇을 계산하기에 지수 데이터가 부족합니다.");

  const prefixMin: typeof rows = [];
  let low = rows[0];
  for (const row of rows) {
    if (row.value < low.value) low = row;
    prefixMin.push(low);
  }
  const suffixMin: typeof rows = new Array(rows.length);
  low = rows.at(-1)!;
  for (let i = rows.length - 1; i >= 0; i--) {
    if (rows[i].value <= low.value) low = rows[i];
    suffixMin[i] = low;
  }

  let best: { start: typeof rows[number]; peak: typeof rows[number]; trough: typeof rows[number]; score: number } | null = null;
  for (let i = 5; i < rows.length - 5; i++) {
    const start = prefixMin[i - 1], peak = rows[i], trough = suffixMin[i + 1];
    const riseDays = (Date.parse(peak.date) - Date.parse(start.date)) / 86400000;
    const fallDays = (Date.parse(trough.date) - Date.parse(peak.date)) / 86400000;
    if (riseDays < 30 || fallDays < 20 || peak.value <= start.value || trough.value >= peak.value) continue;
    const rise = peak.value / start.value - 1;
    const drawdown = 1 - trough.value / peak.value;
    const score = Math.log1p(Math.max(0, rise)) + Math.log1p(Math.max(0, drawdown) * 1.35);
    if (!best || score > best.score) best = { start, peak, trough, score };
  }
  if (!best) throw new Error("START → PEAK → TROUGH 자동 후보를 찾지 못했습니다.");
  return {
    start_date: best.start.date,
    peak_date: best.peak.date,
    trough_date: best.trough.date,
    start_value: best.start.value,
    peak_value: best.peak.value,
    trough_value: best.trough.value,
    rise_pct: (best.peak.value / best.start.value - 1) * 100,
    fall_pct: (best.trough.value / best.peak.value - 1) * 100,
  };
}

function historicalOutputText(payload: Record<string, unknown>) {
  if (typeof payload.output_text === "string") return payload.output_text;
  const output = Array.isArray(payload.output) ? payload.output : [];
  for (const item of output) {
    if (!item || typeof item !== "object") continue;
    const content = Array.isArray((item as { content?: unknown[] }).content) ? (item as { content: unknown[] }).content : [];
    for (const part of content) {
      if (part && typeof part === "object" && (part as { type?: unknown }).type === "output_text"
        && typeof (part as { text?: unknown }).text === "string") return (part as { text: string }).text;
    }
  }
  return null;
}

async function generateHistoricalSummary(admin: any, input: {
  name: string; primaryIndex: HistoricalIndexCode; searchStart: string; searchEnd: string;
  cycle: ReturnType<typeof historicalCycleCandidate>;
}) {
  const key = Deno.env.get("OPENAI_API_KEY");
  if (!key) return input.name + " 전후 시장 상승과 급락, 이후 조정이 이어진 주요 시장 사이클";
  const model = await configuredAiModel(admin, "standard");
  const schema = {
    type: "object", additionalProperties: false,
    properties: { summary: { type: "string", minLength: 20, maxLength: 140 } },
    required: ["summary"],
  };
  const prompt = [
    "MacroWatch의 과거 시장국면 한줄 요약을 한국어로 작성한다.",
    "한 문장, 약 45~100자. [주요 배경] → [시장 전달경로] → [결과] 구조를 선호한다.",
    "국면명으로 널리 확립된 역사적 배경만 사용할 수 있다. 구체 사실이 불확실하면 가격 사이클 중심으로 보수적으로 쓴다.",
    "수치, 투자판단, 과장 표현은 넣지 않는다.",
  ].join("\n");
  const response = await fetch("https://api.openai.com/v1/responses", {
    method: "POST",
    headers: { Authorization: "Bearer " + key, "Content-Type": "application/json" },
    body: JSON.stringify({
      model, reasoning: { effort: "low" }, max_output_tokens: 300,
      prompt_cache_key: "macrowatch-historical-case-summary-v1",
      input: [
        { role: "system", content: [{ type: "input_text", text: prompt }] },
        { role: "user", content: [{ type: "input_text", text: JSON.stringify({
          case_name: input.name, primary_index: input.primaryIndex,
          observation_window: { start: input.searchStart, end: input.searchEnd },
          detected_cycle: input.cycle,
        }) }] },
      ],
      text: { format: { type: "json_schema", name: "historical_case_summary", strict: true, schema } },
    }),
  });
  if (!response.ok) throw new Error("국면 요약 생성에 실패했습니다. (" + response.status + ")");
  const text = historicalOutputText(await response.json() as Record<string, unknown>);
  if (!text) throw new Error("국면 요약 응답이 비어 있습니다.");
  const parsed = JSON.parse(text) as { summary?: unknown };
  return historicalSummary(parsed.summary);
}

async function historicalCyclesForWindow(admin: any, searchStart: string, searchEnd: string) {
  const cycles: Record<string, ReturnType<typeof historicalCycleCandidate>> = {};
  for (const code of HISTORICAL_INDEX_CODES) {
    const { data, error } = await admin.from("market_index_prices")
      .select("market_date,close")
      .eq("index_code", code)
      .gte("market_date", searchStart)
      .lte("market_date", searchEnd)
      .order("market_date", { ascending: true });
    if (error) throw error;
    try { cycles[code] = historicalCycleCandidate((data || []) as HistoricalPoint[]); } catch {}
  }
  return cycles;
}

async function reorderHistoricalCases(admin: any) {
  const { data, error } = await admin.from("historical_cases")
    .select("case_code,search_start").order("search_start", { ascending: true });
  if (error) throw error;
  const rows = data || [];
  for (let i = 0; i < rows.length; i++) {
    const { error: tempError } = await admin.from("historical_cases")
      .update({ display_order: 1000 + i }).eq("case_code", rows[i].case_code);
    if (tempError) throw tempError;
  }
  for (let i = 0; i < rows.length; i++) {
    const { error: finalError } = await admin.from("historical_cases")
      .update({ display_order: i + 1 }).eq("case_code", rows[i].case_code);
    if (finalError) throw finalError;
  }
}

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

      if (action === "save_historical_indicator_manual_pivot") {
        const caseCode = String(body?.case_code || "").trim();
        const seriesCode = String(body?.series_code || "").trim();
        const indexCode = historicalIndex(body?.index_code);
        const sourceDate = historicalDate(body?.source_date);
        const isDeleted = body?.is_deleted === true;
        if (!caseCode || !seriesCode || !sourceDate) {
          return json({ error: "피봇의 국면·지표·날짜를 확인해 주세요." }, 400, origin);
        }
        const pivotDate = isDeleted ? null : historicalDate(body?.pivot_date);
        const pivotValue = isDeleted ? null : Number(body?.pivot_value);
        const relationship = isDeleted ? null : String(body?.relationship || "");
        const reason = isDeleted ? null : String(body?.reason || "").trim();
        const comment = isDeleted ? null : String(body?.comment || "").trim();
        const keyReference = isDeleted || body?.key_reference == null || body.key_reference === ""
          ? null : String(body.key_reference);
        if (!isDeleted && (!pivotDate || !Number.isFinite(pivotValue)
          || !["positive", "inverse", "unclear"].includes(relationship)
          || !reason || reason.length > 250 || (comment?.length || 0) > 1000
          || (keyReference !== null && !["START", "PEAK", "TROUGH"].includes(keyReference)))) {
          return json({ error: "피봇 입력값을 확인해 주세요." }, 400, origin);
        }
        const { error } = await admin.rpc("save_historical_indicator_manual_pivot", {
          p_case_code: caseCode, p_index_code: indexCode, p_series_code: seriesCode,
          p_source_date: sourceDate, p_pivot_date: pivotDate, p_pivot_value: pivotValue,
          p_relationship: relationship, p_reason: reason, p_comment: comment,
          p_key_reference: keyReference, p_is_deleted: isDeleted, p_user_id: user.id,
        });
        if (error) throw error;
        return json({ saved: true }, 200, origin);
      }

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

      if (action === "delete_automation_time") {
        const workflowId = String(body?.workflow_id || ""), cron = String(body?.cron || "");
        if (!/^[\w.-]+\.yml$/.test(workflowId) || cron.trim().split(/\s+/).length !== 5) {
          return json({ error: "삭제할 실행 시간이 올바르지 않습니다." }, 400, origin);
        }
        await deleteAutomationTime(workflowId, cron, githubToken);
        return json({ deleted: true }, 200, origin);
      }

      if (action === "delete_automation_workflow") {
        const workflowId = String(body?.workflow_id || "");
        if (!/^[\w.-]+\.yml$/.test(workflowId)) {
          return json({ error: "삭제할 자동수집이 올바르지 않습니다." }, 400, origin);
        }
        await deleteScheduledWorkflow(workflowId, githubToken);
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

      if (action === "get_ai_models") {
        const [fomc, standard, alertStatus] = await Promise.all([
          configuredAiModel(admin, "fomc"), configuredAiModel(admin, "standard"),
          admin.from("app_settings").select("value").eq("key", AI_MODEL_ALERT_SETTINGS_KEY).maybeSingle(),
        ]);
        if (alertStatus.error) throw alertStatus.error;
        const lastEmail = alertStatus.data?.value || {};
        return json({
          fomc, standard,
          last_email_at: lastEmail.last_email_at || null,
          last_email_success: typeof lastEmail.last_email_success === "boolean" ? lastEmail.last_email_success : null,
          last_checked_at: lastEmail.last_checked_at || null,
        }, 200, origin);
      }

      if (action === "list_ai_model_candidates" || action === "update_ai_model") {
        const role = String(body?.role || "");
        if (role !== "fomc" && role !== "standard") {
          return json({ error: "AI 모델 종류가 올바르지 않습니다." }, 400, origin);
        }
        const selectedRole = role as AiModelRole;
        const current = await configuredAiModel(admin, selectedRole);
        const apiKey = Deno.env.get("OPENAI_API_KEY");
        if (!apiKey) throw new Error("OPENAI_API_KEY가 설정되지 않았습니다.");
        const choices = selectableAiModels(await availableAiModels(apiKey), current, selectedRole);
        if (action === "list_ai_model_candidates") {
          return json({ role, current, choices }, 200, origin);
        }
        const selected = String(body?.model_id || "");
        if (!choices.some((choice) => choice.id === selected)) {
          return json({ error: "선택한 AI 모델이 현재 사용 가능한 후보에 없습니다." }, 400, origin);
        }
        if (selected === current) return json({ role, model_id: current, updated: false }, 200, origin);
        const { data: settings, error: settingsError } = await admin.from("app_settings")
          .select("value").eq("key", AI_MODEL_SETTINGS_KEY).maybeSingle();
        if (settingsError) throw settingsError;
        const value = settings?.value && typeof settings.value === "object" ? settings.value : {};
        const { error } = await admin.from("app_settings").upsert({
          key: AI_MODEL_SETTINGS_KEY,
          value: { ...value, [selectedRole]: selected },
          updated_at: new Date().toISOString(),
          updated_by: user.id,
        }, { onConflict: "key" });
        if (error) throw error;
        return json({ role, model_id: selected, updated: true }, 200, origin);
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
        let recalculationError = "";
        try {
          await githubRequest(`/actions/workflows/${EARNINGS_V2_WORKFLOW}/dispatches`, githubToken, {
            method: "POST",
            body: JSON.stringify({
              ref: BRANCH,
              inputs: {
                year: String(fiscalYear), quarter: String(fiscalQuarter),
              },
            }),
          });
          recalculationDispatched = true;
        } catch (error) {
          // The manual fact is durable, so do not ask the administrator to
          // re-enter it.  But surface the failed recomputation explicitly.
          recalculationError = error instanceof Error ? error.message : "분기 재계산 시작 요청에 실패했습니다.";
        }
        return json({ item: data, recalculation_dispatched: recalculationDispatched, recalculation_error: recalculationError || null }, 200, origin);
      }


      if (action === "resolve_historical_pivot_review") {
        const caseCode = String(body?.case_code || "").trim();
        const indexCode = String(body?.index_code || "").trim();
        const seriesCode = String(body?.series_code || "").trim();
        const pivotDate = String(body?.pivot_date || "").slice(0, 10);
        const resolution = String(body?.resolution || "").toUpperCase();
        if (!caseCode || !indexCode || !seriesCode || !/^\d{4}-\d{2}-\d{2}$/.test(pivotDate) || !["A","B","C","DELETE"].includes(resolution)) {
          return json({ error: "D 피봇 판정 요청이 올바르지 않습니다." }, 400, origin);
        }
        const { data: analysis, error: analysisError } = await admin.from("historical_indicator_ai_analysis")
          .select("pivots,turning_points").eq("case_code", caseCode).eq("index_code", indexCode).eq("series_code", seriesCode).maybeSingle();
        if (analysisError) throw analysisError;
        if (!analysis) return json({ error: "분석 원본을 찾을 수 없습니다." }, 404, origin);
        const pivots = Array.isArray(analysis.pivots) ? [...analysis.pivots] : [];
        const targetIndex = pivots.findIndex((item: any) => String(item?.date || "").slice(0,10) === pivotDate);
        if (targetIndex < 0 || String(pivots[targetIndex]?.grade || "").toUpperCase() !== "D") {
          return json({ error: "검토 대기(D) 피봇을 찾을 수 없습니다." }, 409, origin);
        }
        const now = new Date().toISOString();
        let resolvedPivot: any = null;
        if (resolution === "DELETE") {
          pivots.splice(targetIndex, 1);
        } else {
          resolvedPivot = {
            ...pivots[targetIndex],
            grade: resolution,
            manual_resolution: true,
            manual_reviewed_at: now,
            manual_reviewed_by: user.id,
          };
          pivots[targetIndex] = resolvedPivot;
        }
        const turningPoints = (Array.isArray(analysis.turning_points) ? analysis.turning_points : [])
          .filter((item: any) => resolution !== "DELETE" || String(item?.date || "").slice(0,10) !== pivotDate)
          .map((item: any) => String(item?.date || "").slice(0,10) === pivotDate && resolution !== "DELETE"
            ? { ...item, grade: resolution, manual_resolution: true, manual_reviewed_at: now }
            : item);
        const { error: updateError } = await admin.from("historical_indicator_ai_analysis").update({
          pivots, turning_points: turningPoints, updated_at: now,
        }).eq("case_code", caseCode).eq("index_code", indexCode).eq("series_code", seriesCode);
        if (updateError) throw updateError;

        const { data: scoreRow, error: scoreError } = await admin.from("historical_indicator_ai_scores")
          .select("ai_pivots").eq("case_code", caseCode).eq("index_code", indexCode).eq("series_code", seriesCode).maybeSingle();
        if (scoreError) throw scoreError;
        if (scoreRow) {
          const scorePivots = Array.isArray(scoreRow.ai_pivots) ? [...scoreRow.ai_pivots] : [];
          const scoreIndex = scorePivots.findIndex((item: any) => String(item?.date || "").slice(0,10) === pivotDate);
          if (scoreIndex >= 0) {
            if (resolution === "DELETE") scorePivots.splice(scoreIndex, 1);
            else scorePivots[scoreIndex] = { ...scorePivots[scoreIndex], grade: resolution, manual_resolution: true, manual_reviewed_at: now };
            const { error: scoreUpdateError } = await admin.from("historical_indicator_ai_scores").update({
              ai_pivots: scorePivots, updated_at: now,
            }).eq("case_code", caseCode).eq("index_code", indexCode).eq("series_code", seriesCode);
            if (scoreUpdateError) throw scoreUpdateError;
          }
        }
        return json({ resolved: true, resolution, pivot: resolvedPivot }, 200, origin);
      }

      if (action === "preview_historical_case") {
        const name = historicalCaseName(body?.case_name);
        const primaryIndex = historicalIndex(body?.primary_index_code);
        const searchStart = historicalDate(body?.search_start)!;
        const searchEnd = historicalDate(body?.search_end)!;
        if (searchStart >= searchEnd) return json({ error: "관찰 종료일은 시작일보다 뒤여야 합니다." }, 400, origin);
        const cycles = await historicalCyclesForWindow(admin, searchStart, searchEnd);
        const primaryCycle = cycles[primaryIndex];
        if (!primaryCycle) return json({ error: "대표지수의 자동 피봇을 계산할 수 없습니다." }, 422, origin);
        const summary = await generateHistoricalSummary(admin, { name, primaryIndex, searchStart, searchEnd, cycle: primaryCycle });
        return json({
          summary,
          cycles: HISTORICAL_INDEX_CODES.flatMap((indexCode) => cycles[indexCode] ? [{
            index_code: indexCode, ...cycles[indexCode],
          }] : []),
        }, 200, origin);
      }

      if (action === "save_historical_case") {
        const code = String(body?.case_code || "").trim();
        const name = historicalCaseName(body?.case_name);
        const primaryIndex = historicalIndex(body?.primary_index_code);
        const searchStart = historicalDate(body?.search_start)!;
        const searchEnd = historicalDate(body?.search_end, false);
        if (searchEnd && searchStart >= searchEnd) return json({ error: "관찰 종료일은 시작일보다 뒤여야 합니다." }, 400, origin);
        const summary = historicalSummary(body?.cycle_summary);
        const rawCycles = Array.isArray(body?.cycles) ? body.cycles : [];
        const cycles = rawCycles.map((item: any) => ({
          index_code: historicalIndex(item?.index_code),
          start_date: historicalDate(item?.start_date, false),
          peak_date: historicalDate(item?.peak_date, false),
          trough_date: historicalDate(item?.trough_date, false),
        }));
        const byIndex = new Map(cycles.map((item: any) => [item.index_code, item]));
        if (!byIndex.get(primaryIndex)?.start_date) return json({ error: "대표지수 START가 필요합니다." }, 400, origin);
        if (searchEnd) {
          for (const indexCode of HISTORICAL_INDEX_CODES) {
            const item = byIndex.get(indexCode);
            if (!item?.start_date || !item?.peak_date || !item?.trough_date) {
              return json({ error: indexCode + "의 START / PEAK / TROUGH를 모두 확인해 주세요." }, 400, origin);
            }
          }
        }
        for (const item of cycles) {
          const ordered = [item.start_date, item.peak_date, item.trough_date].filter(Boolean);
          if (ordered.some((value, index) => index && value < ordered[index - 1])) {
            return json({ error: item.index_code + " 기준점은 START → PEAK → TROUGH 순서여야 합니다." }, 400, origin);
          }
          if (item.start_date && item.start_date < searchStart) return json({ error: "기준점이 관찰 시작일보다 빠릅니다." }, 400, origin);
          const lastPoint = item.trough_date || item.peak_date || item.start_date;
          if (searchEnd && lastPoint && lastPoint > searchEnd) return json({ error: "기준점이 관찰 종료일보다 늦습니다." }, 400, origin);
        }

        let caseCode = code;
        const now = new Date().toISOString();
        if (caseCode) {
          const { data: existing, error: existingError } = await admin.from("historical_cases")
            .select("case_code").eq("case_code", caseCode).maybeSingle();
          if (existingError) throw existingError;
          if (!existing) return json({ error: "수정할 과거 국면을 찾을 수 없습니다." }, 404, origin);
          const { error } = await admin.from("historical_cases").update({
            case_name: name, primary_index_code: primaryIndex,
            comparison_index_codes: HISTORICAL_INDEX_CODES.filter((item) => item !== primaryIndex),
            search_start: searchStart, search_end: searchEnd, cycle_summary: summary,
            updated_at: now, updated_by: user.id,
          }).eq("case_code", caseCode);
          if (error) throw error;
        } else {
          caseCode = "case_" + crypto.randomUUID().replaceAll("-", "").slice(0, 12);
          const { data: orderRows, error: orderError } = await admin.from("historical_cases")
            .select("display_order").order("display_order", { ascending: false }).limit(1);
          if (orderError) throw orderError;
          const displayOrder = Number(orderRows?.[0]?.display_order || 0) + 1;
          const { error } = await admin.from("historical_cases").insert({
            case_code: caseCode, display_order: displayOrder, case_name: name,
            primary_index_code: primaryIndex,
            comparison_index_codes: HISTORICAL_INDEX_CODES.filter((item) => item !== primaryIndex),
            search_start: searchStart, search_end: searchEnd, cycle_summary: summary,
            updated_at: now, updated_by: user.id,
          });
          if (error) throw error;
        }

        if (cycles.length) {
          const { error: cycleError } = await admin.from("historical_case_market_cycles").upsert(cycles.map((item: any) => ({
            case_code: caseCode, index_code: item.index_code,
            start_date: item.start_date, peak_date: item.peak_date, trough_date: item.trough_date,
            updated_at: now, updated_by: user.id,
          })), { onConflict: "case_code,index_code" });
          if (cycleError) throw cycleError;
        }
        await reorderHistoricalCases(admin);
        return json({ saved: true, case_code: caseCode }, code ? 200 : 201, origin);
      }

      if (action === "delete_historical_case") {
        const code = String(body?.case_code || "").trim();
        if (!code) return json({ error: "삭제할 국면 식별자가 필요합니다." }, 400, origin);
        const { data, error } = await admin.rpc("delete_historical_case_with_manual_choice", {
          p_case_code: code, p_delete_manual_pivots: body?.delete_manual_pivots === true,
          p_user_id: user.id,
        });
        if (error) throw error;
        if (!data) return json({ error: "삭제할 과거 국면을 찾을 수 없습니다." }, 404, origin);
        await reorderHistoricalCases(admin);
        return json({ deleted: true }, 200, origin);
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
            history_initialization_pending: incompletePriceHistoryIds([candidateId], finalCoverageRows).has(candidateId),
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


