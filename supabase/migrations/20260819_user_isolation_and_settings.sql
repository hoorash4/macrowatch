do $$
declare
  owner_id uuid;
begin
  select user_id into owner_id
  from public.user_accounts
  order by created_at asc
  limit 1;

  if owner_id is not null
     and (select count(*) from public.user_accounts) = 1 then
    update public.targets set user_id = owner_id;
  end if;
end $$;

alter table public.targets
  alter column user_id set not null;

alter table public.targets
  drop constraint if exists targets_user_id_fkey;

alter table public.targets
  add constraint targets_user_id_fkey
  foreign key (user_id) references auth.users(id) on delete cascade;

alter table public.targets enable row level security;

drop policy if exists "Users can read their own targets" on public.targets;
drop policy if exists "Users can insert their own targets" on public.targets;
drop policy if exists "Users can update their own targets" on public.targets;
drop policy if exists "Users can delete their own targets" on public.targets;

create policy "Users can read their own targets"
  on public.targets for select to authenticated
  using (auth.uid() = user_id);

create policy "Users can insert their own targets"
  on public.targets for insert to authenticated
  with check (auth.uid() = user_id);

create policy "Users can update their own targets"
  on public.targets for update to authenticated
  using (auth.uid() = user_id)
  with check (auth.uid() = user_id);

create policy "Users can delete their own targets"
  on public.targets for delete to authenticated
  using (auth.uid() = user_id);

create table if not exists public.app_settings (
  key text primary key,
  value jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default now(),
  updated_by uuid references auth.users(id) on delete set null
);

alter table public.app_settings enable row level security;

insert into public.app_settings (key, value)
values ('target_check_schedule', '{"times":["08:00","18:00"],"timezone":"Asia/Seoul"}'::jsonb)
on conflict (key) do nothing;

