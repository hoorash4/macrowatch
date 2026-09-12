alter table public.user_accounts
  add column if not exists theme_preference text not null default 'system';

alter table public.user_accounts
  drop constraint if exists user_accounts_theme_preference_check;

alter table public.user_accounts
  add constraint user_accounts_theme_preference_check
  check (theme_preference in ('system', 'light', 'dark'));

comment on column public.user_accounts.theme_preference is
  'Per-user UI theme preference: system, light, or dark.';

create or replace function public.set_theme_preference(p_theme text)
returns text
language plpgsql
security definer
set search_path = public, pg_temp
as $$
begin
  if auth.uid() is null then
    raise exception 'authentication required';
  end if;

  if p_theme not in ('system', 'light', 'dark') then
    raise exception 'invalid theme preference';
  end if;

  update public.user_accounts
  set theme_preference = p_theme,
      updated_at = now()
  where user_id = auth.uid();

  if not found then
    raise exception 'user account not found';
  end if;

  return p_theme;
end;
$$;

revoke all on function public.set_theme_preference(text) from public;
grant execute on function public.set_theme_preference(text) to authenticated;
