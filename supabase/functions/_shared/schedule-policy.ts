export type SchedulerType = "github" | "supabase";

export type SchedulePolicy = {
  id: string;
  scheduler: SchedulerType;
  displayName: string;
  phase?: string;
  earliestSafeTimeKst?: string;
  latestRecommendedTimeKst?: string;
  allowedWeekdays?: number[];
  dependencyLabel?: string;
  dependencyReason?: string;
  dependencyType?:
    | "after_kospi_close"
    | "after_us_market_close"
    | "after_ecos_daily_release"
    | "after_source_publication"
    | "after_previous_phase"
    | "fixed_safe_window";
  retryMinutes?: number;
  editable?: boolean;
  mustRunBeforeId?: string;
  mustRunAfterId?: string;
};

const POLICIES: Record<string, SchedulePolicy> = {
  "liquidity.yml": {
    id: "liquidity.yml",
    scheduler: "github",
    displayName: "미국·한국 주식시장 자금환경",
    earliestSafeTimeKst: "13:30",
    dependencyLabel: "한국은행 및 관련 한국 유동성 원천데이터",
    dependencyReason: "KOFR와 월간 통화·유동성 등 필수 한국 원천데이터가 공개된 이후에 실행되어야 합니다.",
    dependencyType: "after_ecos_daily_release",
    editable: true,
  },
  "market-context.yml": {
    id: "market-context.yml",
    scheduler: "github",
    displayName: "뉴스 분석용 KOSPI 가격 수집",
    earliestSafeTimeKst: "16:00",
    allowedWeekdays: [1, 2, 3, 4, 5],
    dependencyLabel: "KOSPI 정규장 종가",
    dependencyReason: "KOSPI 정규장이 15:30에 종료된 뒤 종가 데이터가 확정되어야 합니다.",
    dependencyType: "after_kospi_close",
    editable: true,
  },
  "earnings-us-automatic.yml": {
    id: "earnings-us-automatic.yml",
    scheduler: "github",
    displayName: "시총 상위 100 이익 모멘텀 · 미국 분기 실적 스냅샷",
    phase: "snapshot",
    dependencyLabel: "미국 분기 실적 스냅샷",
    dependencyReason: "분기 실적 스냅샷 수집은 SEC 신규 공시 수집보다 먼저 실행되어야 합니다.",
    dependencyType: "after_us_market_close",
    editable: true,
    mustRunBeforeId: "earnings-us-edgar-automatic.yml",
  },
  "earnings-us-edgar-automatic.yml": {
    id: "earnings-us-edgar-automatic.yml",
    scheduler: "github",
    displayName: "시총 상위 100 이익 모멘텀 · 미국 SEC 신규 공시",
    phase: "edgar",
    dependencyLabel: "SEC 신규 공시",
    dependencyReason: "SEC 신규 공시 수집은 분기 실적 스냅샷 수집 뒤에 실행되어야 합니다.",
    dependencyType: "after_previous_phase",
    editable: true,
    mustRunAfterId: "earnings-us-automatic.yml",
  },
  "earnings-v2-korea-automatic.yml": {
    id: "earnings-v2-korea-automatic.yml",
    scheduler: "github",
    displayName: "시총 상위 100 이익 모멘텀 · 한국 DART 공시",
    phase: "dart",
    allowedWeekdays: [1, 2, 3, 4, 5],
    dependencyLabel: "DART 공시",
    dependencyReason: "DART 공시 수집은 KIS 가격 수집보다 먼저 실행되어야 합니다.",
    dependencyType: "after_source_publication",
    editable: true,
    mustRunBeforeId: "earnings-v2-korea-kis-automatic.yml",
  },
  "earnings-v2-korea-kis-automatic.yml": {
    id: "earnings-v2-korea-kis-automatic.yml",
    scheduler: "github",
    displayName: "시총 상위 100 이익 모멘텀 · 한국 KIS 가격",
    phase: "kis",
    allowedWeekdays: [1, 2, 3, 4, 5],
    dependencyLabel: "한국 시장 가격",
    dependencyReason: "KIS 가격 수집은 DART 공시 수집 뒤에 실행되어야 합니다.",
    dependencyType: "after_previous_phase",
    editable: true,
    mustRunAfterId: "earnings-v2-korea-automatic.yml",
  },
  "sector-flow-open.yml": {
    id: "sector-flow-open.yml",
    scheduler: "supabase",
    displayName: "주도섹터 흐름 · 장초반",
    phase: "open",
    earliestSafeTimeKst: "09:10",
    allowedWeekdays: [1, 2, 3, 4, 5],
    dependencyLabel: "KOSPI 장초반 가격",
    dependencyReason: "정규장 개장 직후의 장초반 가격이 형성된 뒤 실행해야 합니다.",
    dependencyType: "fixed_safe_window",
    retryMinutes: 15,
    editable: true,
    mustRunBeforeId: "sector-flow-intraday.yml",
  },
  "sector-flow-intraday.yml": {
    id: "sector-flow-intraday.yml",
    scheduler: "supabase",
    displayName: "주도섹터 흐름 · 장중",
    phase: "intraday",
    earliestSafeTimeKst: "12:30",
    allowedWeekdays: [1, 2, 3, 4, 5],
    dependencyLabel: "KOSPI 장중 가격",
    dependencyReason: "기존 장중 수집 단계보다 이른 시각에는 실행하지 않습니다.",
    dependencyType: "fixed_safe_window",
    retryMinutes: 15,
    editable: true,
    mustRunAfterId: "sector-flow-open.yml",
    mustRunBeforeId: "sector-flow-close.yml",
  },
  "sector-flow-close.yml": {
    id: "sector-flow-close.yml",
    scheduler: "supabase",
    displayName: "주도섹터 흐름 · 종가",
    phase: "close",
    earliestSafeTimeKst: "15:40",
    allowedWeekdays: [1, 2, 3, 4, 5],
    dependencyLabel: "KOSPI 정규장 종가",
    dependencyReason: "15:30 정규장 종료 후 종가가 확정된 뒤 실행해야 합니다.",
    dependencyType: "after_kospi_close",
    retryMinutes: 15,
    editable: true,
    mustRunAfterId: "sector-flow-intraday.yml",
  },
};

export function schedulePolicy(id: string): SchedulePolicy | null {
  return POLICIES[id] ? { ...POLICIES[id] } : null;
}

export function timeMinutes(value: string): number {
  if (!/^\d{2}:\d{2}$/.test(value)) throw new Error("시간 형식이 올바르지 않습니다.");
  const [hour, minute] = value.split(":").map(Number);
  if (!Number.isInteger(hour) || !Number.isInteger(minute) || hour > 23 || minute > 59) {
    throw new Error("시간 형식이 올바르지 않습니다.");
  }
  return hour * 60 + minute;
}

export function assertScheduleTime(policy: SchedulePolicy | null, time: string) {
  const minutes = timeMinutes(time);
  if (!policy?.earliestSafeTimeKst) return;
  const earliest = timeMinutes(policy.earliestSafeTimeKst);
  if (minutes >= earliest) return;
  const reason = policy.dependencyReason || "이 작업의 원천데이터가 아직 확정되지 않은 시각입니다.";
  throw new Error(`${reason} ${policy.earliestSafeTimeKst} KST 이후 시간으로만 변경할 수 있습니다.`);
}

export function publicSchedulePolicy(policy: SchedulePolicy | null) {
  if (!policy) return null;
  return {
    scheduler: policy.scheduler,
    phase: policy.phase || null,
    earliest_safe_time_kst: policy.earliestSafeTimeKst || null,
    latest_recommended_time_kst: policy.latestRecommendedTimeKst || null,
    allowed_weekdays: policy.allowedWeekdays || null,
    dependency_label: policy.dependencyLabel || null,
    dependency_reason: policy.dependencyReason || null,
    dependency_type: policy.dependencyType || null,
    retry_minutes: policy.retryMinutes || null,
    editable: policy.editable !== false,
    must_run_before_id: policy.mustRunBeforeId || null,
    must_run_after_id: policy.mustRunAfterId || null,
  };
}
