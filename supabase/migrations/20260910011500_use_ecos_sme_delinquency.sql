do $$
begin
  if exists (
    select 1 from information_schema.columns
    where table_schema = 'public'
      and table_name = 'kr_small_business_risk_monthly'
      and column_name = 'sme_corporation_delinquency_pct'
  ) then
    alter table public.kr_small_business_risk_monthly
      rename column sme_corporation_delinquency_pct to sme_loan_delinquency_pct;
  end if;
end $$;

comment on table public.kr_small_business_risk_monthly is
  'Monthly Korean SME risk index: funding outlook SBHI 35%, seasonally adjusted manufacturing utilization 30%, and nationwide SME loan delinquency rate (1 month or more) 35%.';
