from pathlib import Path

schema_path = Path('supabase/functions/_shared/pivot/pivot-schema.ts')
schema = schema_path.read_text()
schema = schema.replace('// Pivot schema v4: optional A/B post-trend parent structure for relationship scoring.\nexport const PIVOT_SCHEMA_VERSION = "pivot-schema-v4";', '// Pivot schema v5: required nullable post-trend field; A/B must carry parent post-trend.\nexport const PIVOT_SCHEMA_VERSION = "pivot-schema-v5";')
old = '''          post_trend: {\n            type: "object",\n            additionalProperties: false,\n            properties: {\n              direction: { type: "string", enum: ["up", "down", "sideways"] },\n              end_date: { type: "string", pattern: "^\\\\d{4}-\\\\d{2}-\\\\d{2}$" },\n            },\n            required: ["direction", "end_date"],\n          },\n        },\n        required: ["date", "value", "type", "grade", "direction", "reason", "confidence"],'''
new = '''          post_trend: {\n            anyOf: [\n              {\n                type: "object",\n                additionalProperties: false,\n                properties: {\n                  direction: { type: "string", enum: ["up", "down", "sideways"] },\n                  end_date: { type: "string", pattern: "^\\\\d{4}-\\\\d{2}-\\\\d{2}$" },\n                },\n                required: ["direction", "end_date"],\n              },\n              { type: "null" },\n            ],\n          },\n        },\n        required: ["date", "value", "type", "grade", "direction", "reason", "confidence", "post_trend"],'''
if old not in schema:
    raise SystemExit('post_trend schema anchor not found')
schema = schema.replace(old, new, 1)
schema = schema.replace('  post_trend?: PivotPostTrend;','  post_trend: PivotPostTrend | null;')
schema_path.write_text(schema)

index_path = Path('supabase/functions/pivot-ai/index.ts')
index = index_path.read_text()
reason_end = "- 부모 추세의 흐름을 바꾸지 못한 반대 방향 움직임은 자식 추세일 뿐이며 A의 근거가 될 수 없다.\n`;\n"
post_instruction = reason_end + '''const POST_TREND_INSTRUCTION = `\nA/B 피봇의 post_trend 출력 규칙은 반드시 지켜라.\n- A 또는 B에는 post_trend를 반드시 객체로 출력한다.\n- post_trend.direction은 up/down/sideways 중 하나다.\n- post_trend.end_date는 해당 피봇을 시작점으로 한 가장 상위의 단일 부모 추세가 구조적으로 끝난 날짜다.\n- 작은 조정, 반등, 짧은 횡보나 국소 파동으로 부모 추세를 끊지 마라.\n- C 또는 D에는 post_trend를 분석하지 말고 반드시 null을 출력한다.\n- post_trend 결과를 이용해 이미 결정한 A/B/C/D 등급이나 기존 regime을 다시 바꾸지 마라.\n`;\n'''
if 'const POST_TREND_INSTRUCTION' not in index:
    if reason_end not in index:
        raise SystemExit('reason instruction anchor not found')
    index = index.replace(reason_end, post_instruction, 1)

old_map = '''  const pivots = (raw.pivots || []).filter(item => validDate(item.date) && inRange(item.date)).map(item => {\n    const point = nearestPoint(points, item.date);\n    return { ...item, reason: String(item.reason || "").trim().slice(0, 400), date: point.date, value: point.value, confidence: Math.max(0, Math.min(1, Number(item.confidence))) };\n  });'''
new_map = '''  const pivots = (raw.pivots || []).filter(item => validDate(item.date) && inRange(item.date)).map(item => {\n    const point = nearestPoint(points, item.date);\n    const grade = String(item.grade || "").toUpperCase();\n    let postTrend = item.post_trend ?? null;\n    if (grade === "A" || grade === "B") {\n      if (!postTrend || typeof postTrend !== "object") throw new Error(`A/B post_trend가 없습니다: ${point.date}`);\n      const direction = String(postTrend.direction || "");\n      const endDate = String(postTrend.end_date || "");\n      if (!["up", "down", "sideways"].includes(direction) || !validDate(endDate) || endDate < point.date || endDate > to) {\n        throw new Error(`A/B post_trend가 올바르지 않습니다: ${point.date}`);\n      }\n      postTrend = { direction: direction as "up" | "down" | "sideways", end_date: endDate };\n    } else {\n      postTrend = null;\n    }\n    return { ...item, post_trend: postTrend, reason: String(item.reason || "").trim().slice(0, 400), date: point.date, value: point.value, confidence: Math.max(0, Math.min(1, Number(item.confidence))) };\n  });'''
if old_map not in index:
    raise SystemExit('normalize pivot map anchor not found')
index = index.replace(old_map, new_map, 1)
index = index.replace('      pivot_reason_instruction: PIVOT_REASON_INSTRUCTION,\n      anomaly_instruction: ANOMALY_WEB_INSTRUCTION,', '      pivot_reason_instruction: PIVOT_REASON_INSTRUCTION,\n      post_trend_instruction: POST_TREND_INSTRUCTION,\n      anomaly_instruction: ANOMALY_WEB_INSTRUCTION,', 1)
index = index.replace('prompt_cache_key: "macrowatch-pivot-analysis-v3"', 'prompt_cache_key: "macrowatch-pivot-analysis-v5"')
index_path.write_text(index)
