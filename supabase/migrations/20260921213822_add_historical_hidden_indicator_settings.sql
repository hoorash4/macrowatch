alter table public.economic_chart_catalog_settings
  add column if not exists historical_hidden_series_codes text[] not null default '{}'::text[];

comment on column public.economic_chart_catalog_settings.historical_hidden_series_codes
  is 'Global Historical Insight indicator codes hidden from the comparison indicator rail. Administrators can restore them from the hidden list.';
