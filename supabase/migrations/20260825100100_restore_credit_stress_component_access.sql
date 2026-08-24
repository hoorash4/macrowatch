-- The long-term component chart is visible only after login. Keep source files
-- private while allowing signed-in members to read these derived observations.
drop policy if exists "Authenticated users can read U.S. credit stress"
  on public.us_credit_stress_monthly;

create policy "Authenticated users can read U.S. credit stress"
  on public.us_credit_stress_monthly
  for select to authenticated using (true);
