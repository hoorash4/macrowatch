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

/** Accept only a Supabase backend key supplied through the server-only header. */
export function isTrustedServerRequest(request: Request): boolean {
  const supplied = request.headers.get("apikey")?.trim();
  return Boolean(supplied && configuredServerKeys().includes(supplied));
}
