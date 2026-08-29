import { serve } from 'https://deno.land/std@0.168.0/http/server.ts'
import { createClient } from 'https://esm.sh/@supabase/supabase-js@2'

const CORS_HEADERS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'POST, OPTIONS',
  'Access-Control-Allow-Headers': 'Authorization, Content-Type',
}

// Any authenticated team member can dispatch a scrape (that's the app's normal
// workflow), but the target URLs are attacker-controllable input handed straight
// to a GitHub Actions runner — reject anything that isn't a plain public http(s)
// URL so this can't be used to probe internal/link-local addresses or the cloud
// metadata endpoint from the runner's network.
const MAX_URLS_PER_JOB = 50

function isSafeExhibitorUrl(raw: string): boolean {
  let u: URL
  try { u = new URL(raw) } catch { return false }
  if (u.protocol !== 'http:' && u.protocol !== 'https:') return false
  const host = u.hostname.toLowerCase().replace(/^\[|\]$/g, '')
  if (host === 'localhost' || host === '0.0.0.0' || host === '169.254.169.254') return false
  if (/^127\./.test(host) || /^10\./.test(host) || /^192\.168\./.test(host)) return false
  if (/^172\.(1[6-9]|2\d|3[01])\./.test(host)) return false
  if (host === '::1' || host.startsWith('fe80:') || host.startsWith('fc') || host.startsWith('fd')) return false
  return true
}

serve(async (req) => {
  try {
    if (req.method === 'OPTIONS') return new Response('ok', { headers: CORS_HEADERS })
    if (req.method !== 'POST') {
      return new Response(JSON.stringify({ error: 'Method not allowed' }), {
        status: 405,
        headers: { ...CORS_HEADERS, 'Content-Type': 'application/json' },
      })
    }

    const authHeader = req.headers.get('Authorization')
    if (!authHeader) {
      return new Response(JSON.stringify({ error: 'Unauthorized' }), {
        status: 401,
        headers: { ...CORS_HEADERS, 'Content-Type': 'application/json' },
      })
    }

    const supabaseAnonKey = Deno.env.get('SUPABASE_ANON_KEY')
    const supabaseUrl = Deno.env.get('SUPABASE_URL')
    if (!supabaseAnonKey || !supabaseUrl) {
      return new Response(JSON.stringify({ error: 'Server misconfigured' }), {
        status: 500,
        headers: { ...CORS_HEADERS, 'Content-Type': 'application/json' },
      })
    }

    const supabase = createClient(supabaseUrl, supabaseAnonKey, {
      global: { headers: { Authorization: authHeader } },
    })

    const { data: { user }, error: getUserError } = await supabase.auth.getUser()
    if (getUserError || !user) {
      return new Response(JSON.stringify({ error: 'Unauthorized' }), {
        status: 401,
        headers: { ...CORS_HEADERS, 'Content-Type': 'application/json' },
      })
    }

    const body = await req.json().catch(() => ({}))
    const showName = body.show_name?.trim()
    const urls = body.urls?.trim()
    if (!showName || !urls) {
      return new Response(JSON.stringify({ error: 'show_name and urls are required' }), {
        status: 400,
        headers: { ...CORS_HEADERS, 'Content-Type': 'application/json' },
      })
    }

    const urlList = urls.split('\n').map((s: string) => s.trim()).filter(Boolean)
    if (urlList.length === 0) {
      return new Response(JSON.stringify({ error: 'At least one URL is required' }), {
        status: 400,
        headers: { ...CORS_HEADERS, 'Content-Type': 'application/json' },
      })
    }
    if (urlList.length > MAX_URLS_PER_JOB) {
      return new Response(JSON.stringify({ error: `Too many URLs — max ${MAX_URLS_PER_JOB} per job` }), {
        status: 400,
        headers: { ...CORS_HEADERS, 'Content-Type': 'application/json' },
      })
    }
    const unsafeUrl = urlList.find((u: string) => !isSafeExhibitorUrl(u))
    if (unsafeUrl) {
      return new Response(JSON.stringify({ error: `Invalid or disallowed URL: ${unsafeUrl}` }), {
        status: 400,
        headers: { ...CORS_HEADERS, 'Content-Type': 'application/json' },
      })
    }

    const githubPat = Deno.env.get('GITHUB_PAT')
    const githubRepo = Deno.env.get('GITHUB_REPO')
    const serviceRoleKey = Deno.env.get('SERVICE_ROLE_KEY')
    if (!githubPat || !githubRepo || !serviceRoleKey) {
      return new Response(JSON.stringify({ error: 'Server misconfigured' }), {
        status: 500,
        headers: { ...CORS_HEADERS, 'Content-Type': 'application/json' },
      })
    }

    const adminClient = createClient(supabaseUrl, serviceRoleKey)

    const now = new Date().toISOString()
    const { data: job, error: jobError } = await adminClient
      .from('scrape_jobs')
      .insert({
        tradeshow_name: showName,
        urls: urlList,
        status: 'pending',
        options: {
          use_llm: body.use_llm === 'true',
          deep_scrape: body.deep_scrape === 'true',
          find_emails: body.find_emails === 'true',
          linkedin_enrich: body.linkedin_enrich === 'true',
        },
        started_at: now,
      })
      .select('id')
      .single()

    if (jobError || !job) {
      return new Response(JSON.stringify({ error: 'Failed to create job' }), {
        status: 500,
        headers: { ...CORS_HEADERS, 'Content-Type': 'application/json' },
      })
    }

    const jobId = job.id

    const ghRes = await fetch(`https://api.github.com/repos/${githubRepo}/actions/workflows/scrape.yml/dispatches`, {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${githubPat}`,
        Accept: 'application/vnd.github+json',
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        ref: 'main',
        inputs: {
          show_name: showName,
          urls: urls,
          use_llm: body.use_llm || 'false',
          deep_scrape: body.deep_scrape || 'false',
          find_emails: body.find_emails || 'true',
          linkedin_enrich: body.linkedin_enrich || 'false',
          job_id: jobId,
        },
      }),
    })

    if (ghRes.status !== 204) {
      const errText = await ghRes.text().catch(() => 'unknown')
      await adminClient.from('scrape_jobs').update({
        status: 'failed',
        error: `GitHub dispatch failed: ${errText.slice(0, 200)}`,
        completed_at: new Date().toISOString(),
      }).eq('id', jobId)
      return new Response(JSON.stringify({ error: `GitHub dispatch failed: ${errText.slice(0, 200)}` }), {
        status: 502,
        headers: { ...CORS_HEADERS, 'Content-Type': 'application/json' },
      })
    }

    return new Response(JSON.stringify({ job_id: jobId }), {
      status: 200,
      headers: { ...CORS_HEADERS, 'Content-Type': 'application/json' },
    })
  } catch (err) {
    return new Response(JSON.stringify({ error: err instanceof Error ? err.message : String(err) }), {
      status: 500,
      headers: { ...CORS_HEADERS, 'Content-Type': 'application/json' },
    })
  }
})
