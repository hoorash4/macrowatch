import "jsr:@supabase/functions-js/edge-runtime.d.ts";

const BASIC_INFO_URL = "https://apis.data.go.kr/1160100/service/GetFnCoBasiInfoService/getFnCoOutl";
const FINANCIALS_URL = "https://apis.data.go.kr/1160100/service/GetFnCoFinaStatCredInfoService_V2/getFnCoSummFinaStat_V2";
const JSON_HEADERS = { "Content-Type": "application/json; charset=utf-8" };

type SourceItem = Record<string, unknown>;

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: JSON_HEADERS });
}

function isFinancialSourceRequest(request: Request): boolean {
  const internalToken = Deno.env.get("EARNINGS_FINANCIAL_SOURCE_TOKEN");
  return Boolean(internalToken) && request.headers.get("Authorization") === `Bearer ${internalToken}`;
}

function compactName(value: unknown) {
  return String(value ?? "")
    .replaceAll("주식회사", "")
    .replaceAll("(주)", "")
    .replaceAll("㈜", "")
    .replaceAll(/\s+/g, "")
    .trim();
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
  url: string,
  serviceKey: string,
  params: Record<string, string>,
): Promise<Record<string, unknown>> {
  // 포털 키가 이미 인코딩된 형태여도 한 번만 인코딩해 전달한다.
  const query = new URLSearchParams({
    serviceKey: decodeURIComponent(serviceKey),
    resultType: "json",
    pageNo: "1",
    numOfRows: "100",
    ...params,
  });
  const response = await fetch(`${url}?${query}`, { signal: AbortSignal.timeout(25_000) });
  if (!response.ok) throw new Error(`금융위원회 API HTTP ${response.status}`);
  const payload = await response.json() as Record<string, unknown>;
  const error = sourceError(payload);
  if (error) throw new Error(`금융위원회 API 응답 오류 (${error})`);
  return payload;
}

function chooseCompany(items: SourceItem[], companyName: string): SourceItem | null | "ambiguous" {
  const target = compactName(companyName);
  const exact = items.filter((item) => compactName(item.fncoNm) === target);
  if (exact.length === 1) return exact[0];
  if (exact.length > 1) return "ambiguous";
  // 금융위 기본정보의 법인명은 상장 표기보다 긴 경우가 있으므로, 유일한
  // 포함 일치만 허용한다. 여러 후보는 추측하지 않고 대기로 남긴다.
  const contained = items.filter((item) => {
    const candidate = compactName(item.fncoNm);
    return candidate.includes(target) || target.includes(candidate);
  });
  return contained.length === 1 ? contained[0] : contained.length > 1 ? "ambiguous" : null;
}

Deno.serve(async (request) => {
  if (request.method !== "POST") return json({ error: "POST 요청만 허용됩니다." }, 405);
  if (!isFinancialSourceRequest(request)) return json({ error: "서버 호출만 허용됩니다." }, 401);
  try {
    const body = await request.json() as Record<string, unknown>;
    const companyName = String(body.company_name ?? "").trim();
    const fiscalYear = Number(body.fiscal_year);
    if (!companyName || !Number.isInteger(fiscalYear) || fiscalYear < 1900 || fiscalYear > 2100) {
      return json({ error: "company_name과 유효한 fiscal_year가 필요합니다." }, 400);
    }
    const serviceKey = Deno.env.get("PUBLIC_DATA_API_KEY");
    if (!serviceKey) throw new Error("PUBLIC_DATA_API_KEY가 설정되지 않았습니다.");

    const basicPayload = await fetchSource(BASIC_INFO_URL, serviceKey, { fncoNm: companyName });
    const company = chooseCompany(readItems(basicPayload, "FnCoBasiInfo_body"), companyName);
    if (company === "ambiguous") return json({ status: "ambiguous" });
    const crno = String(company?.crno ?? "").replaceAll(/\D/g, "");
    if (!company || crno.length !== 13) return json({ status: "not_found" });

    const financialPayload = await fetchSource(FINANCIALS_URL, serviceKey, {
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
    return json({ error: error instanceof Error ? error.message : "금융위원회 원자료 조회 실패" }, 502);
  }
});

