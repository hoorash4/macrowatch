alter table public.user_accounts
  alter column kakao_user_id drop not null;

alter table public.user_accounts
  add column if not exists username text;

create unique index if not exists user_accounts_username_lower_uidx
  on public.user_accounts (lower(username))
  where username is not null;

alter table public.user_accounts
  drop constraint if exists user_accounts_login_identifier_check;

alter table public.user_accounts
  add constraint user_accounts_login_identifier_check
  check (username is not null or kakao_user_id is not null);

alter table public.user_accounts
  drop constraint if exists user_accounts_username_format_check;

alter table public.user_accounts
  add constraint user_accounts_username_format_check
  check (username is null or username ~ '^[a-z0-9._-]{4,32}$');

comment on column public.user_accounts.username is
  'Case-insensitive private login ID. Password hashes remain exclusively in Supabase Auth.';
