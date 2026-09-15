-- This project has broad default table privileges. Keep the exposed case catalog
-- unreadable to anon and let RLS limit signed-in writes to administrators.
revoke all on table public.historical_cases from anon, authenticated;
grant select, insert, update on table public.historical_cases to authenticated;
