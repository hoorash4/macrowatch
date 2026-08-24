export type SourceName = "yonhap" | "maekyung";

export type Candidate = {
  source: SourceName;
  itemHash: string;
  publishedAt: string | null;
  text: string;
  url: string | null;
};

export type ArticleSentiment = {
  itemHash: string;
  sentiment: "positive" | "neutral" | "negative" | "uncertain";
  keywords: string[];
  uncertainSummary: string | null;
};
