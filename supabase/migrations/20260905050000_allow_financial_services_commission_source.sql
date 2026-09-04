alter table earnings_v2.company_quarters
  drop constraint if exists company_quarters_source_check;

alter table earnings_v2.company_quarters
  add constraint company_quarters_source_check
  check (
    source = any (
      array[
        'open_dart'::text,
        'sec_edgar'::text,
        'manual'::text,
        'financial_services_commission'::text,
        'mixed'::text
      ]
    )
  );
