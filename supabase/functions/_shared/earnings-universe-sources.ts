import { unzipSync } from "npm:fflate@0.8.2";

const OPEN_DART_CORP_CODE_URL = "https://opendart.fss.or.kr/api/corpCode.xml";
const NAVER_MARKET_CAP_URL = "https://finance.naver.com/sise/sise_market_sum.naver";
const WON_PER_NAVER_MARKET_CAP_UNIT = 100_000_000;

export type KoreanListedCompany = { ticker: string; corpCode: string; name: string };
export type KoreanMarketCapRow = KoreanListedCompany & {
  rank: number;
  marketCap: number;
  exchange: "KOSPI" | "KOSDAQ";
};

function requiredSecret(name: string) {
  const value = Deno.env.get(name)?.trim();
  if (!value) throw new Error(`${name}가 설정되지 않았습니다.`);
  return value;
}

function decodeHtml(value: string) {
  return value
    .replace(/<[^>]*>/g, "")
    .replace(/&nbsp;/gi, " ")
    .replace(/&amp;/gi, "&")
    .replace(/&quot;/gi, '"')
    .replace(/&#39;|&#x27;/gi, "'")
    .replace(/&lt;/gi, "<")
    .replace(/&gt;/gi, ">")
    .replace(/&#(\d+);/g, (_match, code) => String.fromCodePoint(Number(code)))
    .trim();
}

function numericCell(value: string) {
  const parsed = Number(decodeHtml(value).replaceAll(",", ""));
  return Number.isFinite(parsed) ? parsed : null;
}

export function parseOpenDartCorpCodeXml(xml: string): Map<string, KoreanListedCompany> {
  const companies = new Map<string, KoreanListedCompany>();
  for (const match of xml.matchAll(/<list>([\s\S]*?)<\/list>/g)) {
    const block = match[1];
    const corpCode = block.match(/<corp_code>(\d{8})<\/corp_code>/)?.[1] || "";
    const ticker = block.match(/<stock_code>\s*(\d{6})\s*<\/stock_code>/)?.[1] || "";
    const name = decodeHtml(block.match(/<corp_name>([\s\S]*?)<\/corp_name>/)?.[1] || "");
    if (corpCode && ticker && name) companies.set(ticker, { ticker, corpCode, name });
  }
  if (companies.size < 1_000) throw new Error(`OpenDART 상장법인 매핑이 비정상적으로 적습니다: ${companies.size}개`);
  return companies;
}

export async function fetchOpenDartListedCompanies(): Promise<Map<string, KoreanListedCompany>> {
  const apiKey = requiredSecret("OPENDART_API_KEY");
  const url = new URL(OPEN_DART_CORP_CODE_URL);
  url.searchParams.set("crtfc_key", apiKey);
  const response = await fetch(url, { signal: AbortSignal.timeout(45_000) });
  if (!response.ok) throw new Error(`OpenDART 법인코드 조회 실패 (${response.status})`);
  const archive = unzipSync(new Uint8Array(await response.arrayBuffer()));
  const xmlBytes = archive["CORPCODE.xml"] || archive["corpCode.xml"];
  if (!xmlBytes) throw new Error("OpenDART 법인코드 압축파일에 CORPCODE.xml이 없습니다.");
  return parseOpenDartCorpCodeXml(new TextDecoder("utf-8").decode(xmlBytes));
}

export function parseNaverMarketCapHtml(
  html: string,
  exchange: "KOSPI" | "KOSDAQ",
  listedCompanies: Map<string, KoreanListedCompany>,
): KoreanMarketCapRow[] {
  const rows: KoreanMarketCapRow[] = [];
  for (const match of html.matchAll(/<tr[^>]*>([\s\S]*?)<\/tr>/gi)) {
    const row = match[1];
    const ticker = row.match(/\/item\/main\.naver\?code=(\d{6})[^>]*class="tltle"/i)?.[1] || "";
    const listed = listedCompanies.get(ticker);
    if (!listed) continue; // Excludes ETFs, ETNs, preferred shares, and other non-corporate rows.
    const displayedName = decodeHtml(row.match(/class="tltle"[^>]*>([\s\S]*?)<\/a>/i)?.[1] || "");
    const cells = [...row.matchAll(/<td[^>]*class="number"[^>]*>([\s\S]*?)<\/td>/gi)]
      .map((cell) => numericCell(cell[1]));
    // Default table: current price, change, change %, par value, market cap.
    const marketCapInHundredMillionWon = cells[4];
    if (!displayedName || marketCapInHundredMillionWon === null || marketCapInHundredMillionWon < 0) continue;
    rows.push({
      ticker,
      corpCode: listed.corpCode,
      name: displayedName,
      rank: 0,
      marketCap: marketCapInHundredMillionWon * WON_PER_NAVER_MARKET_CAP_UNIT,
      exchange,
    });
  }
  return rows;
}

export async function fetchKoreanMarketCapUniverse(
  market: "KOSPI" | "KOSDAQ",
  limit: number,
  listedCompanies: Map<string, KoreanListedCompany>,
): Promise<KoreanMarketCapRow[]> {
  const sosok = market === "KOSPI" ? "0" : "1";
  const collected = new Map<string, KoreanMarketCapRow>();
  for (let page = 1; page <= 6 && collected.size < limit; page += 1) {
    const url = new URL(NAVER_MARKET_CAP_URL);
    url.searchParams.set("sosok", sosok);
    url.searchParams.set("page", String(page));
    const response = await fetch(url, {
      headers: { "User-Agent": "MacroWatch/1.0 earnings-universe collector" },
      signal: AbortSignal.timeout(30_000),
    });
    if (!response.ok) throw new Error(`국내 전체 시가총액표 조회 실패 (${response.status})`);
    const html = new TextDecoder("euc-kr").decode(await response.arrayBuffer());
    parseNaverMarketCapHtml(html, market, listedCompanies).forEach((row) => collected.set(row.ticker, row));
  }
  const result = [...collected.values()]
    .sort((a, b) => b.marketCap - a.marketCap || a.ticker.localeCompare(b.ticker))
    .slice(0, limit)
    .map((row, index) => ({ ...row, rank: index + 1 }));
  if (result.length !== limit) {
    throw new Error(`${market} 시가총액 상장기업이 ${result.length}/${limit}개만 확인되었습니다.`);
  }
  return result;
}
