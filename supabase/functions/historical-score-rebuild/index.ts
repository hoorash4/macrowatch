import 'jsr:@supabase/functions-js/edge-runtime.d.ts';
import { createClient } from 'https://esm.sh/@supabase/supabase-js@2';
import { recomputeHistoricalScore } from '../_shared/pivot/historical-score-store.ts';

const origin = 'https://hoorash4.github.io';
const headers = {'Access-Control-Allow-Origin': origin, 'Access-Control-Allow-Headers': 'authorization, apikey, content-type',
  'Access-Control-Allow-Methods': 'POST, OPTIONS', 'Content-Type': 'application/json'};
const reply = (body: unknown, status = 200) => new Response(JSON.stringify(body), {status, headers});
const codePattern = /^[A-Za-z0-9_:-]{1,100}$/;
const indexes = new Set(['SP500', 'NASDAQ_COMPOSITE', 'KOSPI']);

Deno.serve(async request => {
  if (request.method === 'OPTIONS') return new Response(null, {status: 204, headers});
  if (request.method !== 'POST') return reply({error: 'POST 요청만 허용됩니다.'}, 405);
  try {
    const url = Deno.env.get('SUPABASE_URL'), serviceKey = Deno.env.get('SUPABASE_SERVICE_ROLE_KEY');
    if (!url || !serviceKey) throw new Error('데이터 연결 설정이 없습니다.');
    const token = (request.headers.get('Authorization') || '').replace(/^Bearer\s+/i, '');
    if (!token) return reply({error: '로그인이 필요합니다.'}, 401);
    const admin = createClient(url, serviceKey, {auth: {persistSession: false, autoRefreshToken: false}});
    if (token !== serviceKey) {
      const {data: auth, error: authError} = await admin.auth.getUser(token);
      if (authError || !auth.user) return reply({error: '로그인이 필요합니다.'}, 401);
      const {data: account, error: accountError} = await admin.from('user_accounts').select('is_admin')
        .eq('user_id', auth.user.id).maybeSingle();
      if (accountError) throw accountError;
      if (account?.is_admin !== true) return reply({error: '관리자 권한이 필요합니다.'}, 403);
    }
    const input = await request.json();
    const caseCode = String(input?.case_code || ''), indexCode = String(input?.index_code || '');
    const seriesCodes = [...new Set((Array.isArray(input?.series_codes) ? input.series_codes : []).map(String))];
    if (!codePattern.test(caseCode) || !indexes.has(indexCode) || !seriesCodes.length || seriesCodes.length > 30
      || seriesCodes.some(code => !codePattern.test(code))) return reply({error: '점수 재계산 대상이 올바르지 않습니다.'}, 400);
    const results = [];
    for (let i = 0; i < seriesCodes.length; i += 3) {
      results.push(...await Promise.all(seriesCodes.slice(i, i + 3).map(async seriesCode =>
        ({series_code: seriesCode, ...await recomputeHistoricalScore(admin, caseCode, indexCode, seriesCode)}))));
    }
    return reply({items: results});
  } catch (error) {
    console.error('[Historical score rebuild]', error);
    return reply({error: error instanceof Error ? error.message : String(error)}, 500);
  }
});

