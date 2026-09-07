-- Preserve raw market totals and refresh additive seasonal-adjusted levels at the shared market upsert boundary.
alter table earnings_v2.market_quarters
  add column if not exists top_line_sa_total numeric(38,4),
  add column if not exists operating_income_sa_total numeric(38,4),
  add column if not exists net_income_sa_total numeric(38,4);

create or replace function earnings_v2.refresh_market_seasonal_adjustment()
returns integer language plpgsql security definer
set search_path = pg_catalog, public, earnings_v2
as $$
declare v_count integer;
begin
  with eligible_years as (
    select market_id, market_year
    from earnings_v2.market_quarters
    where market_year >= 2019
    group by market_id, market_year
    having count(*) = 4
       and bool_and(lifecycle_status = 'complete')
       and bool_and(top_line_total is not null)
       and bool_and(operating_income_total is not null)
       and bool_and(net_income_total is not null)
  ), ranked_years as (
    select market_id, market_year,
           row_number() over (partition by market_id order by market_year desc) as recency_rank
    from eligible_years
  ), observations as (
    select q.market_id, q.market_year, q.market_quarter,
           metric.metric_name, metric.metric_value,
           avg(metric.metric_value) over (
             partition by q.market_id, q.market_year, metric.metric_name
           ) as annual_quarter_average
    from earnings_v2.market_quarters q
    join ranked_years y
      on y.market_id = q.market_id and y.market_year = q.market_year and y.recency_rank <= 10
    cross join lateral (values
      ('top_line', q.top_line_total),
      ('operating_income', q.operating_income_total),
      ('net_income', q.net_income_total)
    ) metric(metric_name, metric_value)
  ), raw_factors as (
    select market_id, metric_name, market_quarter,
           percentile_cont(0.5) within group (
             order by metric_value - annual_quarter_average
           )::numeric as raw_effect,
           count(*) as sample_count
    from observations
    group by market_id, metric_name, market_quarter
  ), normalized as (
    select market_id, metric_name, market_quarter, sample_count,
           round(raw_effect - avg(raw_effect) over (
             partition by market_id, metric_name
           ), 4) as rounded_effect
    from raw_factors
  ), factors as (
    select market_id, metric_name, market_quarter, sample_count,
           case when market_quarter = 4 then
             -sum(rounded_effect) filter (where market_quarter < 4) over (
               partition by market_id, metric_name
             )
           else rounded_effect end as seasonal_effect
    from normalized
  ), pivoted as (
    select market_id, market_quarter,
           max(seasonal_effect) filter (where metric_name = 'top_line' and sample_count >= 3) as top_line_effect,
           max(seasonal_effect) filter (where metric_name = 'operating_income' and sample_count >= 3) as operating_effect,
           max(seasonal_effect) filter (where metric_name = 'net_income' and sample_count >= 3) as net_effect
    from factors
    group by market_id, market_quarter
  ), adjusted as (
    select q.market_id, q.market_year, q.market_quarter,
           case when q.top_line_total is null then null
             else q.top_line_total - coalesce(f.top_line_effect, 0) end as top_line_sa_total,
           case when q.operating_income_total is null then null
             else q.operating_income_total - coalesce(f.operating_effect, 0) end as operating_income_sa_total,
           case when q.net_income_total is null then null
             else q.net_income_total - coalesce(f.net_effect, 0) end as net_income_sa_total
    from earnings_v2.market_quarters q
    left join pivoted f on f.market_id = q.market_id and f.market_quarter = q.market_quarter
    where q.market_year >= 2019
  )
  update earnings_v2.market_quarters q
  set top_line_sa_total = a.top_line_sa_total,
      operating_income_sa_total = a.operating_income_sa_total,
      net_income_sa_total = a.net_income_sa_total
  from adjusted a
  where q.market_id = a.market_id
    and q.market_year = a.market_year
    and q.market_quarter = a.market_quarter;

  with eligible_years as (
    select market_id, market_year
    from earnings_v2.market_quarters
    where market_year >= 2019
    group by market_id, market_year
    having count(*) = 4
       and bool_and(lifecycle_status = 'complete')
       and bool_and(top_line_total is not null)
       and bool_and(operating_income_total is not null)
       and bool_and(net_income_total is not null)
  ), factor_counts as (
    select market_id, count(*) as candidate_years
    from eligible_years
    group by market_id
  ), ordered as (
    select q.market_id, q.market_year, q.market_quarter,
           q.operating_income_sa_total, q.net_income_sa_total,
           lag(q.market_year) over w as prior_year,
           lag(q.market_quarter) over w as prior_quarter,
           lag(q.operating_income_sa_total) over w as prior_operating,
           lag(q.net_income_sa_total) over w as prior_net
    from earnings_v2.market_quarters q
    where q.market_year >= 2019
    window w as (partition by q.market_id order by q.market_year, q.market_quarter)
  ), growth as (
    select o.*,
      case
        when coalesce(f.candidate_years, 0) < 3 then 'insufficient_history'
        when operating_income_sa_total is null or prior_operating is null
          or prior_year * 4 + prior_quarter <> market_year * 4 + market_quarter - 1 then 'missing_prior'
        when prior_operating = 0 then 'from_zero'
        when prior_operating < 0 and operating_income_sa_total > 0 then 'black_turn'
        when prior_operating > 0 and operating_income_sa_total < 0 then 'red_turn'
        else 'normal'
      end as operating_state,
      case
        when coalesce(f.candidate_years, 0) < 3 then 'insufficient_history'
        when net_income_sa_total is null or prior_net is null
          or prior_year * 4 + prior_quarter <> market_year * 4 + market_quarter - 1 then 'missing_prior'
        when prior_net = 0 then 'from_zero'
        when prior_net < 0 and net_income_sa_total > 0 then 'black_turn'
        when prior_net > 0 and net_income_sa_total < 0 then 'red_turn'
        else 'normal'
      end as net_state
    from ordered o
    left join factor_counts f using (market_id)
  )
  update earnings_v2.market_quarters q
  set operating_income_qoq_state = g.operating_state,
      operating_income_qoq_sa_pct = case when g.operating_state = 'normal'
        then round((g.operating_income_sa_total - g.prior_operating) / abs(g.prior_operating) * 100, 8)
        else null end,
      net_income_qoq_state = g.net_state,
      net_income_qoq_sa_pct = case when g.net_state = 'normal'
        then round((g.net_income_sa_total - g.prior_net) / abs(g.prior_net) * 100, 8)
        else null end
  from growth g
  where q.market_id = g.market_id and q.market_year = g.market_year
    and q.market_quarter = g.market_quarter;

  get diagnostics v_count = row_count;
  return v_count;
end;
$$;

revoke all on function earnings_v2.refresh_market_seasonal_adjustment() from public, anon, authenticated;
grant execute on function earnings_v2.refresh_market_seasonal_adjustment() to service_role;

create or replace function public.earnings_v2_v6_upsert_market_quarters(p_rows jsonb)
returns integer language plpgsql security definer
set search_path = pg_catalog, public, earnings_v2
as $$
declare v_count integer;
begin
  insert into earnings_v2.market_quarters as target (
    market_id, market_year, market_quarter, reference_date,
    top_line_total, operating_income_total, net_income_total,
    average_operating_income, average_net_income, operating_margin_pct, net_margin_pct,
    operating_income_yoy_pct, operating_income_yoy_state, net_income_yoy_pct, net_income_yoy_state,
    actual_company_count, reported_company_count, pending_company_count, target_company_count,
    completion_status, lifecycle_status, calculation_version, calculated_at
  )
  select market_id, market_year, market_quarter, reference_date,
    top_line_total, operating_income_total, net_income_total,
    operating_income_total / nullif(target_company_count, 0),
    net_income_total / nullif(target_company_count, 0),
    operating_margin_pct, net_margin_pct,
    operating_income_yoy_pct, operating_income_yoy_state, net_income_yoy_pct, net_income_yoy_state,
    reported_company_count, reported_company_count, pending_company_count, target_company_count,
    case when coalesce(lifecycle_status, completion_status) = 'complete' then 'complete' else 'incomplete' end,
    coalesce(lifecycle_status, completion_status),
    coalesce(calculation_version, 6), coalesce(calculated_at, now())
  from jsonb_populate_recordset(null::earnings_v2.market_quarters, p_rows)
  on conflict (market_id, market_year, market_quarter) do update set
    reference_date = excluded.reference_date,
    top_line_total = excluded.top_line_total,
    operating_income_total = excluded.operating_income_total,
    net_income_total = excluded.net_income_total,
    average_operating_income = excluded.average_operating_income,
    average_net_income = excluded.average_net_income,
    operating_margin_pct = excluded.operating_margin_pct,
    net_margin_pct = excluded.net_margin_pct,
    operating_income_yoy_pct = excluded.operating_income_yoy_pct,
    operating_income_yoy_state = excluded.operating_income_yoy_state,
    net_income_yoy_pct = excluded.net_income_yoy_pct,
    net_income_yoy_state = excluded.net_income_yoy_state,
    actual_company_count = excluded.actual_company_count,
    reported_company_count = excluded.reported_company_count,
    pending_company_count = excluded.pending_company_count,
    target_company_count = excluded.target_company_count,
    completion_status = excluded.completion_status,
    lifecycle_status = excluded.lifecycle_status,
    calculation_version = excluded.calculation_version,
    calculated_at = excluded.calculated_at;
  get diagnostics v_count = row_count;
  perform earnings_v2.refresh_market_seasonal_adjustment();
  return v_count;
end;
$$;

revoke all on function public.earnings_v2_v6_upsert_market_quarters(jsonb)
  from public, anon, authenticated;
grant execute on function public.earnings_v2_v6_upsert_market_quarters(jsonb) to service_role;

drop function public.earnings_v2_public_market_series(text);
create function public.earnings_v2_public_market_series(p_market_id text)
returns table (
  market_year integer, market_quarter smallint, reference_date date,
  top_line_total numeric, operating_income_total numeric, net_income_total numeric,
  top_line_sa_total numeric, operating_income_sa_total numeric, net_income_sa_total numeric,
  operating_margin_pct numeric, net_margin_pct numeric,
  operating_income_yoy_pct numeric, operating_income_yoy_state text,
  net_income_yoy_pct numeric, net_income_yoy_state text,
  operating_income_qoq_sa_pct numeric, operating_income_qoq_state text,
  net_income_qoq_sa_pct numeric, net_income_qoq_state text,
  reported_company_count integer, pending_company_count integer,
  target_company_count integer, lifecycle_status text
)
language sql stable security definer
set search_path = pg_catalog, public, earnings_v2
as $$
  select q.market_year, q.market_quarter, q.reference_date,
    q.top_line_total, q.operating_income_total, q.net_income_total,
    q.top_line_sa_total, q.operating_income_sa_total, q.net_income_sa_total,
    q.operating_margin_pct, q.net_margin_pct,
    q.operating_income_yoy_pct, q.operating_income_yoy_state,
    q.net_income_yoy_pct, q.net_income_yoy_state,
    q.operating_income_qoq_sa_pct, q.operating_income_qoq_state,
    q.net_income_qoq_sa_pct, q.net_income_qoq_state,
    q.reported_company_count, q.pending_company_count,
    q.target_company_count, q.lifecycle_status
  from earnings_v2.market_quarters q
  where q.market_id = p_market_id and q.market_year >= 2019
    and q.calculation_version >= 6
  order by q.market_year, q.market_quarter;
$$;

revoke all on function public.earnings_v2_public_market_series(text) from public;
grant execute on function public.earnings_v2_public_market_series(text) to anon, authenticated, service_role;

select earnings_v2.refresh_market_seasonal_adjustment();