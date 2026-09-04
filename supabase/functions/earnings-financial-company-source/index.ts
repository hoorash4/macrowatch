import "jsr:@supabase/functions-js/edge-runtime.d.ts";

const FINANCIALS_URL = "https://apis.data.go.kr/1160100/service/GetFnCoFinaStatCredInfoService_V2/getFnCoSummFinaStat_V2";
const INCOME_STATEMENT_URL = "https://apis.data.go.kr/1160100/service/GetFnCoFinaStatCredInfoService_V2/getFnCoIs_V2";
const SECTOR_FINANCIAL_URLS: Record<string, string> = {
  bank: "https://apis.data.go.kr/1160100/service/GetDomeBankInfoService/getDomeBankFinaInfo",
  holding: "https://apis.data.go.kr/1160100/service/GetFinaHoldCompInfoService/getFinaHoldCompFinaInfo",
  life: "https://apis.data.go.kr/1160100/service/GetLifeInsuCompInfoService/getLifeInsuCompFinaInfo",
  nonlife: "https://apis.data.go.kr/1160100/service/GetNonlInsuCompInfoService/getNonlInsuCompFinaInfo",
  card: "https://apis.data.go.kr/1160100/service/GetCredCardCompInfoService/getCredCardCompFinaInfo",
  securities: "https://apis.data.go.kr/1160100/service/GetSecuCompInfoService/getSecuCompFinaInfo",
};
const JSON_HEADERS = { "Content-Type": "application/json; charset=utf-8" };

type SourceItem = Record<string, unknown>;

function json(body: unknown, status = 200, headers: HeadersInit = {}) {
  return new Response(JSON.stringify(body), { status, headers: { ...JSON_HEADERS, ...headers } });
}

class FinancialSourceError extends Error {
  constructor(
    readonly source: "basic-info" | "financials" | "income-statement" | "sector-financial",
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

function describePayload(payload: Record<string, unknown>) {
  const response = payload.response && typeof payload.response === "object"
    ? payload.response as Record<string, unknown>
    : null;
  const body = response?.body && typeof response.body === "object"
    ? response.body as Record<string, unknown>
    : null;
  const items = body?.items;
  const tableList = body?.tableList;
  const first = Array.isArray(items)
    ? items[0]
    : items && typeof items === "object"
      ? (Array.isArray((items as Record<string, unknown>).item)
        ? ((items as Record<string, unknown>).item as unknown[])[0]
        : (items as Record<string, unknown>).item)
      : null;
  const firstTable = Array.isArray(tableList)
    ? tableList[0]
    : tableList && typeof tableList === "object"
      ? (Array.isArray((tableList as Record<string, unknown>).table)
        ? ((tableList as Record<string, unknown>).table as unknown[])[0]
        : (tableList as Record<string, unknown>).table)
      : null;
  return {
    top_level_keys: Object.keys(payload),
    response_keys: response ? Object.keys(response) : [],
    body_keys: body ? Object.keys(body) : [],
    items_type: Array.isArray(items) ? "array" : typeof items,
    items_keys: items && typeof items === "object" && !Array.isArray(items) ? Object.keys(items) : [],
    first_item: first && typeof first === "object" ? first : null,
    table_list_type: Array.isArray(tableList) ? "array" : typeof tableList,
    table_list_keys: tableList && typeof tableList === "object" && !Array.isArray(tableList)
      ? Object.keys(tableList)
      : [],
    first_table: firstTable && typeof firstTable === "object" ? firstTable : null,
  };
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
  source: "basic-info" | "financials" | "income-statement" | "sector-financial",
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
    const mode = String(body.mode ?? "summary");
    const serviceKey = request.headers.get("X-Public-Data-API-Key")?.trim();
    if (!serviceKey) throw new Error("공공데이터 API 인증키가 전달되지 않았습니다.");

    if (mode === "sector_financial") {
      const sector = String(body.sector ?? "");
      const basYm = String(body.bas_ym ?? "");
      const title = String(body.title ?? "").trim();
      const requestedRows = Number(body.num_of_rows ?? 9999);
      const numOfRows = Number.isInteger(requestedRows) && requestedRows >= 1 && requestedRows <= 9999
        ? String(requestedRows)
        : "9999";
      const url = SECTOR_FINANCIAL_URLS[sector];
      if (!url || (basYm && !/^\d{6}$/.test(basYm))) {
        return json({ error: "지원 업종과 선택적인 YYYYMM 형식의 bas_ym이 필요합니다." }, 400);
      }
      const sectorPayload = await fetchSource(
        "sector-financial",
        url,
        serviceKey,
        { ...(basYm ? { basYm } : {}), ...(title ? { title } : {}) },
        numOfRows,
      );
      const rows = readItems(sectorPayload, "");
      return json({
        status: rows.length ? "ok" : "no_report",
        sector,
        basYm,
        row_count: rows.length,
        payload_shape: describePayload(sectorPayload),
        rows,
      });
    }

    const crno = String(body.crno ?? "").replaceAll(/\D/g, "");
    const fiscalYear = Number(body.fiscal_year);
    if (
      !/^\d{13}$/.test(crno)
      || !Number.isInteger(fiscalYear) || fiscalYear < 1900 || fiscalYear > 2100
    ) {
      return json({ error: "13자리 crno와 유효한 fiscal_year가 필요합니다." }, 400);
    }
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

