
export function issuerFromEtfName(name: string) {
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

export async function rebuildSectorRankings(supabaseUrl: string, serviceRoleKey: string) {
  const response = await fetch(`${supabaseUrl}/functions/v1/sector-flow`, {
    method: "POST",
    headers: { apikey: serviceRoleKey, Authorization: `Bearer ${serviceRoleKey}`, "Content-Type": "application/json" },
    body: JSON.stringify({ stage: "close", rebuild_only: true }),
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok || payload?.ok !== true) throw new Error(payload?.error || "섹터 순위 재계산에 실패했습니다.");
}
