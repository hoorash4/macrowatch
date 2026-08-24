-- MacroWatch alerts only need targets.last_value. Historical chart data, if
-- added later, will be imported separately from each provider's time-series API.
drop table if exists public.history;
