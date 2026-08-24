drop table if exists public.indicators;

alter table public.targets
  alter column last_value type numeric
  using nullif(btrim(last_value::text), '')::numeric;

alter table public.history
  alter column recorded_value type numeric
  using nullif(btrim(recorded_value::text), '')::numeric;

create index if not exists history_target_recorded_at_idx
  on public.history (target_id, recorded_at desc);
