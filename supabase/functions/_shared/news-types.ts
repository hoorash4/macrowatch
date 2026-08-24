export type SourceName = "yonhap" | "hankyung" | "gdelt";

export type Candidate = {
  source: SourceName;
  itemHash: string;
  publishedAt: string | null;
  text: string;
  url: string | null;
};

export type AnalyzedEvent = {
  eventDate: string;
  eventAt: string | null;
  summary: string;
  category: "macro" | "finance" | "international";
  impactScope: "company" | "industry" | "market" | "systemic";
  transmissionChannels: string[];
  marketRelevance: number;
  shortTermImpact: "positive" | "neutral" | "negative" | "uncertain";
  fiveDayImpact: "positive" | "neutral" | "negative" | "uncertain";
  confidence: number;
  sourceItemHashes: string[];
};
