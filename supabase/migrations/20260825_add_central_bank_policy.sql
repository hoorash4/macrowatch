-- Official central-bank statements are fetched and analyzed in memory only.
-- This table retains only source metadata and MacroWatch-derived policy signals.
create table if not exists public.central_bank_policy_events (
  central_bank text not null check (central_bank in ('fed', 'bok')),
  meeting_date date not null,
  source_url text not null,
  statement_hash text not null,
  analysis_status text not null default 'pending' check (analysis_status in ('pending', 'completed', 'failed')),
  action text check (action in ('hike', 'hold', 'cut')),
  target_range_lower numeric(6,3),
  target_range_upper numeric(6,3),
  change_bps integer,
  primary_reason text check (primary_reason in ('inflation_fight', 'growth_overheat', 'recession_financial_stress', 'insurance_easing', 'uncertain')),
  reason_confidence numeric(4,3) check (reason_confidence >= 0 and reason_confidence <= 1),
  transition_assessment text check (transition_assessment in ('confirmed', 'not_confirmed', 'uncertain')),
  financial_stress_mentioned boolean,
  growth_downside_mentioned boolean,
  inflation_pressure_mentioned boolean,
  reason_summary text,
  policy_segment integer,
  segment_sequence integer,
  policy_impulse numeric(8,3),
  policy_stress_contribution numeric(8,3),
  score_profile_version text,
  analyzed_at timestamptz,
  last_error text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (central_bank, meeting_date)
);

create index if not exists central_bank_policy_events_status_idx
  on public.central_bank_policy_events (central_bank, analysis_status, meeting_date);

alter table public.central_bank_policy_events enable row level security;

drop policy if exists "Authenticated users can read central-bank policy signals" on public.central_bank_policy_events;
create policy "Authenticated users can read central-bank policy signals"
  on public.central_bank_policy_events for select to authenticated using (true);

comment on table public.central_bank_policy_events is
  'Official statement text is never stored. Rows contain source metadata and derived policy-regime signals only.';
