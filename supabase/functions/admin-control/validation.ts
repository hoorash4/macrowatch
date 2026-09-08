export const ADMIN_CARD_IDS = new Set([
  "member-management", "index-registry", "sector-registry", "news-analysis",
  "decisive-news", "uncertain-news", "policy-review", "target-collection",
  "earnings-v2-pending", "collection-errors", "integrations-backup",
]);

export function validateTimes(value: unknown) {
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

export function requiredText(value: unknown, label: string, maxLength: number) {
  const text = String(value || "").trim();
  if (!text || text.length > maxLength) {
    throw new Error(`${label}을(를) ${maxLength}자 이내로 입력해 주세요.`);
  }
  return text;
}

export function validateUsername(value: unknown) {
  const username = String(value || "").trim().toLowerCase();
  if (!/^[a-z0-9._-]{4,32}$/.test(username)) {
    throw new Error("아이디는 영문 소문자, 숫자, 마침표, 밑줄, 하이픈으로 4~32자여야 합니다.");
  }
  return username;
}

export function validatePassword(value: unknown) {
  const password = String(value || "");
  if (password.length < 6 || password.length > 72) {
    throw new Error("비밀번호는 6~72자로 입력해 주세요.");
  }
  return password;
}

export function internalEmail(username: string) {
  return `id-${username}@users.macrowatch.invalid`;
}

export function validateEtfTicker(value: unknown) {
  const ticker = requiredText(value, "ETF 코드", 6).toUpperCase();
  if (!/^[A-Z0-9]{6}$/.test(ticker)) throw new Error("ETF 코드는 영문 대문자와 숫자로 구성된 6자리여야 합니다.");
  return ticker;
}

export function validateSectorEtf(body: Record<string, unknown>) {
  return {
    sector_name: requiredText(body.sector_name, "섹터명", 80),
    etf_name: requiredText(body.etf_name, "ETF명", 120),
    etf_ticker: validateEtfTicker(body.etf_ticker),
    issuer: requiredText(body.issuer, "운용사", 80),
    is_active: true,
  };
}

export function validateNewSectorEtf(body: Record<string, unknown>) {
  return {
    sector_name: requiredText(body.sector_name, "섹터명", 80),
    etf_ticker: validateEtfTicker(body.etf_ticker),
  };
}

export function validateAdminCardOrder(value: unknown) {
  if (!Array.isArray(value)) throw new Error("관리 카드 순서가 올바르지 않습니다.");
  const order = value.map(String);
  if (order.length !== new Set(order).size || order.some((id) => !ADMIN_CARD_IDS.has(id))) {
    throw new Error("관리 카드 순서에 알 수 없는 항목이 있습니다.");
  }
  return order;
}

export function validateExtremeNewsRule(body: Record<string, unknown>) {
  return { signal: "decisive", phrase: requiredText(body?.phrase, "기준 문장", 300), is_active: true };
}
