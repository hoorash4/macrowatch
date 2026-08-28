import { unzipSync } from "npm:fflate@0.8.2";

const KIS_MASTER_URLS = {
  KOSPI: "https://new.real.download.dws.co.kr/common/master/kospi_code.mst.zip",
  KOSDAQ: "https://new.real.download.dws.co.kr/common/master/kosdaq_code.mst.zip",
} as const;
// SEC's Akamai edge rejects some serverless egress ranges even with the
// required identifying User-Agent. This repository is an automated daily
// mirror of the SEC ticker/CIK mapping, allowing ingestion to remain
// deterministic while the financial facts themselves still come from SEC.
const SEC_TICKERS_URL = "https://raw.githubusercontent.com/jadchaar/sec-cik-mapper/main/mappings/stocks/mappings.csv";
const NASDAQ_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt";
const SP500_CONSTITUENTS_URL = "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv";
const PUBLIC_DATA_USER_AGENT = "MacroWatch hoorash4@users.noreply.github.com";
const WON_PER_MASTER_MARKET_CAP_UNIT = 100_000_000;

export type KoreanMarketCapRow = {
  ticker: string;
  name: string;
  rank: number;
  marketCap: number;
  exchange: "KOSPI" | "KOSDAQ";
};

export type UsListedCompany = {
  ticker: string;
  name: string;
  cik: string;
  exchange: string;
};

type MasterLayout = {
  tailLength: number;
  widths: number[];
  etpIndex: number;
  spacIndex: number;
  preferredIndex: number;
  marketCapIndex: number;
};

const MASTER_LAYOUTS: Record<"KOSPI" | "KOSDAQ", MasterLayout> = {
  KOSPI: {
    // KIS Python examples count the trailing newline as the 228th byte.
    // splitLines removes it, leaving 227 bytes of fixed-width fields.
    tailLength: 227,
    widths: [
      2, 1, 4, 4, 4, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1,
      1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 9, 5, 5, 1, 1, 1, 2, 1, 1,
      1, 2, 2, 2, 3, 1, 3, 12, 12, 8, 15, 21, 2, 7, 1, 1, 1, 1, 1, 9,
      9, 9, 5, 9, 8, 9, 3, 1, 1, 1,
    ],
    etpIndex: 12,
    spacIndex: 19,
    preferredIndex: 54,
    marketCapIndex: 65,
  },
  KOSDAQ: {
    tailLength: 221,
    widths: [
      2, 1, 4, 4, 4, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1,
      1, 1, 1, 1, 1, 1, 9, 5, 5, 1, 1, 1, 2, 1, 1, 1, 2, 2, 2, 3,
      1, 3, 12, 12, 8, 15, 21, 2, 7, 1, 1, 1, 1, 9, 9, 9, 5, 9, 8,
      9, 3, 1, 1, 1,
    ],
    etpIndex: 8,
    spacIndex: 14,
    preferredIndex: 49,
    marketCapIndex: 60,
  },
};

function splitLines(content: Uint8Array) {
  const lines: Uint8Array[] = [];
  let start = 0;
  for (let index = 0; index <= content.length; index += 1) {
    if (index !== content.length && content[index] !== 10) continue;
    let end = index;
    if (end > start && content[end - 1] === 13) end -= 1;
    if (end > start) lines.push(content.slice(start, end));
    start = index + 1;
  }
  return lines;
}

function fieldBytes(tail: Uint8Array, widths: number[], fieldIndex: number) {
  let offset = 0;
  for (let index = 0; index < fieldIndex; index += 1) offset += widths[index];
  return tail.slice(offset, offset + widths[fieldIndex]);
}

function decode(bytes: Uint8Array) {
  return new TextDecoder("euc-kr").decode(bytes).trim();
}

function normalizeUsTicker(value: string) {
  return value.trim().toUpperCase().replaceAll(".", "-").replaceAll("/", "-");
}

function parseCsvLine(line: string) {
  const fields: string[] = [];
  let value = "";
  let quoted = false;
  for (let index = 0; index < line.length; index += 1) {
    const char = line[index];
    if (char === '"') {
      if (quoted && line[index + 1] === '"') {
        value += '"';
        index += 1;
      } else quoted = !quoted;
    } else if (char === "," && !quoted) {
      fields.push(value.trim());
      value = "";
    } else value += char;
  }
  fields.push(value.trim());
  return fields;
}

async function fetchPublicText(url: string) {
  const response = await fetch(url, {
    headers: { "User-Agent": PUBLIC_DATA_USER_AGENT },
    signal: AbortSignal.timeout(45_000),
  });
  if (!response.ok) throw new Error(`공개 종목 기준자료 조회 실패 (${response.status})`);
  return await response.text();
}

/** SEC CIK is the durable company identity shared by both U.S. universes. */
export async function fetchSecListedCompanies() {
  const lines = (await fetchPublicText(SEC_TICKERS_URL)).split(/\r?\n/).filter(Boolean);
  const fields = parseCsvLine(lines.shift() || "");
  const indexes = Object.fromEntries(fields.map((field, index) => [field.toLowerCase(), index]));
  const result = new Map<string, UsListedCompany>();
  for (const line of lines) {
    const values = parseCsvLine(line);
    const ticker = normalizeUsTicker(values[indexes.ticker] || "");
    const cikNumber = Number(values[indexes.cik]);
    const name = (values[indexes.name] || "").trim();
    const exchange = (values[indexes.exchange] || "").trim().toUpperCase();
    if (!ticker || !name || !Number.isInteger(cikNumber) || cikNumber <= 0) continue;
    result.set(ticker, {
      ticker,
      name,
      cik: String(cikNumber).padStart(10, "0"),
      exchange,
    });
  }
  return result;
}

/** Nasdaq's official directory supplies an explicit ETF flag. */
export async function fetchNasdaqOperatingSymbols() {
  const lines = (await fetchPublicText(NASDAQ_LISTED_URL)).split(/\r?\n/).filter(Boolean);
  const headers = (lines.shift() || "").split("|");
  const symbolIndex = headers.indexOf("Symbol");
  const testIndex = headers.indexOf("Test Issue");
  const etfIndex = headers.indexOf("ETF");
  const nextSharesIndex = headers.indexOf("NextShares");
  if ([symbolIndex, testIndex, etfIndex].some((index) => index < 0)) {
    throw new Error("Nasdaq 종목 디렉터리 형식이 예상과 다릅니다.");
  }
  const symbols = new Set<string>();
  for (const line of lines) {
    if (line.startsWith("File Creation Time")) continue;
    const values = line.split("|");
    if (values[testIndex] !== "N" || values[etfIndex] !== "N") continue;
    if (nextSharesIndex >= 0 && values[nextSharesIndex] === "Y") continue;
    const ticker = normalizeUsTicker(values[symbolIndex] || "");
    if (ticker) symbols.add(ticker);
  }
  return symbols;
}

/** Public S&P 500 membership data; CIK prevents duplicate share classes. */
export async function fetchSp500Companies() {
  const lines = (await fetchPublicText(SP500_CONSTITUENTS_URL)).split(/\r?\n/).filter(Boolean);
  const headers = parseCsvLine(lines.shift() || "");
  const symbolIndex = headers.indexOf("Symbol");
  const nameIndex = headers.indexOf("Security");
  const cikIndex = headers.indexOf("CIK");
  if ([symbolIndex, nameIndex, cikIndex].some((index) => index < 0)) {
    throw new Error("S&P 500 구성종목 자료 형식이 예상과 다릅니다.");
  }
  const companies = new Map<string, UsListedCompany>();
  for (const line of lines) {
    const values = parseCsvLine(line);
    const ticker = normalizeUsTicker(values[symbolIndex] || "");
    const cikNumber = Number(values[cikIndex]);
    const name = String(values[nameIndex] || "").trim();
    if (!ticker || !name || !Number.isInteger(cikNumber) || cikNumber <= 0) continue;
    companies.set(ticker, {
      ticker,
      name,
      cik: String(cikNumber).padStart(10, "0"),
      exchange: "US",
    });
  }
  return companies;
}

export function normalizedUsTicker(value: string) {
  return normalizeUsTicker(value);
}

function isTruthyFlag(value: string) {
  return new Set(["Y", "1"]).has(value.toUpperCase());
}

function hasClassification(value: string) {
  return !new Set(["", "0", "N"]).has(value.toUpperCase());
}

export function parseKisKoreanMaster(
  content: Uint8Array,
  exchange: "KOSPI" | "KOSDAQ",
): KoreanMarketCapRow[] {
  const layout = MASTER_LAYOUTS[exchange];
  const rows: KoreanMarketCapRow[] = [];
  for (const line of splitLines(content)) {
    if (line.length <= layout.tailLength + 21) continue;
    const head = line.slice(0, line.length - layout.tailLength);
    const tail = line.slice(line.length - layout.tailLength);
    let ticker = decode(head.slice(0, 9));
    if (ticker.length > 6) ticker = ticker.slice(-6);
    const name = decode(head.slice(21));
    const etp = decode(fieldBytes(tail, layout.widths, layout.etpIndex));
    const spac = decode(fieldBytes(tail, layout.widths, layout.spacIndex));
    const preferred = decode(fieldBytes(tail, layout.widths, layout.preferredIndex));
    const marketCapUnits = Number(decode(fieldBytes(tail, layout.widths, layout.marketCapIndex)));
    if (!/^\d{6}$/.test(ticker) || !name || !Number.isFinite(marketCapUnits) || marketCapUnits < 0) continue;
    // ETP is a classification code rather than a boolean flag. KIS currently
    // marks ETFs with "2", so checking only Y/1 would incorrectly admit large
    // ETFs into the operating-company market-cap universe.
    if (hasClassification(etp) || isTruthyFlag(spac) || hasClassification(preferred)) continue;
    rows.push({ ticker, name, rank: 0, marketCap: marketCapUnits * WON_PER_MASTER_MARKET_CAP_UNIT, exchange });
  }
  return rows;
}

export async function fetchKoreanMarketCapUniverse(
  market: "KOSPI" | "KOSDAQ",
  limit: number,
): Promise<KoreanMarketCapRow[]> {
  const response = await fetch(KIS_MASTER_URLS[market], {
    headers: { "User-Agent": "MacroWatch/1.0 earnings-universe collector" },
    signal: AbortSignal.timeout(45_000),
  });
  if (!response.ok) throw new Error(`KIS ${market} 종목 마스터 조회 실패 (${response.status})`);
  const files = unzipSync(new Uint8Array(await response.arrayBuffer()));
  const content = Object.values(files)[0];
  if (!content) throw new Error(`KIS ${market} 종목 마스터 압축파일이 비어 있습니다.`);
  const result = parseKisKoreanMaster(content, market)
    .sort((a, b) => b.marketCap - a.marketCap || a.ticker.localeCompare(b.ticker))
    .slice(0, limit)
    .map((row, index) => ({ ...row, rank: index + 1 }));
  if (result.length !== limit) {
    throw new Error(`KIS ${market} 보통주 시가총액 종목이 ${result.length}/${limit}개만 확인되었습니다.`);
  }
  return result;
}
