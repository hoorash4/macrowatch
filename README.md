# MacroWatch

MacroWatch is the Evotive Research macro monitoring dashboard and alerting service.

## Runtime

- Frontend: static HTML/CSS/JavaScript served from GitHub Pages
- Backend jobs: Python collectors executed by GitHub Actions
- Database and Edge Functions: Supabase
- Scheduled collectors: GitHub Actions and explicitly scoped Supabase cron jobs

## Operational boundaries

Automatic collectors, manual recalculation, historical bootstrap/backfill, repair, maintenance, health checks, and deployments are intentionally separated. Scheduled collectors must not perform unrelated historical rewrites or maintenance side effects.

## Economic indicator charts

`economic-charts.html` provides the dedicated economic-indicator chart workspace. Externally published observations are stored in `economic_chart_points`, reusable calculations in `economic_chart_derived_points`, and charts read their union through `economic_chart_series_points`. Scheduled collection appends recent observations while historical backfill remains an explicit manual mode.
