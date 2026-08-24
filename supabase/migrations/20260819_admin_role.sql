alter table public.user_accounts
  add column if not exists is_admin boolean not null default false;

update public.user_accounts
set is_admin = true,
    updated_at = now()
where user_id = (
  select user_id
  from public.user_accounts
  order by created_at asc
  limit 1
)
and (select count(*) from public.user_accounts) = 1;

