alter table public.central_bank_policy_events
  add column if not exists is_emergency boolean not null default false,
  add column if not exists has_large_rate_move boolean not null default false,
  add column if not exists impact_multiplier numeric(4,2) not null default 1.00;

comment on column public.central_bank_policy_events.impact_multiplier is
  '1.25x for an emergency decision, plus another 1.25x for an absolute policy-rate move of 50bp or more; both conditions yield 1.50x.';
