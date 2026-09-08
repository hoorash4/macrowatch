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

export function kstTimeToCron(time: string) {
  const [hour, minute] = time.split(":").map(Number);
  const utcMinutes = (hour * 60 + minute - 9 * 60 + 24 * 60) % (24 * 60);
  return `${utcMinutes % 60} ${Math.floor(utcMinutes / 60)} * * *`;
}

export function encodeBase64(value: string) {
  const bytes = new TextEncoder().encode(value);
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary);
}

export async function updateWorkflowSchedule(times: string[], token: string) {
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
