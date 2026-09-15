create table if not exists public.historical_current_settings (
  setting_key text primary key default 'default'
    check (setting_key = 'default'),
  current_name text not null default '현재 국면 관찰 중'
    check (char_length(btrim(current_name)) between 1 and 60),
  updated_at timestamptz not null default now(),
  updated_by uuid references auth.users(id) on delete set null
);

create index if not exists historical_current_settings_updated_by_idx
  on public.historical_current_settings(updated_by);

alter table public.historical_current_settings enable row level security;

create policy "Authenticated users read historical current settings"
  on public.historical_current_settings for select to authenticated using (true);

create policy "Administrators update historical current settings"
  on public.historical_current_settings for update to authenticated
  using (
    exists (
      select 1 from public.user_accounts
      where user_id = (select auth.uid()) and is_admin = true
    )
  )
  with check (
    exists (
      select 1 from public.user_accounts
      where user_id = (select auth.uid()) and is_admin = true
    )
  );

revoke all on table public.historical_current_settings from public, anon, authenticated;
grant select, update on table public.historical_current_settings to authenticated;

insert into public.historical_current_settings (setting_key, current_name)
values ('default', '현재 국면 관찰 중')
on conflict (setting_key) do nothing;

comment on table public.historical_current_settings is
  'Singleton fallback title for Historical Insight monitoring when no incomplete market cycle exists.';
