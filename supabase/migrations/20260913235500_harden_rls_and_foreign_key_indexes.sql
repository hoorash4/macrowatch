-- Tighten client privileges without changing authenticated application behavior.

drop policy if exists "allow anon select targets" on public.targets;
drop policy if exists "allow anon insert targets" on public.targets;
drop policy if exists "allow anon update targets" on public.targets;
drop policy if exists "allow anon delete targets" on public.targets;

revoke all on table public.targets from anon;
grant select, insert, update, delete on table public.targets to authenticated;
grant all on table public.targets to service_role;

-- These RLS-enabled tables intentionally have no browser policies. Their existing
-- no-policy RLS denial is backed by explicit privilege denial as defense in depth.
revoke all on table
  public.alert_events,
  public.api_sources,
  public.app_settings,
  public.central_bank_policy_analysis_history,
  public.device_tokens,
  public.korea_export_intramonth_snapshots,
  public.market_index_prices,
  public.news_article_sentiments,
  public.news_daily_sentiment,
  public.news_event_feedback,
  public.news_event_sources,
  public.news_events,
  public.news_extreme_alerts,
  public.news_extreme_matches,
  public.news_extreme_rules,
  public.news_pipeline_runs,
  public.notification_channels,
  public.policy_briefing_alerts
from anon, authenticated;

grant all on table
  public.alert_events,
  public.api_sources,
  public.app_settings,
  public.central_bank_policy_analysis_history,
  public.device_tokens,
  public.korea_export_intramonth_snapshots,
  public.market_index_prices,
  public.news_article_sentiments,
  public.news_daily_sentiment,
  public.news_event_feedback,
  public.news_event_sources,
  public.news_events,
  public.news_extreme_alerts,
  public.news_extreme_matches,
  public.news_extreme_rules,
  public.news_pipeline_runs,
  public.notification_channels,
  public.policy_briefing_alerts
to service_role;

-- Internal SECURITY DEFINER helpers are callable only by the backend role.
revoke all on function public.earnings_v2_us_active_companies(integer) from public, anon, authenticated;
revoke all on function public.earnings_v2_us_get_universe(text, integer, smallint) from public, anon, authenticated;
revoke all on function public.earnings_v2_us_market_facts(text, integer, smallint) from public, anon, authenticated;
revoke all on function public.rls_auto_enable() from public, anon, authenticated;
grant execute on function public.earnings_v2_us_active_companies(integer) to service_role;
grant execute on function public.earnings_v2_us_get_universe(text, integer, smallint) to service_role;
grant execute on function public.earnings_v2_us_market_facts(text, integer, smallint) to service_role;
grant execute on function public.rls_auto_enable() to service_role;

-- Public chart readers require a signed-in session. Theme updates already verify
-- auth.uid() in the function body and remain available to authenticated users.
revoke all on function public.earnings_v2_public_company_series(text) from anon;
revoke all on function public.earnings_v2_public_latest_company_options() from anon;
revoke all on function public.earnings_v2_public_market_series(text) from anon;
revoke all on function public.set_theme_preference(text) from anon;

-- Cache auth.uid() once per statement instead of evaluating it for every row.
drop policy if exists "Users can read their own account" on public.user_accounts;
create policy "Users can read their own account"
  on public.user_accounts for select to authenticated
  using ((select auth.uid()) = user_id);

drop policy if exists "Users can read their own targets" on public.targets;
create policy "Users can read their own targets"
  on public.targets for select to authenticated
  using ((select auth.uid()) = user_id);
drop policy if exists "Users can insert their own targets" on public.targets;
create policy "Users can insert their own targets"
  on public.targets for insert to authenticated
  with check ((select auth.uid()) = user_id);
drop policy if exists "Users can update their own targets" on public.targets;
create policy "Users can update their own targets"
  on public.targets for update to authenticated
  using ((select auth.uid()) = user_id)
  with check ((select auth.uid()) = user_id);
drop policy if exists "Users can delete their own targets" on public.targets;
create policy "Users can delete their own targets"
  on public.targets for delete to authenticated
  using ((select auth.uid()) = user_id);

drop policy if exists "Users can read their own economic chart preferences" on public.economic_chart_preferences;
create policy "Users can read their own economic chart preferences"
  on public.economic_chart_preferences for select to authenticated
  using ((select auth.uid()) = user_id);
drop policy if exists "Users can insert their own economic chart preferences" on public.economic_chart_preferences;
create policy "Users can insert their own economic chart preferences"
  on public.economic_chart_preferences for insert to authenticated
  with check ((select auth.uid()) = user_id);
drop policy if exists "Users can update their own economic chart preferences" on public.economic_chart_preferences;
create policy "Users can update their own economic chart preferences"
  on public.economic_chart_preferences for update to authenticated
  using ((select auth.uid()) = user_id)
  with check ((select auth.uid()) = user_id);
drop policy if exists "Users can delete their own economic chart preferences" on public.economic_chart_preferences;
create policy "Users can delete their own economic chart preferences"
  on public.economic_chart_preferences for delete to authenticated
  using ((select auth.uid()) = user_id);

drop policy if exists "Administrators can insert economic chart catalog settings" on public.economic_chart_catalog_settings;
create policy "Administrators can insert economic chart catalog settings"
  on public.economic_chart_catalog_settings for insert to authenticated
  with check (exists (
    select 1 from public.user_accounts
    where user_id = (select auth.uid()) and is_admin = true
  ));
drop policy if exists "Administrators can update economic chart catalog settings" on public.economic_chart_catalog_settings;
create policy "Administrators can update economic chart catalog settings"
  on public.economic_chart_catalog_settings for update to authenticated
  using (exists (
    select 1 from public.user_accounts
    where user_id = (select auth.uid()) and is_admin = true
  ))
  with check (exists (
    select 1 from public.user_accounts
    where user_id = (select auth.uid()) and is_admin = true
  ));

-- Index the referencing side of active foreign keys used by deletes and joins.
create index if not exists app_settings_updated_by_idx
  on public.app_settings(updated_by);
create index if not exists economic_chart_catalog_settings_updated_by_idx
  on public.economic_chart_catalog_settings(updated_by);
create index if not exists market_sector_weekly_rankings_etf_id_idx
  on public.market_sector_weekly_rankings(etf_id);
create index if not exists news_event_feedback_reviewed_by_idx
  on public.news_event_feedback(reviewed_by);
