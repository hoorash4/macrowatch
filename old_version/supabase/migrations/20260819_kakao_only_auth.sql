create table if not exists public.user_accounts (
  user_id uuid primary key references auth.users(id) on delete cascade,
  kakao_user_id text not null unique,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

alter table public.user_accounts enable row level security;

create policy "Users can read their own account"
  on public.user_accounts
  for select
  to authenticated
  using (auth.uid() = user_id);

create index if not exists user_accounts_kakao_user_id_idx
  on public.user_accounts (kakao_user_id);

