export type SourceName = "yonhap" | "maekyung" | "financial_news";
export type ExtremeSignal = "decisive" | null;

export type ExtremeNewsRule = {
  id: string;
  signal: Exclude<ExtremeSignal, null>;
  phrase: string;
};

export type Candidate = {
  source: SourceName;
  itemHash: string;
  publishedAt: string | null;
  text: string;
  url: string | null;
};

export type ArticleSentiment = {
  itemHash: string;
  excludeFromIndex: boolean;
  sentiment: "positive" | "neutral" | "negative" | "uncertain";
  keywords: string[];
  uncertainSummary: string | null;
  extremeSignal: ExtremeSignal;
  extremeKeywords: string[];
};
