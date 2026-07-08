import { serve } from 'https://deno.land/std@0.168.0/http/server.ts'
import { createClient } from 'https://esm.sh/@supabase/supabase-js@2'

const CORS_HEADERS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'POST, OPTIONS',
  'Access-Control-Allow-Headers': 'Authorization, Content-Type',
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
        urls: urls.split('\n').map(s => s.trim()).filter(Boolean),
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
