-- A retried job keeps its prior metadata. Reapplying review_required must still
-- quarantine a newly parsed quarter even when the previous attempt also needed
-- review, so the trigger intentionally remains idempotent.
create or replace function public.apply_earnings_job_review_outcome()
returns trigger language plpgsql security definer
set search_path = public, pg_temp
as $$
declare v_quarter smallint;
begin
  if new.source <> 'open_dart'
     or coalesce(new.metadata->>'outcome', '') <> 'review_required' then
    return new;
  end if;
  v_quarter := case new.report_code
    when '11013' then 1 when '11012' then 2
    when '11014' then 3 when '11011' then 4 else null end;
  if v_quarter is not null then
    update public.earnings_quarterly_financials
    set quality_status = 'review_required',
        canonical_version = canonical_version + 1,
        calculated_at = now(), updated_at = now()
    where company_id = new.company_id and fiscal_year = new.business_year
      and fiscal_quarter = v_quarter;
  end if;
  return new;
end;
$$;
revoke all on function public.apply_earnings_job_review_outcome()
  from public, anon, authenticated;
