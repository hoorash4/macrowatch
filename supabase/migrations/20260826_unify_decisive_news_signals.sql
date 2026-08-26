-- Present administrator-defined rare events as one calm, direction-neutral category.
alter table public.news_extreme_rules
  drop constraint if exists news_extreme_rules_signal_check;
update public.news_extreme_rules set signal = 'decisive';
alter table public.news_extreme_rules
  add constraint news_extreme_rules_signal_check check (signal = 'decisive');

alter table public.news_article_sentiments
  drop constraint if exists news_article_sentiments_extreme_signal_check;
update public.news_article_sentiments set extreme_signal = 'decisive' where extreme_signal is not null;
alter table public.news_article_sentiments
  add constraint news_article_sentiments_extreme_signal_check check (extreme_signal in ('decisive') or extreme_signal is null);

alter table public.news_extreme_matches
  drop constraint if exists news_extreme_matches_extreme_signal_check;
update public.news_extreme_matches set extreme_signal = 'decisive';
alter table public.news_extreme_matches
  add constraint news_extreme_matches_extreme_signal_check check (extreme_signal = 'decisive');

alter table public.news_daily_article_sentiment
  add column if not exists decisive_news_count integer not null default 0 check (decisive_news_count >= 0);
update public.news_daily_article_sentiment
  set decisive_news_count = coalesce(critical_negative_count, 0) + coalesce(critical_positive_count, 0);

alter table public.news_extreme_alerts
  add column if not exists decisive_news_count integer not null default 0 check (decisive_news_count >= 0);
update public.news_extreme_alerts
  set decisive_news_count = coalesce(critical_negative_count, 0) + coalesce(critical_positive_count, 0);
