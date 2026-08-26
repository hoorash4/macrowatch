alter table public.central_bank_policy_events
  add column if not exists ai_primary_reason text check (ai_primary_reason in ('inflation_fight', 'growth_overheat', 'recession_financial_stress', 'insurance_easing', 'uncertain')),
  add column if not exists admin_primary_reason text check (admin_primary_reason in ('inflation_fight', 'growth_overheat', 'recession_financial_stress', 'insurance_easing')),
  add column if not exists admin_reason_keyword text,
  add column if not exists admin_score_override numeric(9,3),
  add column if not exists admin_resolved_at timestamptz,
  add column if not exists admin_resolved_by uuid,
  add column if not exists direction_segment integer,
  add column if not exists direction_sequence integer not null default 0,
  add column if not exists reason_segment integer,
  add column if not exists reason_sequence integer not null default 0,
  add column if not exists trend_type text check (trend_type in ('none', 'single', 'adjustment', 'confirmed', 'bridge_pending', 'bridge_confirmed', 'hold_pending', 'hold_scoring')),
  add column if not exists hold_sequence integer not null default 0,
  add column if not exists base_score numeric(9,3),
  add column if not exists first_decision_adjustment numeric(9,3),
  add column if not exists large_move_adjustment numeric(9,3),
  add column if not exists emergency_adjustment numeric(9,3),
  add column if not exists hold_adjustment numeric(9,3),
  add column if not exists final_event_score numeric(9,3),
  add column if not exists policy_index numeric(12,3);

update public.central_bank_policy_events
set ai_primary_reason = primary_reason
where ai_primary_reason is null and primary_reason is not null;

alter table public.central_bank_policy_events
  drop constraint if exists central_bank_policy_events_admin_resolution_complete;

alter table public.central_bank_policy_events
  add constraint central_bank_policy_events_admin_resolution_complete check (
    (admin_primary_reason is null and admin_reason_keyword is null and admin_score_override is null and admin_resolved_at is null and admin_resolved_by is null)
    or
    (admin_primary_reason is not null and nullif(btrim(admin_reason_keyword), '') is not null and admin_score_override is not null and admin_resolved_at is not null and admin_resolved_by is not null)
  );

create index if not exists central_bank_policy_events_review_idx
  on public.central_bank_policy_events (central_bank, meeting_date)
  where admin_primary_reason is null
    and (ai_primary_reason = 'uncertain' or transition_assessment = 'confirmed');

comment on column public.central_bank_policy_events.policy_index is
  'MacroWatch policy-stress line value, starting at 1000 and adding each final_event_score in meeting order.';

comment on column public.central_bank_policy_events.admin_score_override is
  'Administrator-confirmed event score. When present it replaces only that meeting automatic score.';
