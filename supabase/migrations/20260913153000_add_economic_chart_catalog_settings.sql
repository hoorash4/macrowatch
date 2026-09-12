create table if not exists public.economic_chart_catalog_settings (
  id boolean primary key default true check (id),
  category_order jsonb not null default '[]'::jsonb,
  updated_at timestamptz not null default now(),
  updated_by uuid references auth.users(id) on delete set null,
  constraint economic_chart_catalog_category_order_array
    check (jsonb_typeof(category_order) = 'array')
);

alter table public.economic_chart_catalog_settings enable row level security;

drop policy if exists "Authenticated users can read economic chart catalog settings"
  on public.economic_chart_catalog_settings;
drop policy if exists "Administrators can insert economic chart catalog settings"
  on public.economic_chart_catalog_settings;
drop policy if exists "Administrators can update economic chart catalog settings"
  on public.economic_chart_catalog_settings;

create policy "Authenticated users can read economic chart catalog settings"
  on public.economic_chart_catalog_settings
  for select to authenticated
  using (true);

create policy "Administrators can insert economic chart catalog settings"
  on public.economic_chart_catalog_settings
  for insert to authenticated
  with check (
    exists (
      select 1 from public.user_accounts
      where user_id = auth.uid() and is_admin = true
    )
  );

create policy "Administrators can update economic chart catalog settings"
  on public.economic_chart_catalog_settings
  for update to authenticated
  using (
    exists (
      select 1 from public.user_accounts
      where user_id = auth.uid() and is_admin = true
    )
  )
  with check (
    exists (
      select 1 from public.user_accounts
      where user_id = auth.uid() and is_admin = true
    )
  );

grant select on public.economic_chart_catalog_settings to authenticated;
grant insert, update on public.economic_chart_catalog_settings to authenticated;

insert into public.economic_chart_catalog_settings (id, category_order)
values (true, '["금리 · 신용", "밸류에이션", "시장가격", "고빈도 경기", "물가", "기업신용", "유동성"]'::jsonb)
on conflict (id) do nothing;

comment on table public.economic_chart_catalog_settings is
  'Administrator-managed global ordering for economic chart categories.';
