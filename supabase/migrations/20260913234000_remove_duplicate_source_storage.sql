-- Run only after the canonical-source application release has been verified.

create or replace function public.store_liquidity_batch(p_country text, p_raw jsonb, p_results jsonb)
returns void language plpgsql security invoker set search_path = '' as $$
begin
  if p_country not in ('US','KR') or jsonb_array_length(p_results)=0 then
    raise exception 'Invalid liquidity batch';
  end if;
  if exists (select 1 from jsonb_array_elements(p_results) r where r->>'country' is distinct from p_country) then
    raise exception 'Country mismatch';
  end if;
  insert into public.economic_chart_points(series_code,observation_date,value,frequency,source)
  select series_code,observation_date,value,frequency,source
  from jsonb_to_recordset(p_raw) r(series_code text,observation_date date,value numeric,frequency text,source text)
  on conflict(series_code,observation_date) do update set value=excluded.value,frequency=excluded.frequency,
    source=excluded.source,collected_at=now();
  insert into public.liquidity_indices(country,metric,observation_date,score,sample_count,is_warmup,frequency,method_version)
  select country,metric,observation_date,score,sample_count,is_warmup,frequency,method_version
  from jsonb_to_recordset(p_results) r(country text,metric text,observation_date date,score double precision,
    sample_count integer,is_warmup boolean,frequency text,method_version text)
  on conflict(country,metric,observation_date,method_version) do nothing;
end $$;

alter table public.liquidity_indices
  drop column if exists components,
  drop column if exists component_scores;
alter table public.us_market_stress_index_monthly
  drop column if exists sp500_month_end_close;
alter table public.us_market_tension_weekly
  drop column if exists high_yield_oas_pct,
  drop column if exists financial_conditions_credit_index,
  drop column if exists financial_conditions_risk_index,
  drop column if exists nonfinancial_leverage_index,
  drop column if exists short_term_funding_spread,
  drop column if exists sp500_friday_close;
alter table public.em_market_stress_weekly
  drop column if exists high_yield_4w_average,
  drop column if exists tail_risk_4w_average,
  drop column if exists blended_4w_average,
  drop column if exists vxeem_4w_average,
  drop column if exists eem_weekly_close;
alter table public.em_capital_capacity_daily
  drop column if exists em_dollar_index,
  drop column if exists real_yield_10y,
  drop column if exists us_high_yield_oas,
  drop column if exists nfci;
alter table public.korea_market_stress_monthly
  drop column if exists corporate_credit_spread,
  drop column if exists investment_grade_spread,
  drop column if exists rating_gap_spread,
  drop column if exists short_term_funding_spread,
  drop column if exists interbank_liquidity_spread,
  drop column if exists kospi_close,
  drop column if exists bok_fsi,
  drop column if exists usdkrw_exchange_rate;
alter table public.korea_foreign_flow_daily
  drop column if exists foreign_net_buy_amount,
  drop column if exists kospi_trading_value,
  drop column if exists usdkrw_rate;
alter table public.policy_expectation_spreads
  drop column if exists treasury_3m_rate,
  drop column if exists treasury_2y_rate,
  drop column if exists effr_rate;
alter table public.us_small_business_risk_monthly
  drop column if exists sales_expectation_net,
  drop column if exists borrowing_difficulty_pct,
  drop column if exists small_business_delinquency_pct,
  drop column if exists optimism_index;
alter table public.kr_small_business_risk_monthly
  drop column if exists funding_outlook_sbhi,
  drop column if exists utilization_sa_pct,
  drop column if exists sme_loan_delinquency_pct,
  drop column if exists headline_outlook_sbhi;

drop table if exists public.korea_market_stress_weekly;
drop table if exists public.korea_foreign_flow_raw;
drop table if exists public.us_treasury_10y_daily;
drop table if exists public.equity_bond_source_monthly;
drop table if exists public.liquidity_observations;
drop table if exists public.automatic_source_points;
drop table if exists public.us_inflation_leading_daily;

comment on table public.economic_chart_points is
  'Canonical reusable economic and market observations. Features and models reference these rows instead of storing private copies.';
comment on table public.liquidity_indices is
  'Published liquidity scores and calculation metadata; reusable raw observations are stored only in economic_chart_points.';
comment on table public.us_inflation_monthly is
  'Final composite from canonical official CPI/PCE/PPI YoY series; provisional values use Cleveland Fed CPI/PCE YoY nowcasts.';
