-- Activation is no longer an administrator-facing lifecycle. Restore any
-- previously retired rows so the administrator can keep or delete them.
update public.market_sector_etfs set is_active = true where not is_active;
update public.news_extreme_rules set is_active = true where not is_active;
