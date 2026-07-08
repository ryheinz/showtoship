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
        status: 405, headers: { ...CORS_HEADERS, 'Content-Type': 'application/json' },
      })
    }

    const authHeader = req.headers.get('Authorization')
    if (!authHeader) {
      return new Response(JSON.stringify({ error: 'Unauthorized' }), {
        status: 401, headers: { ...CORS_HEADERS, 'Content-Type': 'application/json' },
      })
    }

    const supabaseUrl = Deno.env.get('SUPABASE_URL')
    const supabaseAnonKey = Deno.env.get('SUPABASE_ANON_KEY')
    if (!supabaseUrl || !supabaseAnonKey) {
      return new Response(JSON.stringify({ error: 'Server misconfigured' }), {
        status: 500, headers: { ...CORS_HEADERS, 'Content-Type': 'application/json' },
      })
    }

    const supabase = createClient(supabaseUrl, supabaseAnonKey, {
      global: { headers: { Authorization: authHeader } },
    })

    const { data: { user }, error: authError } = await supabase.auth.getUser()
    if (authError || !user) {
      return new Response(JSON.stringify({ error: 'Unauthorized' }), {
        status: 401, headers: { ...CORS_HEADERS, 'Content-Type': 'application/json' },
      })
    }

    const body = await req.json().catch(() => ({}))
    const { website, company_name, api_key } = body
    if (!website && !company_name) {
      return new Response(JSON.stringify({ error: 'website or company_name is required' }), {
        status: 400, headers: { ...CORS_HEADERS, 'Content-Type': 'application/json' },
      })
    }

    // Use key from request body (browser) or server secret (admin-configured)
    const apiKey = api_key || Deno.env.get('HUNTER_API_KEY')
    if (!apiKey) {
      return new Response(JSON.stringify({ error: 'Hunter API key not configured — set it in Settings' }), {
        status: 503, headers: { ...CORS_HEADERS, 'Content-Type': 'application/json' },
      })
    }

    // Extract domain from website
    let domain = ''
    if (website) {
      try {
        domain = new URL(website).hostname.replace('www.', '')
      } catch {
        domain = website.replace(/https?:\/\//, '').replace('www.', '').split('/')[0]
      }
    }

    if (!domain) {
      return new Response(JSON.stringify({ error: 'Could not extract domain from website' }), {
        status: 400, headers: { ...CORS_HEADERS, 'Content-Type': 'application/json' },
      })
    }

    // Call Hunter.io API
    const hunterRes = await fetch(
      `https://api.hunter.io/v2/domain-search?domain=${encodeURIComponent(domain)}&api_key=${apiKey}`
    )

    if (!hunterRes.ok) {
      const errText = await hunterRes.text().catch(() => '')
      return new Response(JSON.stringify({ error: `Hunter API error: ${errText.slice(0, 200)}` }), {
        status: 502, headers: { ...CORS_HEADERS, 'Content-Type': 'application/json' },
      })
    }

    const hunterData = await hunterRes.json()

    const emails = hunterData?.data?.emails || []
    const contacts = emails.map((e: any) => ({
      name: [e.first_name, e.last_name].filter(Boolean).join(' '),
      title: e.position || '',
      email: e.value || '',
      confidence: e.confidence || 0,
      seniority: e.seniority || '',
      department: e.department || '',
      phone: e.phone_number || '',
      linkedin_url: e.linkedin_url || '',
      twitter: e.twitter || '',
    }))

    // Also try to get the organization name
    const organization = hunterData?.data?.organization || ''

    return new Response(JSON.stringify({
      domain,
      organization,
      contacts,
      total: hunterData?.meta?.results || contacts.length,
    }), {
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
