import "jsr:@supabase/functions-js/edge-runtime.d.ts";

const FINANCIALS_URL = "https://apis.data.go.kr/1160100/service/GetFnCoFinaStatCredInfoService_V2/getFnCoSummFinaStat_V2";
const INCOME_STATEMENT_URL = "https://apis.data.go.kr/1160100/service/GetFnCoFinaStatCredInfoService_V2/getFnCoIs_V2";
const JSON_HEADERS = { "Content-Type": "application/json; charset=utf-8" };

type SourceItem = Record<string, unknown>;

function json(body: unknown, status = 200, headers: HeadersInit = {}) {
  return new Response(JSON.stringify(body), { status, headers: { ...JSON_HEADERS, ...headers } });
}

class FinancialSourceError extends Error {
  constructor(
    readonly source: "basic-info" | "financials" | "income-statement",
    readonly reason: string,
    message: string,
  ) {
    super(message);
  }
}

function upstreamReason(raw: string, fallback: string) {
  try {
    const payload = JSON.parse(raw) as Record<string, unknown>;
    const serviceHeader = payload.OpenAPI_ServiceResponse && typeof payload.OpenAPI_ServiceResponse === "object"
      ? (payload.OpenAPI_ServiceResponse as Record<string, unknown>).cmmMsgHeader
      : null;
    const error = serviceHeader && typeof serviceHeader === "object"
      ? String((serviceHeader as Record<string, unknown>).returnReasonCode ?? "")
      : sourceError(payload) ?? "";
    if (/^[A-Za-z0-9_-]{1,60}$/.test(error)) return error;
  } catch {
    // JSON 오류 응답이 아니면 상태 기반 이유만 기록한다.
  }
  return fallback;
}

function isFinancialSourceRequest(request: Request): boolean {
  const internalToken = Deno.env.get("EARNINGS_FINANCIAL_SOURCE_TOKEN");
  return Boolean(internalToken) && request.headers.get("Authorization") === `Bearer ${internalToken}`;
}

function readItems(payload: Record<string, unknown>, bodyKey: string): SourceItem[] {
  const response = payload.response && typeof payload.response === "object"
    ? payload.response as Record<string, unknown>
    : null;
  const body = response?.body && typeof response.body === "object"
    ? response.body as Record<string, unknown>
    : payload[bodyKey] && typeof payload[bodyKey] === "object"
      ? payload[bodyKey] as Record<string, unknown>
      : null;
  const items = body?.items && typeof body.items === "object"
    ? body.items as Record<string, unknown>
    : null;
  const item = items?.item;
  if (Array.isArray(item)) return item.filter((row): row is SourceItem => Boolean(row) && typeof row === "object");
  return item && typeof item === "object" ? [item as SourceItem] : [];
}

function sourceError(payload: Record<string, unknown>) {
  const response = payload.response && typeof payload.response === "object"
    ? payload.response as Record<string, unknown>
    : null;
  const header = response?.header && typeof response.header === "object"
    ? response.header as Record<string, unknown>
    : Object.values(payload).find((value): value is Record<string, unknown> => (
      Boolean(value) && typeof value === "object" && "resultCode" in value
    ));
  const code = String(header?.resultCode ?? "");
  return code && code !== "00" && code !== "000" ? code : null;
}

async function fetchSource(
  source: "basic-info" | "financials" | "income-statement",
  url: string,
  serviceKey: string,
  params: Record<string, string>,
  numOfRows = "100",
): Promise<Record<string, unknown>> {
  // 포털 키가 이미 인코딩된 형태여도 한 번만 인코딩해 전달한다.
  const query = new URLSearchParams({
    serviceKey: decodeURIComponent(serviceKey),
    resultType: "json",
    pageNo: "1",
    numOfRows,
    ...params,
  });
  const response = await fetch(`${url}?${query}`, { signal: AbortSignal.timeout(25_000) });
  const raw = await response.text();
  if (!response.ok) {
    throw new FinancialSourceError(
      source,
      upstreamReason(raw, `UPSTREAM_HTTP_${response.status}`),
      `금융위원회 API HTTP ${response.status}`,
    );
  }
  let payload: Record<string, unknown>;
  try {
    payload = JSON.parse(raw) as Record<string, unknown>;
  } catch {
    throw new FinancialSourceError(source, "INVALID_JSON", "금융위원회 API JSON 응답 오류");
  }
  const error = sourceError(payload);
  if (error) throw new FinancialSourceError(source, error, `금융위원회 API 응답 오류 (${error})`);
  return payload;
}

Deno.serve(async (request) => {
  if (request.method !== "POST") return json({ error: "POST 요청만 허용됩니다." }, 405);
  if (!isFinancialSourceRequest(request)) return json({ error: "서버 호출만 허용됩니다." }, 401);
  try {
    const body = await request.json() as Record<string, unknown>;
    const crno = String(body.crno ?? "").replaceAll(/\D/g, "");
    const fiscalYear = Number(body.fiscal_year);
    if (
      !/^\d{13}$/.test(crno)
      || !Number.isInteger(fiscalYear) || fiscalYear < 1900 || fiscalYear > 2100
    ) {
      return json({ error: "13자리 crno와 유효한 fiscal_year·fiscal_quarter가 필요합니다." }, 400);
    }
    const serviceKey = request.headers.get("X-Public-Data-API-Key")?.trim();
    if (!serviceKey) throw new Error("공공데이터 API 인증키가 전달되지 않았습니다.");

    const mode = String(body.mode ?? "summary");
    if (mode === "income_statement") {
      const incomePayload = await fetchSource("income-statement", INCOME_STATEMENT_URL, serviceKey, {
        crno,
        bizYear: String(fiscalYear),
      }, "9999");
      const accounts = readItems(incomePayload, "FnCoIs_body").map((item) => ({
        basDt: item.basDt,
        crno: item.crno,
        bizYear: item.bizYear,
        fnclDcd: item.fnclDcd,
        fnclDcdNm: item.fnclDcdNm,
        acitId: item.acitId,
        acitNm: item.acitNm,
        thqrAcitAmt: item.thqrAcitAmt,
        crtmAcitAmt: item.crtmAcitAmt,
        lsqtAcitAmt: item.lsqtAcitAmt,
        pvtrAcitAmt: item.pvtrAcitAmt,
        bpvtrAcitAmt: item.bpvtrAcitAmt,
        curCd: item.curCd,
      }));
      return json(accounts.length ? { status: "ok", crno, accounts } : { status: "no_report", crno });
    }
    if (mode !== "summary") return json({ error: "지원하지 않는 조회 모드입니다." }, 400);

    const financialPayload = await fetchSource("financials", FINANCIALS_URL, serviceKey, {
      crno,
      bizYear: String(fiscalYear),
    });
    const reports = readItems(financialPayload, "FnCoSummFinaStat_body").map((item) => ({
      rptCd: item.rptCd,
      rptCdNm: item.rptCdNm,
      fnclDcd: item.fnclDcd,
      fnclDcdNm: item.fnclDcdNm,
      fncoSaleAmt: item.fncoSaleAmt,
      fncoBzopPft: item.fncoBzopPft,
      fncoCrtmNpf: item.fncoCrtmNpf,
      curCd: item.curCd,
    }));
    return json(reports.length ? { status: "ok", crno, reports } : { status: "no_report", crno });
  } catch (error) {
    const source = error instanceof FinancialSourceError ? error.source : "runtime";
    const reason = error instanceof FinancialSourceError ? error.reason : "RUNTIME_ERROR";
    return json(
      { error: error instanceof Error ? error.message : "금융위원회 원자료 조회 실패" },
      502,
      { "X-Financial-Source-Stage": source, "X-Financial-Source-Reason": reason },
    );
  }
});

