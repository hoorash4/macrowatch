import "jsr:@supabase/functions-js/edge-runtime.d.ts";
import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

const ALLOWED_ORIGIN = "https://hoorash4.github.io";
const REDIRECT_URI = "https://hoorash4.github.io/macrowatch/";
const encoder = new TextEncoder();

function corsHeaders(origin: string | null) {
  return {
    "Access-Control-Allow-Origin": origin === ALLOWED_ORIGIN ? origin : ALLOWED_ORIGIN,
    "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Vary": "Origin",
  };
}

function json(body: unknown, status: number, origin: string | null) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { ...corsHeaders(origin), "Content-Type": "application/json; charset=utf-8" },
  });
}

function toBase64Url(bytes: Uint8Array) {
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function fromBase64Url(value: string) {
  const normalized = value.replace(/-/g, "+").replace(/_/g, "/");
  return atob(normalized.padEnd(Math.ceil(normalized.length / 4) * 4, "="));
}

function fromBase64UrlBytes(value: string) {
  return Uint8Array.from(fromBase64Url(value), (character) => character.charCodeAt(0));
}

async function signState(payload: string, secret: string) {
  const key = await crypto.subtle.importKey(
    "raw",
    encoder.encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const signature = await crypto.subtle.sign("HMAC", key, encoder.encode(payload));
  return toBase64Url(new Uint8Array(signature));
}

async function createState(secret: string) {
  const payload = toBase64Url(encoder.encode(JSON.stringify({
    nonce: crypto.randomUUID(),
    expires_at: Date.now() + 10 * 60 * 1000,
  })));
  return `${payload}.${await signState(payload, secret)}`;
}

async function verifyState(state: string, secret: string) {
  const [payload, signature] = state.split(".");
  if (!payload || !signature || await signState(payload, secret) !== signature) return false;
  try {
    const parsed = JSON.parse(fromBase64Url(payload));
    return Number(parsed.expires_at) > Date.now();
  } catch {
    return false;
  }
}

async function tokenEncryptionKey(secret: string) {
  const material = await crypto.subtle.digest(
    "SHA-256",
    encoder.encode(`macrowatch/kakao-token/v1\0${secret}`),
  );
  return crypto.subtle.importKey("raw", material, { name: "AES-GCM" }, false, ["encrypt", "decrypt"]);
}

async function encryptToken(token: string | undefined, secret: string) {
  if (!token) return null;
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const ciphertext = await crypto.subtle.encrypt(
    { name: "AES-GCM", iv },
    await tokenEncryptionKey(secret),
    encoder.encode(token),
  );
  return {
    version: 1,
    iv: toBase64Url(iv),
    ciphertext: toBase64Url(new Uint8Array(ciphertext)),
  };
}

async function decryptToken(value: unknown, secret: string) {
  if (typeof value === "string") return value;
  if (!value || typeof value !== "object") return "";
  const encrypted = value as Record<string, unknown>;
  if (encrypted.version !== 1 || typeof encrypted.iv !== "string" || typeof encrypted.ciphertext !== "string") {
    return "";
  }
  const plaintext = await crypto.subtle.decrypt(
    { name: "AES-GCM", iv: fromBase64UrlBytes(encrypted.iv) },
    await tokenEncryptionKey(secret),
    fromBase64UrlBytes(encrypted.ciphertext),
  );
  return new TextDecoder().decode(plaintext);
}

async function unlinkKakaoAccount(
  config: Record<string, unknown>,
  clientId: string,
  clientSecret: string,
  tokenEncryptionSecret: string,
) {
  let accessToken = await decryptToken(config.access_token, tokenEncryptionSecret);
  const refreshToken = await decryptToken(config.refresh_token, tokenEncryptionSecret);
  if (!accessToken && !refreshToken) return;

  const unlink = (token: string) => fetch("https://kapi.kakao.com/v1/user/unlink", {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
  });

  let response = accessToken ? await unlink(accessToken) : null;
  if ((!response || response.status === 401) && refreshToken) {
    const tokenBody = new URLSearchParams({
      grant_type: "refresh_token",
      client_id: clientId,
      refresh_token: refreshToken,
    });
    if (clientSecret) tokenBody.set("client_secret", clientSecret);
    const refreshResponse = await fetch("https://kauth.kakao.com/oauth/token", {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded;charset=utf-8" },
      body: tokenBody,
    });
    const refreshed = await refreshResponse.json();
    if (!refreshResponse.ok || !refreshed.access_token) {
      throw new Error("카카오 연결을 해제하지 못했습니다. 잠시 후 다시 시도해 주세요.");
    }
    accessToken = refreshed.access_token;
    response = await unlink(accessToken);
  }
  if (response && !response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(error.msg || "카카오 연결을 해제하지 못했습니다.");
  }
}

async function authenticatedUser(supabaseUrl: string, anonKey: string, jwt: string) {
  const response = await fetch(`${supabaseUrl}/auth/v1/user`, {
    headers: { apikey: anonKey, Authorization: `Bearer ${jwt}` },
  });
  if (!response.ok) return null;
  const user = await response.json().catch(() => null);
  return user?.id ? user : null;
}

export default {
  async fetch(request: Request) {
    const origin = request.headers.get("Origin");
    if (request.method === "OPTIONS") return new Response("ok", { headers: corsHeaders(origin) });
    if (request.method !== "POST") return json({ error: "Method not allowed" }, 405, origin);

    try {
      const supabaseUrl = Deno.env.get("SUPABASE_URL")!;
      const serviceRoleKey = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!;
      const anonKey = Deno.env.get("SUPABASE_ANON_KEY")!;
      const kakaoClientId = Deno.env.get("KAKAO_REST_API_KEY")!;
      const kakaoClientSecret = Deno.env.get("KAKAO_CLIENT_SECRET") || "";
      if (!kakaoClientId) return json({ error: "카카오 API 키가 설정되지 않았습니다." }, 500, origin);

      const admin = createClient(supabaseUrl, serviceRoleKey, {
        auth: { persistSession: false, autoRefreshToken: false },
      });
      const body = await request.json();
      const action = String(body?.action || "");
      const stateSecret = kakaoClientSecret || serviceRoleKey;
      const tokenEncryptionSecret = Deno.env.get("KAKAO_TOKEN_ENCRYPTION_KEY") || serviceRoleKey;

      if (action === "start") {
        const state = await createState(stateSecret);
        const authorizeUrl = new URL("https://kauth.kakao.com/oauth/authorize");
        authorizeUrl.searchParams.set("client_id", kakaoClientId);
        authorizeUrl.searchParams.set("redirect_uri", REDIRECT_URI);
        authorizeUrl.searchParams.set("response_type", "code");
        authorizeUrl.searchParams.set("scope", "talk_message");
        authorizeUrl.searchParams.set("state", state);
        return json({ authorize_url: authorizeUrl.toString(), state }, 200, origin);
      }

      if (action === "exchange") {
        const code = String(body?.code || "");
        const state = String(body?.state || "");
        if (!code || !await verifyState(state, stateSecret)) {
          return json({ error: "카카오 로그인 요청이 만료되었거나 올바르지 않습니다." }, 400, origin);
        }

        const tokenBody = new URLSearchParams({
          grant_type: "authorization_code",
          client_id: kakaoClientId,
          redirect_uri: REDIRECT_URI,
          code,
        });
        if (kakaoClientSecret) tokenBody.set("client_secret", kakaoClientSecret);
        const tokenResponse = await fetch("https://kauth.kakao.com/oauth/token", {
          method: "POST",
          headers: { "Content-Type": "application/x-www-form-urlencoded;charset=utf-8" },
          body: tokenBody,
        });
        const tokenData = await tokenResponse.json();
        if (!tokenResponse.ok || !tokenData.access_token) {
          return json({ error: tokenData.error_description || "카카오 로그인에 실패했습니다." }, 400, origin);
        }

        const tokenInfoResponse = await fetch("https://kapi.kakao.com/v1/user/access_token_info", {
          headers: { Authorization: `Bearer ${tokenData.access_token}` },
        });
        const tokenInfo = await tokenInfoResponse.json();
        if (!tokenInfoResponse.ok || !tokenInfo?.id) {
          return json({ error: "카카오 사용자 정보를 확인하지 못했습니다." }, 400, origin);
        }

        const kakaoUserId = String(tokenInfo.id);
        const internalEmail = `kakao-${kakaoUserId}@users.macrowatch.invalid`;
        const password = `${crypto.randomUUID()}${crypto.randomUUID()}`;
        const { data: account } = await admin
          .from("user_accounts")
          .select("user_id")
          .eq("kakao_user_id", kakaoUserId)
          .maybeSingle();

        let userId = account?.user_id;
        let firstAccount = false;
        if (!userId) {
          const { count } = await admin
            .from("user_accounts")
            .select("*", { count: "exact", head: true });
          firstAccount = count === 0;
          const { data: created, error: createError } = await admin.auth.admin.createUser({
            email: internalEmail,
            password,
            email_confirm: true,
            user_metadata: { auth_provider: "kakao" },
          });
          if (createError || !created.user) throw createError || new Error("계정을 만들지 못했습니다.");
          userId = created.user.id;
          const { error: accountError } = await admin.from("user_accounts").insert({
            user_id: userId,
            kakao_user_id: kakaoUserId,
          });
          if (accountError) throw accountError;
        } else {
          const { error: updateError } = await admin.auth.admin.updateUserById(userId, {
            password,
          });
          if (updateError) throw updateError;
        }

        const now = Date.now();
        const { error: channelError } = await admin.from("notification_channels").upsert({
          user_id: userId,
          channel: "kakao_self",
          config: {
            connected: true,
            wants_kakao: true,
            kakao_user_id: kakaoUserId,
            access_token: await encryptToken(tokenData.access_token, tokenEncryptionSecret),
            refresh_token: await encryptToken(tokenData.refresh_token, tokenEncryptionSecret),
            access_expires_at: new Date(now + Number(tokenData.expires_in || 0) * 1000).toISOString(),
            refresh_expires_at: tokenData.refresh_token_expires_in
              ? new Date(now + Number(tokenData.refresh_token_expires_in) * 1000).toISOString()
              : null,
            connected_at: new Date().toISOString(),
          },
          is_active: true,
          updated_at: new Date().toISOString(),
        }, { onConflict: "user_id,channel" });
        if (channelError) throw channelError;

        if (firstAccount) {
          await admin.from("targets").update({ user_id: userId }).is("user_id", null);
        }

        const client = createClient(supabaseUrl, anonKey, {
          auth: { persistSession: false, autoRefreshToken: false },
        });
        const { data: signedIn, error: signInError } = await client.auth.signInWithPassword({
          email: internalEmail,
          password,
        });
        if (signInError || !signedIn.session) {
          throw signInError || new Error("로그인 세션을 만들지 못했습니다.");
        }

        return json({
          access_token: signedIn.session.access_token,
          refresh_token: signedIn.session.refresh_token,
        }, 200, origin);
      }

      const jwt = (request.headers.get("Authorization") || "").replace(/^Bearer\s+/i, "");
      if (!jwt) return json({ error: "로그인이 필요합니다." }, 401, origin);
      const requestApiKey = request.headers.get("apikey") || anonKey;
      const user = await authenticatedUser(supabaseUrl, requestApiKey, jwt);
      if (!user) {
        return json({ error: "로그인 정보가 유효하지 않습니다." }, 401, origin);
      }

      if (action === "status") {
        const { data: channel } = await admin
          .from("notification_channels")
          .select("config, is_active")
          .eq("user_id", user.id)
          .eq("channel", "kakao_self")
          .maybeSingle();
        const config = channel?.config && typeof channel.config === "object" ? channel.config : {};
        return json({
          connected: Boolean(config.connected),
          is_active: channel?.is_active !== false,
        }, 200, origin);
      }

      if (action === "delete_account") {
        const { data: channel } = await admin
          .from("notification_channels")
          .select("config")
          .eq("user_id", user.id)
          .eq("channel", "kakao_self")
          .maybeSingle();
        const config = channel?.config && typeof channel.config === "object"
          ? channel.config as Record<string, unknown>
          : {};
        await unlinkKakaoAccount(config, kakaoClientId, kakaoClientSecret, tokenEncryptionSecret);

        await admin.from("device_tokens").delete().eq("user_id", user.id);
        await admin.from("notification_channels").delete().eq("user_id", user.id);
        await admin.from("targets").delete().eq("user_id", user.id);
        const { error: deleteError } = await admin.auth.admin.deleteUser(user.id);
        if (deleteError) throw deleteError;
        return json({ deleted: true }, 200, origin);
      }

      return json({ error: "지원하지 않는 요청입니다." }, 400, origin);
    } catch (error) {
      return json({ error: error instanceof Error ? error.message : "요청 처리에 실패했습니다." }, 500, origin);
    }
  },
};

