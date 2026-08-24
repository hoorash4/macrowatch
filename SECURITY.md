# Security operations

## Required Supabase secrets

Store secrets only in Supabase Edge Function secrets or GitHub Actions secrets. Never add them to browser JavaScript, migrations, or committed `.env` files.

- `SUPABASE_SERVICE_ROLE_KEY`
- `SUPABASE_ANON_KEY`
- `KAKAO_REST_API_KEY`
- `KAKAO_CLIENT_SECRET`
- `KAKAO_TOKEN_ENCRYPTION_KEY` — a separate, randomly generated secret used only to encrypt Kakao tokens at rest
- `FRED_API_KEY`
- `ECOS_API_KEY`
- `GITHUB_ADMIN_TOKEN`

`kakao-auth` temporarily supports existing plaintext token records and uses the service-role key as a compatibility fallback. Set `KAKAO_TOKEN_ENCRYPTION_KEY`, then have each user reconnect their Kakao account so stored tokens are rewritten in encrypted form. Remove the fallback only after every active token has been migrated.

## Access controls

- Keep GitHub Actions and Supabase secrets scoped to this repository/project.
- Use a fine-grained `GITHUB_ADMIN_TOKEN` limited to this repository and only the permissions needed to dispatch workflows and update the schedule workflow file.
- Keep `is_admin` assignment server-side. Do not allow browser clients to write `user_accounts.is_admin`.
- Review Supabase RLS policies after every schema change. Tables without client policies should stay deny-by-default.

## Abuse controls

Configure authenticated request limits at the Supabase gateway or an upstream WAF:

- `search-indicators`: 60 requests per minute per user
- `check-one-target`: 12 requests per minute per user
- `kakao-auth` `start`/`exchange`: 10 requests per minute per IP

The edge functions validate identity and bound request sizes, but a durable distributed rate limit must be provided by the platform layer.

## Rotation and incident response

- Rotate Kakao, Supabase service-role, GitHub admin, FRED, ECOS, and Google service-account credentials immediately after suspected exposure.
- After rotating `KAKAO_TOKEN_ENCRYPTION_KEY`, users must reconnect Kakao so tokens can be re-encrypted.
- Verify GitHub Actions logs do not contain secret values before granting repository access.
- Periodically restore a backup into an isolated project to verify that backup encryption and recovery work.
