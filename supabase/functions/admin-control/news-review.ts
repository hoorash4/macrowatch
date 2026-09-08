
export async function refreshArticleSentiment(admin: any, articleDate: string) {
  const { data, error } = await admin.from("news_article_sentiments")
    .select("ai_sentiment,admin_sentiment").eq("article_date", articleDate);
  if (error) throw error;
  const counts = { positive: 0, negative: 0, neutral: 0, uncertain: 0 };
  for (const row of data || []) counts[(row.admin_sentiment || row.ai_sentiment) as keyof typeof counts] += 1;
  const { error: upsertError } = await admin.from("news_daily_article_sentiment").upsert({
    article_date: articleDate, positive_count: counts.positive, negative_count: counts.negative,
    neutral_count: counts.neutral, uncertain_count: counts.uncertain,
    analyzed_article_count: (data || []).length, generated_at: new Date().toISOString(),
  });
  if (upsertError) throw upsertError;
}

export async function excludeUncertainArticle(admin: any, id: string) {
  const { data, error } = await admin.from("news_article_sentiments")
    .delete()
    .eq("id", id).eq("ai_sentiment", "uncertain").is("admin_sentiment", null)
    .select("article_date").maybeSingle();
  if (error) throw error;
  if (!data) throw new Error("이미 처리되었거나 존재하지 않는 항목입니다.");

  const { data: daily, error: dailyError } = await admin.from("news_daily_article_sentiment")
    .select("excluded_count").eq("article_date", data.article_date).maybeSingle();
  if (dailyError) throw dailyError;

  await refreshArticleSentiment(admin, data.article_date);
  const { error: excludedError } = await admin.from("news_daily_article_sentiment").update({
    excluded_count: (daily?.excluded_count || 0) + 1,
    generated_at: new Date().toISOString(),
  }).eq("article_date", data.article_date);
  if (excludedError) throw excludedError;
}
