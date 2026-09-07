import "jsr:@supabase/functions-js/edge-runtime.d.ts";
import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

const ALLOWED_ORIGIN = "https://hoorash4.github.io";

function headers(origin: string | null) {
  return {
    "Access-Control-Allow-Origin": origin === ALLOWED_ORIGIN ? origin : ALLOWED_ORIGIN,
    "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Vary": "Origin",
  };
}

function json(body: unknown, status: number, origin: string | null) {
  return new Response(JSON.stringify(body), { status, headers: { ...headers(origin), "Content-Type": "application/json; charset=utf-8" } });
}

function emailAddress(value: unknown) {
  const address = String(value || "").trim().toLowerCase();
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(address) || address.length > 254) {
    throw new Error("유효한 이메일 주소를 입력해 주세요.");
  }
  return address;
}

Deno.serve(async (request) => {
  const origin = request.headers.get("Origin");
  if (request.method === "OPTIONS") return new Response("ok", { headers: headers(origin) });
  if (request.method !== "POST") return json({ error: "POST 요청만 허용됩니다." }, 405, origin);
  try {
    const supabaseUrl = Deno.env.get("SUPABASE_URL")!;
    const anonKey = Deno.env.get("SUPABASE_ANON_KEY")!;
    const serviceRoleKey = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!;
    const jwt = (request.headers.get("Authorization") || "").replace(/^Bearer\s+/i, "");
    const auth = createClient(supabaseUrl, anonKey, { auth: { persistSession: false, autoRefreshToken: false } });
    const { data: { user }, error: userError } = await auth.auth.getUser(jwt);
    if (userError || !user) return json({ error: "로그인이 필요합니다." }, 401, origin);
    const admin = createClient(supabaseUrl, serviceRoleKey, { auth: { persistSession: false, autoRefreshToken: false } });
    const body = await request.json().catch(() => ({}));
    const action = String(body?.action || "");
    if (action === "status") {
      const { data, error } = await admin.from("notification_channels")
        .select("config,is_active").eq("user_id", user.id).eq("channel", "email").maybeSingle();
      if (error) throw error;
      const config = data?.config && typeof data.config === "object" ? data.config as Record<string, unknown> : {};
      return json({ address: typeof config.address === "string" ? config.address : "", is_active: data?.is_active === true }, 200, origin);
    }
    if (action === "save") {
      const address = emailAddress(body?.address);
      const { error } = await admin.from("notification_channels").upsert({
        user_id: user.id, channel: "email", config: { address }, is_active: true, updated_at: new Date().toISOString(),
      }, { onConflict: "user_id,channel" });
      if (error) throw error;
      return json({ address, is_active: true }, 200, origin);
    }
    if (action === "remove") {
      const { error } = await admin.from("notification_channels").delete().eq("user_id", user.id).eq("channel", "email");
      if (error) throw error;
      return json({ removed: true }, 200, origin);
    }
    return json({ error: "지원하지 않는 요청입니다." }, 400, origin);
  } catch (error) {
    return json({ error: error instanceof Error ? error.message : "이메일 알림 설정을 처리하지 못했습니다." }, 400, origin);
  }
});
