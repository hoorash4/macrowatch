import { assertScheduleTime, publicSchedulePolicy, schedulePolicy, timeMinutes } from "../_shared/schedule-policy.ts";

export const REPOSITORY = "hoorash4/macrowatch";
export const BRANCH = "main";

export function githubHeaders(token: string) {
  return {
    "Authorization": `Bearer ${token}`,
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    "Content-Type": "application/json",
  };
}

export async function githubRequest(path: string, token: string, init: RequestInit = {}) {
  const response = await fetch(`https://api.github.com/repos/${REPOSITORY}${path}`, {
    ...init,
    headers: { ...githubHeaders(token), ...(init.headers || {}) },
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(error.message || `GitHub 요청 실패 (${response.status})`);
  }
  if (response.status === 204) return null;
  const text = await response.text();
  return text ? JSON.parse(text) : null;
}

export async function latestRun(workflow: string, token: string) {
  const data = await githubRequest(
    `/actions/workflows/${workflow}/runs?per_page=20`,
    token,
  );
  // A workflow source update can leave a failed `push` record behind even
  // though collectors are schedule-only. The admin dashboard must show the
  // most recent actual collection, not that historical validation record.
  const run = data?.workflow_runs?.find((item: { event?: string; conclusion?: string | null }) => (
    (item.event === "schedule" || item.event === "workflow_dispatch")
    && item.conclusion !== "skipped"
  ));
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

export async function latestSuccessfulRun(workflow: string, token: string) {
  const data = await githubRequest(`/actions/workflows/${workflow}/runs?per_page=30`, token);
  const run = data?.workflow_runs?.find((item: { event?: string; conclusion?: string | null }) => (
    (item.event === "schedule" || item.event === "workflow_dispatch")
    && item.conclusion === "success"
  ));
  if (!run) return null;
  return { id: run.id, updated_at: run.updated_at, html_url: run.html_url };
}

type WorkflowSource = { name: string; path: string; sha: string; content: string };
const SCHEDULE_BLOCK = /(^  schedule:\r?\n[\s\S]*?)(?=^  (?:[A-Za-z_][\w-]*):|^jobs:)/m;
const CRON_LINE = /^(\s*- cron:\s*["']?)([^"'\r\n]+)(["']?\s*)$/gm;

export function decodeBase64Utf8(value: unknown) {
  const binary = atob(String(value || "").replace(/\s/g, ""));
  const bytes = Uint8Array.from(binary, (character) => character.charCodeAt(0));
  return new TextDecoder("utf-8", { fatal: true }).decode(bytes);
}

function parseScheduledWorkflow(file: WorkflowSource) {
  const name = /^name:\s*(.+)$/m.exec(file.content)?.[1]?.trim();
  const block = SCHEDULE_BLOCK.exec(file.content)?.[1];
  if (!name || !block) return null;
  const crons = [...block.matchAll(CRON_LINE)].map((match) => match[2].trim());
  return crons.length ? { id: file.name, path: file.path, sha: file.sha, name, crons } : null;
}

function assertScheduleEditIsIsolated(file: WorkflowSource) {
  if (/^  (?:push|workflow_run|repository_dispatch):/m.test(file.content)) {
    throw new Error("시간 변경과 함께 다른 실행이 시작될 수 있는 워크플로는 수정할 수 없습니다.");
  }
}

async function workflowSource(path: string, token: string): Promise<WorkflowSource> {
  const file = await githubRequest(`/contents/${path}?ref=${BRANCH}`, token);
  return { name: String(file.name), path: String(file.path), sha: String(file.sha), content: decodeBase64Utf8(file.content) };
}

const AUTOMATION_CARD_NAMES: Record<string, string> = {
  "backup-database.yml": "데이터베이스 백업",
  "central-bank-policy.yml": "통화정책 시그널",
  "check-targets.yml": "지표 추적 알림",
  "em-capital-capacity.yml": "이머징 자금 유입 여건",
  "em-stress.yml": "이머징 시장 스트레스 지수",
  "equity-bond-attractiveness.yml": "주식투자 매력 흐름",
  "equity-bond-relative-value.yml": "주식투자 매력 흐름 · 상대가치",
  "financial-stress.yml": "미국 신용위험 추이",
  "inflation-model.yml": "통합물가지수와 금리",
  "korea-foreign-flow.yml": "외국인 자금 유출입 강도",
  "korea-small-business-risk.yml": "한국 중소기업 위험지수",
  "korea-stress.yml": "한국 시장 스트레스 지수",
  "liquidity.yml": "미국·한국 주식시장 자금환경",
  "market-context.yml": "뉴스 분석용 KOSPI 가격 수집",
  "news-pipeline.yml": "뉴스 흐름",
  "policy-expectation.yml": "시장 내재 정책금리 기대",
  "small-business-risk.yml": "미국 중소기업 위험지수",
};

function automationDisplayName(workflow: { id: string; name: string }) {
  return schedulePolicy(workflow.id)?.displayName || AUTOMATION_CARD_NAMES[workflow.id] || workflow.name;
}

function sectorStage(workflowId: string) {
  const match = /^sector-flow-(open|intraday|close)\.yml$/.exec(workflowId);
  return match?.[1] || null;
}

function supabaseEnvironment() {
  if (typeof Deno === "undefined") return null;
  const url = Deno.env.get("SUPABASE_URL")?.replace(/\/$/, "");
  const key = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY");
  return url && key ? { url, key } : null;
}

async function supabaseRpc(name: string, body: Record<string, unknown> = {}) {
  const environment = supabaseEnvironment();
  if (!environment) return [];
  const response = await fetch(`${environment.url}/rest/v1/rpc/${name}`, {
    method: "POST",
    headers: {
      apikey: environment.key,
      Authorization: `Bearer ${environment.key}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(`Supabase scheduler 요청 실패 (${response.status}): ${text.slice(0, 300)}`);
  }
  if (response.status === 204) return null;
  const text = await response.text();
  return text ? JSON.parse(text) : null;
}

async function sectorScheduleRows() {
  const rows = await supabaseRpc("macrowatch_sector_flow_schedules");
  return Array.isArray(rows) ? rows : [];
}

async function scheduledSectorFlows() {
  const rows = await sectorScheduleRows();
  const entries = [];
  for (const stage of ["open", "intraday", "close"]) {
    const primary = rows.find((row) => row?.jobname === `macrowatch-sector-flow-${stage}-primary`);
    const retry = rows.find((row) => row?.jobname === `macrowatch-sector-flow-${stage}-retry`);
    if (!primary || !retry) continue;
    const workflowId = `sector-flow-${stage}.yml`;
    const policy = schedulePolicy(workflowId);
    entries.push({
      workflow_id: workflowId,
      cron: String(primary.schedule),
      kst_time: kstTimeFromCron(String(primary.schedule)),
      name: policy?.displayName || `sector-flow ${stage}`,
      state: primary.active === true && retry.active === true ? "active" : "disabled_manually",
      schedule_count: 1,
      latest_success: null,
      ...publicSchedulePolicy(policy),
    });
  }
  return entries;
}

export async function scheduledWorkflows(token: string) {
  const files = await githubRequest(`/contents/.github/workflows?ref=${BRANCH}`, token);
  const candidates = await Promise.all((Array.isArray(files) ? files : [])
    .filter((file) => String(file.name).endsWith(".yml"))
    .map((file) => workflowSource(String(file.path), token)));
  const workflows = candidates.map(parseScheduledWorkflow).filter(Boolean) as Array<{ id: string; path: string; sha: string; name: string; crons: string[] }>;
  const states = await githubRequest("/actions/workflows?per_page=100", token);
  const stateByPath = new Map((states?.workflows || []).map((item: { path?: string; state?: string }) => [String(item.path || "").replace(/^\.github\//, ".github/"), String(item.state || "active")]));
  const entries = await Promise.all(workflows.map(async (workflow) => {
    const latestSuccess = await latestSuccessfulRun(workflow.id, token);
    const state = stateByPath.get(workflow.path) || "active";
    const policy = schedulePolicy(workflow.id);
    return workflow.crons.map((cron) => ({
      workflow_id: workflow.id,
      cron,
      kst_time: kstTimeFromCron(cron),
      name: automationDisplayName(workflow),
      state,
      schedule_count: workflow.crons.length,
      latest_success: latestSuccess,
      scheduler: "github",
      ...publicSchedulePolicy(policy),
    }));
  }));
  const sectorEntries = await scheduledSectorFlows();
  return [...entries.flat(), ...sectorEntries]
    .sort((left, right) => left.kst_time.localeCompare(right.kst_time) || left.name.localeCompare(right.name, "ko"));
}

export function kstTimeFromCron(cron: string) {
  const [minute, hour] = cron.trim().split(/\s+/, 3).map(Number);
  if (!Number.isInteger(minute) || !Number.isInteger(hour)) throw new Error("지원하지 않는 cron 형식입니다.");
  const total = (hour * 60 + minute + 9 * 60) % (24 * 60);
  return `${String(Math.floor(total / 60)).padStart(2, "0")}:${String(total % 60).padStart(2, "0")}`;
}

function kstDateShift(utcHour: number, utcMinute: number) {
  return utcHour * 60 + utcMinute + 9 * 60 >= 24 * 60 ? 1 : 0;
}

function shiftWeekdays(value: string, delta: number) {
  if (value === "*") return value;
  const days = new Set<number>();
  for (const part of value.split(",")) {
    const range = part.split("-").map(Number);
    if (range.some((day) => !Number.isInteger(day) || day < 0 || day > 6)) throw new Error("지원하지 않는 요일 cron 형식입니다.");
    const [start, end = start] = range;
    if (start > end) throw new Error("지원하지 않는 요일 cron 형식입니다.");
    for (let day = start; day <= end; day += 1) days.add((day + delta + 7) % 7);
  }
  const sorted = [...days].sort((a, b) => a - b);
  const groups: number[][] = [];
  for (const day of sorted) {
    const group = groups.at(-1);
    if (group && day === group.at(-1)! + 1) group.push(day);
    else groups.push([day]);
  }
  return groups.map((group) => group.length === 1 ? String(group[0]) : `${group[0]}-${group.at(-1)}`).join(",");
}

export function updateCronTime(cron: string, time: string) {
  const fields = cron.trim().split(/\s+/);
  if (fields.length !== 5 || !/^\d{2}:\d{2}$/.test(time)) throw new Error("시간 또는 cron 형식이 올바르지 않습니다.");
  const [hour, minute] = time.split(":").map(Number);
  if (hour > 23 || minute > 59) throw new Error("시간 형식이 올바르지 않습니다.");
  const oldMinute = Number(fields[0]);
  const oldHour = Number(fields[1]);
  if (!Number.isInteger(oldMinute) || !Number.isInteger(oldHour) || oldMinute < 0 || oldMinute > 59 || oldHour < 0 || oldHour > 23) {
    throw new Error("지원하지 않는 cron 형식입니다.");
  }
  const utc = (hour * 60 + minute - 9 * 60 + 24 * 60) % (24 * 60);
  const shift = kstDateShift(oldHour, oldMinute) - (hour < 9 ? 1 : 0);
  if (shift !== 0 && fields[2] !== "*") {
    throw new Error("월간 일정은 한국 날짜가 바뀌지 않는 시간으로만 변경할 수 있습니다.");
  }
  fields[0] = String(utc % 60);
  fields[1] = String(Math.floor(utc / 60));
  fields[4] = shiftWeekdays(fields[4], shift);
  return fields.join(" ");
}

function weekdayCronForKst(time: string) {
  const minutes = timeMinutes(time);
  if (minutes < 9 * 60) throw new Error("Supabase 장중 스케줄은 09:00 KST 이전으로 변경할 수 없습니다.");
  const utc = minutes - 9 * 60;
  return `${utc % 60} ${Math.floor(utc / 60)} * * 1-5`;
}

function addMinutes(time: string, delta: number) {
  const minutes = timeMinutes(time) + delta;
  if (minutes >= 24 * 60) throw new Error("재시도 시각이 다음 한국 날짜로 넘어가므로 이 시간으로 변경할 수 없습니다.");
  return `${String(Math.floor(minutes / 60)).padStart(2, "0")}:${String(minutes % 60).padStart(2, "0")}`;
}

async function assertPhaseOrder(workflowId: string, time: string, token: string) {
  const relation: Record<string, { other: string; position: "before" | "after"; label: string }> = {
    "earnings-us-automatic.yml": { other: "earnings-us-edgar-automatic.yml", position: "before", label: "미국 SEC 신규 공시 단계" },
    "earnings-us-edgar-automatic.yml": { other: "earnings-us-automatic.yml", position: "after", label: "미국 분기 실적 스냅샷 단계" },
    "earnings-v2-korea-automatic.yml": { other: "earnings-v2-korea-kis-automatic.yml", position: "before", label: "한국 KIS 가격 단계" },
    "earnings-v2-korea-kis-automatic.yml": { other: "earnings-v2-korea-automatic.yml", position: "after", label: "한국 DART 공시 단계" },
  };
  const rule = relation[workflowId];
  if (!rule) return;
  const other = parseScheduledWorkflow(await workflowSource(`.github/workflows/${rule.other}`, token));
  if (!other || other.crons.length !== 1) throw new Error("연결된 phase 일정을 확인하지 못했습니다.");
  const otherTime = kstTimeFromCron(other.crons[0]);
  const currentMinutes = timeMinutes(time), otherMinutes = timeMinutes(otherTime);
  const invalid = rule.position === "before" ? currentMinutes >= otherMinutes : currentMinutes <= otherMinutes;
  if (invalid) {
    const direction = rule.position === "before" ? "이전" : "이후";
    throw new Error(`${rule.label}(${otherTime} KST) ${direction} 시간으로만 변경할 수 있습니다.`);
  }
}

async function cancelQueuedScheduledRuns(workflowId: string, token: string) {
  const data = await githubRequest(`/actions/workflows/${workflowId}/runs?event=schedule&status=queued&per_page=100`, token);
  const runs = Array.isArray(data?.workflow_runs) ? data.workflow_runs : [];
  for (const run of runs) {
    const id = Number(run?.id);
    if (Number.isInteger(id) && id > 0) {
      await githubRequest(`/actions/runs/${id}/cancel`, token, { method: "POST" });
    }
  }
  return runs.length;
}

async function saveWorkflowSource(file: WorkflowSource, content: string, message: string, token: string) {
  await githubRequest(`/contents/${file.path}`, token, { method: "PUT", body: JSON.stringify({ message, content: encodeBase64(content), sha: file.sha, branch: BRANCH }) });
}

async function updateSectorFlowTime(workflowId: string, cron: string, time: string) {
  const stage = sectorStage(workflowId);
  if (!stage) throw new Error("sector-flow stage가 올바르지 않습니다.");
  const policy = schedulePolicy(workflowId);
  assertScheduleTime(policy, time);
  const rows = await sectorScheduleRows();
  const primary = rows.find((row) => row?.jobname === `macrowatch-sector-flow-${stage}-primary`);
  if (!primary || String(primary.schedule) !== cron) throw new Error("현재 등록된 Supabase 실행 시간을 찾지 못했습니다.");
  const retryMinutes = policy?.retryMinutes || 15;
  const retryTime = addMinutes(time, retryMinutes);
  await supabaseRpc("macrowatch_set_sector_flow_schedule", {
    p_stage: stage,
    p_primary_schedule: weekdayCronForKst(time),
    p_retry_schedule: weekdayCronForKst(retryTime),
  });
}

export async function updateAutomationTime(workflowId: string, cron: string, time: string, token: string) {
  if (sectorStage(workflowId)) {
    await updateSectorFlowTime(workflowId, cron, time);
    return;
  }
  assertScheduleTime(schedulePolicy(workflowId), time);
  await assertPhaseOrder(workflowId, time, token);
  const file = await workflowSource(`.github/workflows/${workflowId}`, token);
  assertScheduleEditIsIsolated(file);
  const parsed = parseScheduledWorkflow(file);
  if (!parsed || !parsed.crons.includes(cron)) throw new Error("현재 등록된 실행 시간을 찾지 못했습니다.");
  const updatedCron = updateCronTime(cron, time);
  if (updatedCron === cron) return;
  const block = SCHEDULE_BLOCK.exec(file.content)?.[1];
  if (!block) throw new Error("자동수집 일정 블록을 찾지 못했습니다.");
  const next = file.content.replace(block, block.replace(cron, updatedCron));
  if (next === file.content) throw new Error("현재 등록된 실행 시간을 찾지 못했습니다.");
  await cancelQueuedScheduledRuns(workflowId, token);
  await saveWorkflowSource(file, next, `Update ${parsed.name} schedule from MacroWatch admin`, token);
}

export async function setWorkflowEnabled(workflowId: string, enabled: boolean, token: string) {
  const stage = sectorStage(workflowId);
  if (stage) {
    await supabaseRpc("macrowatch_set_sector_flow_enabled", { p_stage: stage, p_enabled: enabled });
    return;
  }
  await githubRequest(`/actions/workflows/${workflowId}/${enabled ? "enable" : "disable"}`, token, { method: "PUT" });
}

export async function deleteScheduledWorkflow(workflowId: string, token: string) {
  if (sectorStage(workflowId)) throw new Error("주도섹터 자동수집 단계는 관리자 화면에서 삭제할 수 없습니다.");
  const file = await workflowSource(`.github/workflows/${workflowId}`, token);
  const parsed = parseScheduledWorkflow(file);
  if (!parsed) throw new Error("삭제할 정기 자동수집 워크플로가 아닙니다.");
  const notifier = await workflowSource(".github/workflows/scheduled-failure-email.yml", token);
  const entry = new RegExp(`^\\s+- "${parsed.name.replace(/[.*+?^${}()|[\\]\\\\]/g, "\\$&")}"\\r?\\n`, "m");
  const nextNotifier = notifier.content.replace(entry, "");
  if (nextNotifier === notifier.content) throw new Error("실패 알림 목록에서 자동수집 항목을 찾지 못했습니다.");
  // Contents API updates one file per commit. Updating the alert list first
  // and deleting this workflow second can leave monitoring inconsistent when
  // either request fails. Write both tree changes in one non-force ref update.
  const ref = await githubRequest(`/git/ref/heads/${BRANCH}`, token);
  const parent = String(ref?.object?.sha || "");
  if (!parent) throw new Error("현재 기본 브랜치 커밋을 찾지 못했습니다.");
  const parentCommit = await githubRequest(`/git/commits/${parent}`, token);
  const baseTree = String(parentCommit?.tree?.sha || "");
  if (!baseTree) throw new Error("현재 기본 브랜치 트리를 찾지 못했습니다.");
  const tree = await githubRequest("/git/trees", token, {
    method: "POST",
    body: JSON.stringify({
      base_tree: baseTree,
      tree: [
        { path: notifier.path, mode: "100644", type: "blob", content: nextNotifier },
        { path: file.path, mode: "100644", type: "blob", sha: null },
      ],
    }),
  });
  const treeSha = String(tree?.sha || "");
  if (!treeSha) throw new Error("삭제용 Git 트리를 만들지 못했습니다.");
  const commit = await githubRequest("/git/commits", token, {
    method: "POST",
    body: JSON.stringify({
      message: `Delete ${parsed.name} automation from MacroWatch admin`,
      tree: treeSha,
      parents: [parent],
    }),
  });
  const commitSha = String(commit?.sha || "");
  if (!commitSha) throw new Error("삭제용 Git 커밋을 만들지 못했습니다.");
  await githubRequest(`/git/refs/heads/${BRANCH}`, token, {
    method: "PATCH", body: JSON.stringify({ sha: commitSha, force: false }),
  });
}

export async function deleteAutomationTime(workflowId: string, cron: string, token: string) {
  if (sectorStage(workflowId)) throw new Error("주도섹터 primary/retry 일정은 단계 단위로 유지되어야 하므로 개별 삭제할 수 없습니다.");
  const file = await workflowSource(`.github/workflows/${workflowId}`, token);
  const parsed = parseScheduledWorkflow(file);
  if (!parsed || !parsed.crons.includes(cron)) throw new Error("삭제할 실행 시간을 찾지 못했습니다.");
  if (parsed.crons.length === 1) {
    throw new Error("마지막 일정입니다. 자동수집 전체 삭제를 별도로 확인하세요.");
  }
  const block = SCHEDULE_BLOCK.exec(file.content)?.[1];
  if (!block) throw new Error("자동수집 일정 블록을 찾지 못했습니다.");
  let removed = false;
  const nextBlock = block.split(/(?<=\n)/).filter((line) => {
    const match = /^\s*- cron:\s*["']?([^"'\r\n]+)["']?\s*$/.exec(line.trim());
    if (!removed && match?.[1].trim() === cron) { removed = true; return false; }
    return true;
  }).join("");
  if (!removed) throw new Error("삭제할 실행 시간을 찾지 못했습니다.");
  await saveWorkflowSource(file, file.content.replace(block, nextBlock), `Remove one ${parsed.name} schedule from MacroWatch admin`, token);
}

export function encodeBase64(value: string) {
  const bytes = new TextEncoder().encode(value);
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary);
}
