begin;

update public.news_daily_article_sentiment set article_date = '2099-08-23' where article_date = '2026-08-24';
update public.news_daily_article_sentiment set article_date = '2099-08-24' where article_date = '2026-08-25';
update public.news_article_sentiments set article_date = '2099-08-23' where article_date = '2026-08-24';
update public.news_article_sentiments set article_date = '2099-08-24' where article_date = '2026-08-25';

update public.news_daily_article_sentiment set article_date = '2026-08-23' where article_date = '2099-08-23';
update public.news_daily_article_sentiment set article_date = '2026-08-24' where article_date = '2099-08-24';
update public.news_article_sentiments set article_date = '2026-08-23' where article_date = '2099-08-23';
update public.news_article_sentiments set article_date = '2026-08-24' where article_date = '2099-08-24';

commit;
