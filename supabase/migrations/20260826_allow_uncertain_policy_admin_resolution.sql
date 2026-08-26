alter table public.central_bank_policy_events
  drop constraint if exists central_bank_policy_events_admin_primary_reason_check;

alter table public.central_bank_policy_events
  add constraint central_bank_policy_events_admin_primary_reason_check check (
    admin_primary_reason in ('inflation_fight', 'growth_overheat', 'recession_financial_stress', 'insurance_easing', 'uncertain')
  );

alter table public.central_bank_policy_events
  drop constraint if exists central_bank_policy_events_admin_resolution_complete;

alter table public.central_bank_policy_events
  add constraint central_bank_policy_events_admin_resolution_complete check (
    (admin_primary_reason is null and admin_reason_keyword is null and admin_score_override is null and admin_resolved_at is null and admin_resolved_by is null)
    or
    (
      admin_primary_reason is not null
      and admin_score_override is not null
      and admin_resolved_at is not null
      and admin_resolved_by is not null
    )
  );
