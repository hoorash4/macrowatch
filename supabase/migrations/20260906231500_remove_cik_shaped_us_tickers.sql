-- A historical Nasdaq-100 fallback once passed SEC CIKs through the ticker
-- field. Every affected issuer also has a valid ticker identifier, so these
-- malformed aliases are safe to remove.
delete from earnings_v2.company_identifiers
where identifier_type = 'ticker'
  and identifier_value ~ '^[0-9]{10}$';

