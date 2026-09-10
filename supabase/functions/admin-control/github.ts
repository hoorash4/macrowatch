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
  return response.json();
}

export async function latestRun(workflow: string, token: string) {
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

export async function latestSuccessfulRun(workflow: string, token: string) {
  const data = await githubRequest(`/actions/workflows/${workflow}/runs?per_page=30`, token);
  const run = data?.workflow_runs?.find((item: { conclusion?: string | null }) => item.conclusion === "success");
  if (!run) return null;
  return { id: run.id, updated_at: run.updated_at, html_url: run.html_url };
}

type WorkflowSource = { name: string; path: string; sha: string; content: string };
const SCHEDULE_BLOCK = /(^  schedule:\r?\n[\s\S]*?)(?=^  (?:[A-Za-z_][\w-]*):|^jobs:)/m;
const CRON_LINE = /^(\s*- cron:\s*["']?)([^"'\r\n]+)(["']?\s*)$/gm;

function decodeContent(value: unknown) {
  return atob(String(value || "").replace(/\s/g, ""));
}

function parseScheduledWorkflow(file: WorkflowSource) {
  const name = /^name:\s*(.+)$/m.exec(file.content)?.[1]?.trim();
  const block = SCHEDULE_BLOCK.exec(file.content)?.[1];
  if (!name || !block) return null;
  const crons = [...block.matchAll(CRON_LINE)].map((match) => match[2].trim());
  return crons.length ? { id: file.name, path: file.path, sha: file.sha, name, crons } : null;
}

async function workflowSource(path: string, token: string): Promise<WorkflowSource> {
  const file = await githubRequest(`/contents/${path}?ref=${BRANCH}`, token);
  return { name: String(file.name), path: String(file.path), sha: String(file.sha), content: decodeContent(file.content) };
}

export async function scheduledWorkflows(token: string) {
  const files = await githubRequest(`/contents/.github/workflows?ref=${BRANCH}`, token);
  const candidates = await Promise.all((Array.isArray(files) ? files : [])
    .filter((file) => String(file.name).endsWith(".yml"))
    .map((file) => workflowSource(String(file.path), token)));
  const workflows = candidates.map(parseScheduledWorkflow).filter(Boolean) as Array<{ id: string; path: string; sha: string; name: string; crons: string[] }>;
  const states = await githubRequest("/actions/workflows?per_page=100", token);
  const stateByPath = new Map((states?.workflows || []).map((item: { path?: string; state?: string }) => [String(item.path || "").replace(/^\.github\//, ".github/"), String(item.state || "active")]));
  return Promise.all(workflows.map(async (workflow) => ({
    ...workflow,
    kst_times: workflow.crons.map(kstTimeFromCron),
    state: stateByPath.get(workflow.path) || "active",
    latest_success: await latestSuccessfulRun(workflow.id, token),
  })));
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

async function saveWorkflowSource(file: WorkflowSource, content: string, message: string, token: string) {
  await githubRequest(`/contents/${file.path}`, token, { method: "PUT", body: JSON.stringify({ message, content: encodeBase64(content), sha: file.sha, branch: BRANCH }) });
}

export async function updateAutomationSchedule(workflowId: string, times: string[], token: string) {
  const file = await workflowSource(`.github/workflows/${workflowId}`, token);
  const parsed = parseScheduledWorkflow(file);
  if (!parsed || parsed.crons.length !== times.length) throw new Error("현재 등록된 자동 수집 일정과 입력 시간이 일치하지 않습니다.");
  const replacements = parsed.crons.map((cron, index) => [cron, updateCronTime(cron, times[index])] as const);
  let next = file.content;
  replacements.forEach(([before], index) => { next = next.replaceAll(before, `__MACROWATCH_CRON_${index}__`); });
  replacements.forEach(([, after], index) => { next = next.replaceAll(`__MACROWATCH_CRON_${index}__`, after); });
  if (next !== file.content) await saveWorkflowSource(file, next, `Update ${parsed.name} schedule from MacroWatch admin`, token);
}

export async function setWorkflowEnabled(workflowId: string, enabled: boolean, token: string) {
  await githubRequest(`/actions/workflows/${workflowId}/${enabled ? "enable" : "disable"}`, token, { method: "PUT" });
}

export async function deleteScheduledWorkflow(workflowId: string, token: string) {
  const file = await workflowSource(`.github/workflows/${workflowId}`, token);
  const parsed = parseScheduledWorkflow(file);
  if (!parsed) throw new Error("삭제할 정기 자동수집 워크플로가 아닙니다.");
  const notifier = await workflowSource(".github/workflows/scheduled-failure-email.yml", token);
  const entry = new RegExp(`^\\s+- "${parsed.name.replace(/[.*+?^${}()|[\\]\\\\]/g, "\\$&")}"\\r?\\n`, "m");
  const nextNotifier = notifier.content.replace(entry, "");
  if (nextNotifier === notifier.content) throw new Error("실패 알림 목록에서 자동수집 항목을 찾지 못했습니다.");
  await saveWorkflowSource(notifier, nextNotifier, `Remove ${parsed.name} from scheduled failure alerts`, token);
  await githubRequest(`/contents/${file.path}`, token, { method: "DELETE", body: JSON.stringify({ message: `Delete ${parsed.name} automation from MacroWatch admin`, sha: file.sha, branch: BRANCH }) });
}

export async function deleteAutomationTime(workflowId: string, cron: string, token: string) {
  const file = await workflowSource(`.github/workflows/${workflowId}`, token);
  const parsed = parseScheduledWorkflow(file);
  if (!parsed || !parsed.crons.includes(cron)) throw new Error("삭제할 실행 시간을 찾지 못했습니다.");
  if (parsed.crons.length === 1) return deleteScheduledWorkflow(workflowId, token);
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
