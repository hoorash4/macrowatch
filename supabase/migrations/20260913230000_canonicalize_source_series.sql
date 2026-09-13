-- Canonicalize reusable economic/market observations before removing feature-owned copies.

insert into public.economic_chart_points(series_code, observation_date, value, frequency, source)
select mapped.code, p.period_date, p.value, mapped.frequency, mapped.source
from public.automatic_source_points p
join lateral (values
  ('em_capital_capacity','em_dollar_index','EM_DOLLAR_INDEX','D','FRED:DTWEXEMEGS'),
  ('em_capital_capacity','real_yield_10y','US10Y_REAL','D','FRED:DFII10'),
  ('em_capital_capacity','us_high_yield_oas','HY_OAS','D','FRED:BAMLH0A0HYM2'),
  ('em_capital_capacity','nfci','NFCI','W','FRED:NFCI'),
  ('equity_bond_attractiveness','KR_EQUITY','KOSPI_CLOSE','W','YAHOO:^KS11'),
  ('equity_bond_attractiveness','KR_YIELD','KR10Y','W','ECOS:817Y002/010210000'),
  ('equity_bond_attractiveness','US_EQUITY','OEF_ADJUSTED_CLOSE','W','YAHOO:OEF'),
  ('equity_bond_attractiveness','US_YIELD','US10Y','W','USTREASURY:10Y'),
  ('equity_bond_relative','DFII10','US10Y_REAL','D','FRED:DFII10'),
  ('equity_bond_relative','NFCI','NFCI','W','FRED:NFCI')
) mapped(collector, old_code, code, frequency, source)
on mapped.collector=p.collector and mapped.old_code=p.series_code
on conflict (series_code, observation_date) do nothing;

insert into public.economic_chart_points(series_code, observation_date, value, frequency, source)
select case series_code when 'T10Y2Y' then 'US10Y2Y' else series_code end,
       observation_date, value, 'M', 'LEGACY:'||source
from public.equity_bond_source_monthly
on conflict (series_code, observation_date) do nothing;

insert into public.economic_chart_points(series_code, observation_date, value, frequency, source)
select 'US10Y', observed_on, treasury_10y_pct, 'D', source
from public.us_treasury_10y_daily where treasury_10y_pct is not null
on conflict (series_code, observation_date) do nothing;

insert into public.economic_chart_points(series_code, observation_date, value, frequency, source)
select mapping.code, o.observation_date, o.value, mapping.frequency, mapping.source
from public.liquidity_observations o
join lateral (values
 ('US','sofr','US_SOFR','D','FRED:SOFR'), ('US','iorb','US_IORB','D','FRED:IORB'),
 ('US','ioer','US_IOER','D','FRED:IOER'), ('US','rrp','RRP','D','FRED:RRPONTSYD'),
 ('US','real_yield','US10Y_REAL','D','FRED:DFII10'),
 ('US','credit_conditions','NFCI_CREDIT','W','FRED:NFCICREDIT'),
 ('US','fed_assets','US_FED_ASSETS','W','FRED:WALCL'), ('US','tga','TGA','W','FRED:WTREGEN'),
 ('KR','call','KR_CALL_RATE','D','BOK_SNAPSHOT:849'),
 ('KR','m2','KR_M2','M','BOK_SNAPSHOT:875'), ('KR','lf','KR_LF','M','BOK_SNAPSHOT:876'),
 ('KR','kofr','KR_KOFR','D','ECOS:817Y002/010901000'),
 ('KR','equity_flow','KR_BOP_EQUITY_FLOW','M','ECOS:301Y013/BOPF22100000'),
 ('KR','bond_flow','KR_BOP_BOND_FLOW','M','ECOS:301Y013/BOPF22200000')
) mapping(country, old_series, code, frequency, source)
on mapping.country=o.country and mapping.old_series=o.series
on conflict (series_code, observation_date) do nothing;

insert into public.economic_chart_points(series_code, observation_date, value, frequency, source)
select code, observation_date, value, 'D', source from (
 select 'KR_FOREIGN_NET_BUY' code, observation_date, foreign_net_buy_amount value, 'KIS:KOSPI_FOREIGN_NET_BUY' source from public.korea_foreign_flow_raw
 union all select 'KOSPI_TRADING_VALUE', observation_date, kospi_trading_value, 'KIS:KOSPI_TRADING_VALUE' from public.korea_foreign_flow_raw
 union all select 'USDKRW', observation_date, usdkrw_rate, 'ECOS:731Y001/0000001' from public.korea_foreign_flow_raw
) source_rows where value is not null
on conflict (series_code, observation_date) do nothing;

insert into public.economic_chart_points(series_code, observation_date, value, frequency, source)
select code, observation_date, value, frequency, source from (
 select 'SP500_MONTH_END' code, month observation_date, sp500_month_end_close value, 'M' frequency, 'FRED:SP500' source from public.us_market_stress_index_monthly
 union all select 'HY_OAS_WEEKLY', week, high_yield_oas_pct, 'W', 'FRED:BAMLH0A0HYM2' from public.us_market_tension_weekly
 union all select 'NFCI_CREDIT', week, financial_conditions_credit_index, 'W', 'FRED:NFCICREDIT' from public.us_market_tension_weekly
 union all select 'NFCI_RISK', week, financial_conditions_risk_index, 'W', 'FRED:NFCIRISK' from public.us_market_tension_weekly
 union all select 'NFCI_NONFIN_LEVERAGE', week, nonfinancial_leverage_index, 'W', 'FRED:NFCINONFINLEVERAGE' from public.us_market_tension_weekly
 union all select 'US_SHORT_FUNDING_SPREAD', week, short_term_funding_spread, 'W', 'DERIVED:DCPN3M-DGS3MO' from public.us_market_tension_weekly
 union all select 'SP500_WEEKLY_CLOSE', week, sp500_friday_close, 'W', 'FRED:SP500' from public.us_market_tension_weekly
 union all select 'EM_HY_OAS_4W', week, high_yield_4w_average, 'W', 'DERIVED:BAMLEMHYHYLCRPIUSOAS:4W' from public.em_market_stress_weekly
 union all select 'EM_TAIL_RISK_OAS_4W', week, tail_risk_4w_average, 'W', 'DERIVED:BAMLEM4BRRBLCRPIOAS:4W' from public.em_market_stress_weekly
 union all select 'VXEEM_4W', week, vxeem_4w_average, 'W', 'DERIVED:VXEEMCLS:4W' from public.em_market_stress_weekly
 union all select 'EEM_WEEKLY_CLOSE', week, eem_weekly_close, 'W', 'YAHOO:EEM' from public.em_market_stress_weekly
 union all select 'BOK_FSI', month, bok_fsi, 'M', 'BOK_SNAPSHOT:1583' from public.korea_market_stress_monthly
 union all select 'KOSPI_MONTH_END', month, kospi_close, 'M', 'ECOS:802Y001/0001000' from public.korea_market_stress_monthly
 union all select 'KOSPI_WEEKLY_CLOSE', week, kospi_close, 'W', 'ECOS:802Y001/0001000' from public.korea_market_stress_weekly
) source_rows where value is not null
on conflict (series_code, observation_date) do nothing;

insert into public.economic_chart_points(series_code, observation_date, value, frequency, source)
select code, observation_date, value, 'D', source from (
 select 'US3M' code, observation_date, treasury_3m_rate value, 'USTREASURY:daily_treasury_yield_curve' source from public.policy_expectation_spreads
 union all select 'US2Y', observation_date, treasury_2y_rate, 'USTREASURY:daily_treasury_yield_curve' from public.policy_expectation_spreads
 union all select 'EFFR', observation_date, effr_rate, 'FRED:DFF' from public.policy_expectation_spreads
) source_rows
on conflict (series_code, observation_date) do nothing;

insert into public.economic_chart_points(series_code, observation_date, value, frequency, source)
select code, observation_date, value, 'M', source from (
 select 'US_NFIB_SALES_EXPECTATION' code, month observation_date, sales_expectation_net value, 'NFIB:SBET/sales_expect' source from public.us_small_business_risk_monthly
 union all select 'US_NFIB_BORROWING_DIFFICULTY', month, borrowing_difficulty_pct, 'NFIB:SBET/credit_access' from public.us_small_business_risk_monthly
 union all select 'US_NFIB_OPTIMISM', month, optimism_index, 'NFIB:SBET/OPT_INDEX' from public.us_small_business_risk_monthly
 union all select 'KR_SME_FUNDING_OUTLOOK', month, funding_outlook_sbhi, 'KBIZ/KOSIS:funding_outlook' from public.kr_small_business_risk_monthly
 union all select 'KR_SME_UTILIZATION_SA', month, utilization_sa_pct, 'KBIZ/KOSIS:utilization_sa' from public.kr_small_business_risk_monthly
 union all select 'KR_CORP_DELINQ', delinquency_source_month, sme_loan_delinquency_pct, 'ECOS:141Y005/R4AB00/X00/0960' from public.kr_small_business_risk_monthly
 union all select 'KR_SME_HEADLINE_OUTLOOK', month, headline_outlook_sbhi, 'KBIZ/KOSIS:headline_outlook' from public.kr_small_business_risk_monthly
) source_rows where value is not null
on conflict (series_code, observation_date) do nothing;

do $$
begin
  if exists (
    select 1 from public.us_treasury_10y_daily old
    where old.treasury_10y_pct is not null and not exists (
      select 1 from public.economic_chart_points c
      where c.series_code='US10Y' and c.observation_date=old.observed_on
    )
  ) then raise exception 'US10Y canonical migration is incomplete'; end if;
  if exists (
    select 1 from public.korea_foreign_flow_raw old
    where not exists (select 1 from public.economic_chart_points c where c.series_code='KR_FOREIGN_NET_BUY' and c.observation_date=old.observation_date)
       or not exists (select 1 from public.economic_chart_points c where c.series_code='KOSPI_TRADING_VALUE' and c.observation_date=old.observation_date)
       or not exists (select 1 from public.economic_chart_points c where c.series_code='USDKRW' and c.observation_date=old.observation_date)
  ) then raise exception 'Korea foreign-flow canonical migration is incomplete'; end if;
  if exists (
    select 1 from public.policy_expectation_spreads old
    where not exists (select 1 from public.economic_chart_points c where c.series_code='US3M' and c.observation_date=old.observation_date)
       or not exists (select 1 from public.economic_chart_points c where c.series_code='US2Y' and c.observation_date=old.observation_date)
       or not exists (select 1 from public.economic_chart_points c where c.series_code='EFFR' and c.observation_date=old.observation_date)
  ) then raise exception 'Policy-expectation canonical migration is incomplete'; end if;
end $$;

create or replace function public.store_liquidity_batch(p_country text, p_raw jsonb, p_results jsonb)
returns void language plpgsql set search_path = '' as $$
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
  insert into public.liquidity_indices(country,metric,observation_date,score,components,component_scores,sample_count,is_warmup,frequency,method_version)
  select country,metric,observation_date,score,components,component_scores,sample_count,is_warmup,frequency,method_version
  from jsonb_to_recordset(p_results) r(country text,metric text,observation_date date,score double precision,
    components jsonb,component_scores jsonb,sample_count integer,is_warmup boolean,frequency text,method_version text)
  on conflict(country,metric,observation_date,method_version) do nothing;
end $$;

create or replace function public.store_korea_foreign_flow_sources(p_rows jsonb)
returns void language plpgsql security invoker set search_path='' as $$
begin
  insert into public.economic_chart_points(series_code,observation_date,value,frequency,source)
  select series_code, observation_date, value, 'D', source
  from (
    select 'KR_FOREIGN_NET_BUY' series_code, (row->>'observationDate')::date observation_date,
      (row->>'foreignNetBuyAmount')::numeric value, 'KIS:KOSPI_FOREIGN_NET_BUY' source from jsonb_array_elements(p_rows) row
    union all select 'KOSPI_TRADING_VALUE', (row->>'observationDate')::date,
      (row->>'kospiTradingValue')::numeric, 'KIS:KOSPI_TRADING_VALUE' from jsonb_array_elements(p_rows) row
    union all select 'USDKRW', (row->>'observationDate')::date,
      (row->>'usdkrwRate')::numeric, 'ECOS:731Y001/0000001' from jsonb_array_elements(p_rows) row
  ) values_to_store
  on conflict(series_code,observation_date) do update set value=excluded.value,source=excluded.source,collected_at=now();
end $$;
revoke all on function public.store_korea_foreign_flow_sources(jsonb) from public,anon,authenticated;
grant execute on function public.store_korea_foreign_flow_sources(jsonb) to service_role;

-- Allow the cut-over release to write derived-only rows while the legacy source
-- columns remain available for rollback. The columns are removed only after the
-- canonical readers and collectors have been verified in production.
alter table public.em_capital_capacity_daily
  alter column em_dollar_index drop not null,
  alter column real_yield_10y drop not null,
  alter column us_high_yield_oas drop not null,
  alter column nfci drop not null;
alter table public.em_market_stress_weekly
  alter column high_yield_4w_average drop not null,
  alter column tail_risk_4w_average drop not null,
  alter column blended_4w_average drop not null;
alter table public.korea_foreign_flow_daily
  alter column foreign_net_buy_amount drop not null,
  alter column kospi_trading_value drop not null,
  alter column usdkrw_rate drop not null;
alter table public.policy_expectation_spreads
  alter column treasury_3m_rate drop not null,
  alter column treasury_2y_rate drop not null,
  alter column effr_rate drop not null;
alter table public.us_small_business_risk_monthly
  alter column sales_expectation_net drop not null,
  alter column borrowing_difficulty_pct drop not null;
alter table public.kr_small_business_risk_monthly
  alter column funding_outlook_sbhi drop not null,
  alter column utilization_sa_pct drop not null,
  alter column sme_loan_delinquency_pct drop not null;

comment on table public.economic_chart_points is
  'Canonical reusable economic and market observations. Features and models must reference these rows instead of storing private copies.';
comment on table public.us_inflation_monthly is
  'Final composite from canonical official CPI/PCE/PPI YoY series; provisional values use Cleveland Fed CPI/PCE YoY nowcasts.';
