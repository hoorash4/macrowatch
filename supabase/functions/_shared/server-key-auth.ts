import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

function configuredServerKeys(): string[] {
  const keys = new Set<string>();
  const legacy = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")?.trim();
  if (legacy) keys.add(legacy);

  const modern = Deno.env.get("SUPABASE_SECRET_KEYS")?.trim();
  if (modern) {
    try {
      const parsed = JSON.parse(modern) as Record<string, unknown>;
      for (const value of Object.values(parsed)) {
        if (typeof value === "string" && value.trim()) keys.add(value.trim());
      }
    } catch {
      // A malformed platform variable must fail closed, not disable auth.
    }
  }
  return [...keys];
}

/**
 * Accept only a Supabase backend key supplied through the server-only header.
 *
 * The fast path supports platform-provided legacy and modern key variables.
 * The permission probe handles independently named `sb_secret_...` keys used
 * by CI without coupling the function to a particular secret's literal value.
 */
export async function isTrustedServerRequest(
  request: Request,
  supabaseUrl: string,
): Promise<boolean> {
  const supplied = request.headers.get("apikey")?.trim();
  if (!supplied) return false;
  if (configuredServerKeys().includes(supplied)) return true;

  const verifier = createClient(supabaseUrl, supplied, {
    auth: { persistSession: false, autoRefreshToken: false },
  });
  const { error } = await verifier.rpc("earnings_v2_get_company_quarters_many", {
    p_company_ids: [],
  });
  return error === null;
}
