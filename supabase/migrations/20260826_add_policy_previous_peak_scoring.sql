alter table public.central_bank_policy_events
  add column if not exists rate_cycle_id integer,
  add column if not exists is_confirmed_rate_peak boolean not null default false,
  add column if not exists rate_peak_upper numeric(7,3),
  add column if not exists rate_peak_formed_date date,
  add column if not exists previous_peak_upper numeric(7,3),
  add column if not exists previous_peak_formed_date date,
  add column if not exists previous_peak_age_days integer,
  add column if not exists previous_peak_reached boolean not null default false,
  add column if not exists previous_peak_adjustment numeric(9,3);

comment on column public.central_bank_policy_events.is_confirmed_rate_peak is
  'True on the first meeting that reached a rate-cycle high later confirmed by the first subsequent cut.';

comment on column public.central_bank_policy_events.previous_peak_adjustment is
  'Additive +100 stress score when a hike first reaches the latest confirmed upper-bound peak after at least 360 days.';
