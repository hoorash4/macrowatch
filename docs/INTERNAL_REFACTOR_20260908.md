# Internal refactor — in progress, not a completion report

## Contract and rollback

- Baseline: `ecc3888`; rollback branch: `codex/restore-before-internal-20260908`.
- Working branch: `codex/deep-internal-refactor`.
- Preserve rendered UI, numerical outputs, API/DB contracts, schedules, notifications and collection policies.
- Review every tracked file. Review does not require changing every file.
- Historical migrations, archived versions and backfill-only code are not rewritten for cosmetic consistency.
- User clarified priority: active frontend, automatic collectors and Edge Functions. No further v2/v2.5 backfill refactoring.
- Earlier uncommitted earnings extractions remain under review; not deployed.

## Review ledger

Only files whose full contents have been examined are marked reviewed below.
Partial inspection, automated scanning and passing tests do not mean a full review.

| File | Review / decision | Verification |
| --- | --- | --- |
| assets/js/core/config.js | Reviewed; public configuration already centralized; retain | No change |
| assets/js/core/frontend-core.js | Reviewed; share existing authenticated/anonymous HTTP handling | Five offline request/refresh/error tests pass |
| assets/js/core/auth.js | Reviewed; remove duplicate HTTP code and unreachable private login launcher; separate element binding, login, profile and account events in the same file | Syntax passed; browser and event parity pending |
| assets/js/core/autocomplete-off.js | Reviewed; existing dynamic-form observer is intentional; retain | No change |
| assets/js/admin/admin-card-order.js | Reviewed; drag lifecycle and saved-order reconciliation already own their state; retain | No change |
| assets/js/admin/admin-policy-review.js | Reviewed; policy-review workflow separate; no forced generic CRUD abstraction | No change |
| assets/js/admin/admin.js | Reviewed; workflow control mapping and default schedule centralized | Offline suite passed; live UI pending |
| assets/js/dashboard/script.js | Reviewed; collection state evaluated once per target; scroll threshold centralized | Offline suite passed; live UI pending |

### Additional full-file reviews

- Frontend charts: analysis-chart-utils, dashboard-charts, korea-earnings-chart,
  korea-foreign-flow-chart, em-capacity-chart, policy-expectation-chart,
  equity-bond-attractiveness-chart, liquidity-chart, policy-chart. Common pagination,
  timeline widths and SVG element creation reused only where semantics match.
  Earnings segment paths now computed once per metric per viewport update.
  Liquidity's distinct resize/scroll lifecycle and fixed-score chart axes retained.
- policy-briefing.js: retain; escaping differs intentionally from generic frontend
  escaping (apostrophes/null semantics). Do not unify by name alone.
- Python signals: em_stress_pipeline, financial_stress_pipeline,
  korea_stress_pipeline, equity_bond_attractiveness, equity_bond_model,
  equity_bond_attractiveness_pipeline, equity_bond_pipeline, liquidity_pipeline,
  policy_expectation_pipeline, em_capital_capacity_pipeline.
  Preserve all calculation/collection windows. Repeated window scans replaced with
  indexed slices; shared rule keys reused; Korea monthly assembly isolated.
- Python common.py; sources/market.py and sources/financial_stress.py;
  tracking/check_targets.py; operations/backup_crypto.py,
  operations/upload_drive_backup.py, operations/send_policy_briefing_alerts.py:
  reviewed; distinct provider/alert/backup semantics retained.
- US earnings pipeline.py, automatic_cli.py, repository.py, models.py,
  aggregation.py, providers.py, transform.py: reviewed. Pending-company grouping,
  shared persisted filing payload and complete-component aggregation centralized.
  SEC fiscal/date priorities, requests, timeouts and retry policies retained.
- Edge entry points: admin-control (including github, validation, news-review,
  sector-registry), check-one-target, notification-settings, search-indicators,
  kakao-auth, market-context, korea-foreign-flow, sector-flow,
  sector-flow-scheduler, kis-market-test, news-pipeline, policy-pipeline.
  Identical server JSON response construction shared; CORS variants retained.
  News source list derives from RSS_FEEDS; all eight feeds remain enabled.
- Shared Edge market files: kis-client, korea-foreign-flow, market-context,
  market-indicators, sector-flow. Sector history uses findLast without reverse copies.
  Shared news files: news-types and openai-adapter. Shared policy files: ai-policy,
  policy-admin, policy-score-store, policy-scoring, policy-types. Retained policies.
- Build: pages-compat.cjs and check_pages_build.cjs. Legacy auth alias includes its
  current shared request dependency, as the dashboard alias already does for charts.
- Config: .gitignore, backend/requirements.txt, supabase/config.toml;
  workflows/code-quality.yml, pages-build.yml, deploy-supabase.yml reviewed.
  Deploy workflow replays migrations; those SQL effects still require checking
  before merge. No workflow or database changes made.

### Checkpoint verification (not production completion)

- Latest full local run: 348 Python tests pass, JavaScript syntax and Node tests pass.
- US test module: 104 tests pass when backend is explicitly on PYTHONPATH.
- New offline tests: authenticated/anonymous calls and refresh/error behavior (5),
  unsorted/duplicate visible-axis boundaries (1), market request order/key encoding/
  250ms spacing/stop-on-failure and JSON response contract (3), all news feeds with
  partial failure preservation (1), earnings repository contract (5).
- Earlier numerical comparison with ecc3888: seeded US/Korea attractiveness rows
  and logistic model coefficients matched exactly; no production recalculation.
- Not yet complete: full CSS/HTML review, remaining modules and historical-file
  references, CI Deno type checking, browser parity, deployment and final DB checks.

## Remaining scope

- Remaining frontend JS, both HTML entry points and complete CSS cascade.
- Active Python collectors, sources, tracking/operations modules and US automatic pipeline.
- All Edge Function entry points and shared modules.
- Workflows, build tooling, configuration, tests, documentation and archive/runtime references.
- File-by-file ledger expansion, behavior regression tests, deployment and read-only live verification.

## Safeguards

- Auth: only pre-session OAuth requests may use the publishable token; authenticated 401 refresh remains bounded to once.
- No actual account mutation, email sending, paid AI invocation or financial backfill as a refactoring test.
- CSS specificity/cascade and cached public asset URLs must be preserved before deployment.
- Do not report this document or this partial ledger as a completed whole-project audit.
