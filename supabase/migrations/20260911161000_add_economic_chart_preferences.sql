create table if not exists public.economic_chart_preferences (
  user_id uuid primary key references auth.users(id) on delete cascade,
  series_order jsonb not null default '{}'::jsonb,
  hidden_series jsonb not null default '[]'::jsonb,
  horizontal_lines jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default now(),
  constraint economic_chart_preferences_series_order_object
    check (jsonb_typeof(series_order) = 'object'),
  constraint economic_chart_preferences_hidden_series_array
    check (jsonb_typeof(hidden_series) = 'array'),
  constraint economic_chart_preferences_horizontal_lines_object
    check (jsonb_typeof(horizontal_lines) = 'object')
);

alter table public.economic_chart_preferences enable row level security;

drop policy if exists "Users can read their own economic chart preferences"
  on public.economic_chart_preferences;
drop policy if exists "Users can insert their own economic chart preferences"
  on public.economic_chart_preferences;
drop policy if exists "Users can update their own economic chart preferences"
  on public.economic_chart_preferences;
drop policy if exists "Users can delete their own economic chart preferences"
  on public.economic_chart_preferences;

create policy "Users can read their own economic chart preferences"
  on public.economic_chart_preferences
  for select to authenticated
  using (auth.uid() = user_id);

create policy "Users can insert their own economic chart preferences"
  on public.economic_chart_preferences
  for insert to authenticated
  with check (auth.uid() = user_id);

create policy "Users can update their own economic chart preferences"
  on public.economic_chart_preferences
  for update to authenticated
  using (auth.uid() = user_id)
  with check (auth.uid() = user_id);

create policy "Users can delete their own economic chart preferences"
  on public.economic_chart_preferences
  for delete to authenticated
  using (auth.uid() = user_id);

grant select, insert, update, delete on public.economic_chart_preferences to authenticated;

comment on table public.economic_chart_preferences is
  'Per-user economic chart list ordering, hidden series, and plain horizontal lines. Alert-linked lines remain in public.targets.';
