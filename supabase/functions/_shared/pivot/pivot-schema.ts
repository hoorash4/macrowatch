// Pivot schema v4: A/B post-trend structure is optional during prompt rollout and used when present.
export const PIVOT_SCHEMA_VERSION = "pivot-schema-v4";

export const PIVOT_ANALYSIS_SCHEMA = {
  type: "object",
  additionalProperties: false,
  properties: {
    regimes: {
      type: "array",
      items: {
        type: "object",
        additionalProperties: false,
        properties: {
          type: { type: "string", enum: ["uptrend", "downtrend", "sideways"] },
          start_date: { type: "string", pattern: "^\\d{4}-\\d{2}-\\d{2}$" },
          end_date: {
            anyOf: [
              { type: "string", pattern: "^\\d{4}-\\d{2}-\\d{2}$" },
              { type: "null" },
            ],
          },
          confidence: { type: "number", minimum: 0, maximum: 1 },
        },
        required: ["type", "start_date", "end_date", "confidence"],
      },
    },
    pivots: {
      type: "array",
      items: {
        type: "object",
        additionalProperties: false,
        properties: {
          date: { type: "string", pattern: "^\\d{4}-\\d{2}-\\d{2}$" },
          value: { type: "number" },
          type: {
            type: "string",
            enum: [
              "major_reversal",
              "sideways_entry",
              "sideways_exit",
              "local_reversal",
              "slope_change",
            ],
          },
          grade: { type: "string", enum: ["A", "B", "C", "D"] },
          direction: { type: "string", enum: ["high", "low", "neutral"] },
          reason: { type: "string", minLength: 1, maxLength: 400 },
          confidence: { type: "number", minimum: 0, maximum: 1 },
          post_trend: {
            anyOf: [
              {
                type: "object",
                additionalProperties: false,
                properties: {
                  direction: { type: "string", enum: ["up", "down", "sideways"] },
                  end_date: { type: "string", pattern: "^\\d{4}-\\d{2}-\\d{2}$" },
                },
                required: ["direction", "end_date"],
              },
              { type: "null" },
            ],
          },
        },
        required: ["date", "value", "type", "grade", "direction", "reason", "confidence", "post_trend"],
      },
    },
    anomalies: {
      type: "array",
      items: {
        type: "object",
        additionalProperties: false,
        properties: {
          movement_start_date: { type: "string", pattern: "^\\d{4}-\\d{2}-\\d{2}$" },
          date: { type: "string", pattern: "^\\d{4}-\\d{2}-\\d{2}$" },
          value: { type: "number" },
          type: { type: "string", enum: ["spike_up", "spike_down"] },
          event_date: { type: "string", pattern: "^\\d{4}-\\d{2}-\\d{2}$" },
          event_name: { type: "string", minLength: 1 },
          reason: { type: "string", minLength: 1 },
          unexpected_event: { type: "boolean", enum: [true] },
          search_window_start: { type: "string", pattern: "^\\d{4}-\\d{2}-\\d{2}$" },
          search_window_end: { type: "string", pattern: "^\\d{4}-\\d{2}-\\d{2}$" },
          source_urls: {
            type: "array",
            minItems: 1,
            maxItems: 5,
            items: { type: "string", minLength: 1 },
          },
          confidence: { type: "number", minimum: 0, maximum: 1 },
        },
        required: [
          "movement_start_date",
          "date",
          "value",
          "type",
          "event_date",
          "event_name",
          "reason",
          "unexpected_event",
          "search_window_start",
          "search_window_end",
          "source_urls",
          "confidence"
        ],
      },
    },
  },
  required: ["regimes", "pivots", "anomalies"],
} as const;

export type PivotRegime = {
  type: "uptrend" | "downtrend" | "sideways";
  start_date: string;
  end_date: string | null;
  confidence: number;
};

export type PivotPostTrend = {
  direction: "up" | "down" | "sideways";
  end_date: string;
};

export type PivotPoint = {
  date: string;
  value: number;
  type: "major_reversal" | "sideways_entry" | "sideways_exit" | "local_reversal" | "slope_change";
  grade: "A" | "B" | "C" | "D";
  direction: "high" | "low" | "neutral";
  reason: string;
  confidence: number;
  post_trend: PivotPostTrend | null;
};

export type PivotAnomaly = {
  movement_start_date: string;
  date: string;
  value: number;
  type: "spike_up" | "spike_down";
  event_date: string;
  event_name: string;
  reason: string;
  unexpected_event: true;
  search_window_start: string;
  search_window_end: string;
  source_urls: string[];
  confidence: number;
};

export type PivotAnalysisOutput = {
  regimes: PivotRegime[];
  pivots: PivotPoint[];
  anomalies: PivotAnomaly[];
};
