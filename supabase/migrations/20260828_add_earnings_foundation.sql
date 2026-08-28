-- Earnings Momentum foundation.
--
-- Raw provider responses, immutable filing facts, and the current canonical
-- quarterly values are deliberately separated. A correction can therefore
-- replace the canonical row without destroying the source history needed for
-- audits or future formula changes.

create table if not exists public.earnings_companies (
  id uuid primary key default gen_random_uuid(),
  country text not null check (country in ('KR', 'US')),
  company_name text not null check (char_length(trim(company_name)) between 1 and 160),
  ticker text,
  exchange text,
  reporting_currency text not null check (reporting_currency ~ '^[A-Z]{3}$'),
  is_active boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  check (ticker is null or char_length(trim(ticker)) between 1 and 32),
  check (exchange is null or char_length(trim(exchange)) between 1 and 40)
);

create table if not exists public.earnings_company_identifiers (
  company_id uuid not null references public.earnings_companies(id) on delete cascade,
  identifier_type text not null check (identifier_type in (
    'dart_corp_code',
    'krx_ticker',
    'kis_domestic_code',
    'sec_cik',
    'us_ticker',
    'kis_overseas_code'
  )),
  identifier_value text not null check (char_length(trim(identifier_value)) between 1 and 64),
  is_primary boolean not null default true,
  valid_from date,
  valid_to date,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (company_id, identifier_type, identifier_value),
  unique (identifier_type, identifier_value),
  check (valid_to is null or valid_from is null or valid_to >= valid_from)
);

create table if not exists public.earnings_indices (
  index_id text primary key,
  index_name text not null,
  country text not null check (country in ('KR', 'US')),
  target_count smallint not null check (target_count > 0),
  constituent_source text,
  is_active boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

insert into public.earnings_indices
  (index_id, index_name, country, target_count, constituent_source)
values
  ('SP100', 'S&P 500 시가총액 상위 100', 'US', 100, 'S&P 500 constituents + KIS market cap'),
  ('NASDAQ100', 'NASDAQ 시가총액 상위 100', 'US', 100, 'KIS overseas market-cap ranking'),
  ('KOSPI100', 'KOSPI 시가총액 상위 100', 'KR', 100, 'KIS domestic market-cap ranking'),
  ('KOSDAQ50', 'KOSDAQ 시가총액 상위 50', 'KR', 50, 'KIS domestic market-cap ranking')
on conflict (index_id) do nothing;

create table if not exists public.earnings_index_memberships (
  index_id text not null references public.earnings_indices(index_id) on delete restrict,
  company_id uuid not null references public.earnings_companies(id) on delete restrict,
  effective_from date not null,
  effective_to date,
  source text not null,
  source_reference text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (index_id, company_id, effective_from),
  check (effective_to is null or effective_to >= effective_from)
);

create unique index if not exists earnings_index_memberships_current_uidx
  on public.earnings_index_memberships (index_id, company_id)
  where effective_to is null;

create index if not exists earnings_index_memberships_company_idx
  on public.earnings_index_memberships (company_id, effective_from desc);

-- A payload is one provider response. request_params must contain only
-- non-secret parameters; API keys and authorization headers are never stored.
create table if not exists public.earnings_source_payloads (
  id uuid primary key default gen_random_uuid(),
  source text not null check (source in ('open_dart', 'sec_edgar', 'kis', 'manual')),
  operation text not null check (char_length(trim(operation)) between 1 and 80),
  request_key text not null check (char_length(trim(request_key)) between 1 and 240),
  request_params jsonb not null default '{}'::jsonb,
  response_payload jsonb,
  payload_sha256 text,
  status text not null check (status in ('started', 'completed', 'failed')),
  requested_at timestamptz not null default now(),
  completed_at timestamptz,
  error_message text,
  created_at timestamptz not null default now(),
  check (payload_sha256 is null or payload_sha256 ~ '^[0-9a-f]{64}$'),
  check (completed_at is null or completed_at >= requested_at),
  check (
    (status = 'started' and completed_at is null)
    or (status = 'completed' and completed_at is not null and response_payload is not null)
    or (status = 'failed' and completed_at is not null)
  )
);

create unique index if not exists earnings_source_payloads_hash_uidx
  on public.earnings_source_payloads (source, operation, request_key, payload_sha256)
  where payload_sha256 is not null;

create index if not exists earnings_source_payloads_lookup_idx
  on public.earnings_source_payloads (source, operation, requested_at desc);

-- One row represents one submitted filing. Corrected filings are new rows and
-- point to the filing they replace instead of overwriting it.
create table if not exists public.earnings_filings (
  id uuid primary key default gen_random_uuid(),
  company_id uuid not null references public.earnings_companies(id) on delete restrict,
  source text not null check (source in ('open_dart', 'sec_edgar', 'manual')),
  source_filing_id text not null check (char_length(trim(source_filing_id)) between 1 and 120),
  filing_kind text not null check (filing_kind in (
    'q1', 'half_year', 'q3', 'annual', 'quarterly', 'amendment', 'other'
  )),
  source_report_code text,
  fiscal_year integer not null check (fiscal_year between 1900 and 2200),
  fiscal_quarter smallint check (fiscal_quarter between 1 and 4),
  market_year integer check (market_year between 1900 and 2200),
  market_quarter smallint check (market_quarter between 1 and 4),
  period_start date,
  period_end date not null,
  filing_date date not null,
  filing_at timestamptz,
  is_correction boolean not null default false,
  corrects_filing_id uuid references public.earnings_filings(id) on delete set null,
  source_url text,
  metadata jsonb not null default '{}'::jsonb,
  source_payload_id uuid references public.earnings_source_payloads(id) on delete set null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (source, source_filing_id),
  check (period_start is null or period_end >= period_start),
  check (not is_correction or corrects_filing_id is not null or source = 'open_dart')
);

create index if not exists earnings_filings_company_period_idx
  on public.earnings_filings (company_id, period_end desc, filing_date desc);

create index if not exists earnings_filings_market_cycle_idx
  on public.earnings_filings (market_year, market_quarter, filing_date desc);

-- Immutable normalized facts extracted from a filing response. source_row_key
-- is generated by the collector from the provider row identity so retries are
-- idempotent even when one filing contains both consolidated and separate data.
create table if not exists public.earnings_financial_facts (
  id bigint generated by default as identity primary key,
  filing_id uuid not null references public.earnings_filings(id) on delete cascade,
  company_id uuid not null references public.earnings_companies(id) on delete restrict,
  metric text not null check (metric in ('revenue', 'operating_income', 'net_income', 'eps')),
  source_account_id text,
  source_account_name text not null,
  statement_type text,
  consolidation_scope text not null check (consolidation_scope in ('CFS', 'OFS', 'NA')),
  period_start date,
  period_end date not null,
  value_kind text not null check (value_kind in ('quarter', 'ytd', 'fy', 'instant', 'unknown')),
  value numeric(38, 8),
  currency text,
  source_field text,
  source_row_key text not null,
  raw_row jsonb not null,
  source_payload_id uuid references public.earnings_source_payloads(id) on delete set null,
  created_at timestamptz not null default now(),
  unique (filing_id, source_row_key),
  check (period_start is null or period_end >= period_start),
  check (currency is null or currency ~ '^[A-Z]{3}$')
);

create index if not exists earnings_financial_facts_company_metric_idx
  on public.earnings_financial_facts (company_id, metric, period_end desc);

-- Current standardized single-quarter values. This table is replaceable and
-- recalculable from immutable filings/facts; no YoY or ranking formula lives
-- here so formula changes never require another provider download.
create table if not exists public.earnings_quarterly_financials (
  company_id uuid not null references public.earnings_companies(id) on delete restrict,
  fiscal_year integer not null check (fiscal_year between 1900 and 2200),
  fiscal_quarter smallint not null check (fiscal_quarter between 1 and 4),
  market_year integer not null check (market_year between 1900 and 2200),
  market_quarter smallint not null check (market_quarter between 1 and 4),
  period_start date,
  period_end date not null,
  revenue numeric(38, 8),
  operating_income numeric(38, 8),
  net_income numeric(38, 8),
  eps numeric(38, 8),
  currency text not null check (currency ~ '^[A-Z]{3}$'),
  consolidation_scope text not null check (consolidation_scope in ('CFS', 'OFS', 'NA')),
  source_filing_id uuid not null references public.earnings_filings(id) on delete restrict,
  quality_status text not null default 'complete' check (quality_status in (
    'complete', 'partial', 'review_required'
  )),
  missing_metrics text[] not null default '{}'::text[],
  canonical_version integer not null default 1 check (canonical_version > 0),
  source_updated_at timestamptz not null,
  calculated_at timestamptz not null default now(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (company_id, fiscal_year, fiscal_quarter),
  unique (company_id, period_end),
  check (period_start is null or period_end >= period_start),
  check (missing_metrics <@ array['revenue', 'operating_income', 'net_income', 'eps']::text[])
);

create index if not exists earnings_quarterly_market_cycle_idx
  on public.earnings_quarterly_financials (market_year desc, market_quarter desc, company_id);

alter table public.earnings_companies enable row level security;
alter table public.earnings_company_identifiers enable row level security;
alter table public.earnings_indices enable row level security;
alter table public.earnings_index_memberships enable row level security;
alter table public.earnings_source_payloads enable row level security;
alter table public.earnings_filings enable row level security;
alter table public.earnings_financial_facts enable row level security;
alter table public.earnings_quarterly_financials enable row level security;

-- The dashboard may read normalized reference and quarterly data. Raw payloads,
-- filing rows, and extracted source facts intentionally have no client policy;
-- only the service role can access them.
drop policy if exists "Authenticated users can read earnings companies" on public.earnings_companies;
create policy "Authenticated users can read earnings companies"
  on public.earnings_companies for select to authenticated using (true);

drop policy if exists "Authenticated users can read earnings company identifiers" on public.earnings_company_identifiers;
create policy "Authenticated users can read earnings company identifiers"
  on public.earnings_company_identifiers for select to authenticated using (true);

drop policy if exists "Authenticated users can read earnings indices" on public.earnings_indices;
create policy "Authenticated users can read earnings indices"
  on public.earnings_indices for select to authenticated using (true);

drop policy if exists "Authenticated users can read earnings index memberships" on public.earnings_index_memberships;
create policy "Authenticated users can read earnings index memberships"
  on public.earnings_index_memberships for select to authenticated using (true);

drop policy if exists "Authenticated users can read earnings quarterly financials" on public.earnings_quarterly_financials;
create policy "Authenticated users can read earnings quarterly financials"
  on public.earnings_quarterly_financials for select to authenticated using (true);

comment on table public.earnings_companies is
  'Deduplicated Korean and U.S. companies used by earnings momentum universes.';
comment on table public.earnings_company_identifiers is
  'Provider identifiers separated from mutable tickers and company display names.';
comment on table public.earnings_index_memberships is
  'Effective-dated universe membership; one company may belong to several indices.';
comment on table public.earnings_source_payloads is
  'Immutable raw provider responses. Secret request values must never be persisted.';
comment on table public.earnings_filings is
  'OpenDART or SEC filing identity and correction lineage.';
comment on table public.earnings_financial_facts is
  'Immutable normalized source facts extracted from each filing payload.';
comment on table public.earnings_quarterly_financials is
  'Current canonical single-quarter financial values used as input to derived metrics.';
