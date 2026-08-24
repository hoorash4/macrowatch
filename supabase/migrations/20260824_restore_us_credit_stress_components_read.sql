-- The existing component trend chart remains available to signed-in users.
create policy "Authenticated users can read U.S. credit stress"
  on public.us_credit_stress_monthly for select to authenticated using (true);
