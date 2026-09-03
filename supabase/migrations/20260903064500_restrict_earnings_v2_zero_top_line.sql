-- The zero-revenue fallback is only valid for a nonfinancial company whose
-- complete statement reports an operating loss. Restore earlier profitable
-- matches to pending so they can be retried without keeping a false zero.

update earnings_v2.company_quarters
set top_line = null,
    source_top_line_cumulative = null,
    operating_margin_pct = null,
    net_margin_pct = null,
    is_pending = true,
    quality_status = 'review_required',
    source_filing_id = regexp_replace(source_filing_id, '^zero_top_line:', ''),
    updated_at = now()
where source_filing_id like 'zero_top_line:%'
  and operating_income >= 0;

