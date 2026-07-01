import { serve } from 'https://deno.land/std@0.168.0/http/server.ts'
import { createClient } from 'https://esm.sh/@supabase/supabase-js@2'

const CORS_HEADERS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'POST, OPTIONS',
  'Access-Control-Allow-Headers': 'Authorization, x-api-key, x-lead-apikey, Content-Type',
}

serve(async (req) => {
  try {
    if (req.method === 'OPTIONS') return new Response('ok', { headers: CORS_HEADERS })

    // Auth: check x-api-key
    const extApiKey = req.headers.get('x-api-key') || req.headers.get('x-lead-apikey')
    const expectedKey = Deno.env.get('EXTENSION_API_KEY')
    if (!extApiKey || extApiKey !== expectedKey) {
      return new Response(JSON.stringify({ error: 'Unauthorized' }), {
        status: 401,
        headers: { ...CORS_HEADERS, 'Content-Type': 'application/json' },
      })
    }

    if (req.method !== 'POST') {
      return new Response(JSON.stringify({ error: 'Method not allowed' }), {
        status: 405,
        headers: { ...CORS_HEADERS, 'Content-Type': 'application/json' },
      })
    }

    const supabaseUrl = Deno.env.get('SUPABASE_URL')
    const serviceRoleKey = Deno.env.get('SERVICE_ROLE_KEY')
    if (!supabaseUrl || !serviceRoleKey) {
      return new Response(JSON.stringify({ error: 'Server misconfigured' }), {
        status: 500,
        headers: { ...CORS_HEADERS, 'Content-Type': 'application/json' },
      })
    }

    const supabase = createClient(supabaseUrl, serviceRoleKey)

    const body = await req.json().catch(() => ({}))
    const {
      tradeshow_name,
      company_name,
      contact_name,
      email,
      phone,
      linkedin_url,
      website,
      city,
      country,
      title,
    } = body

    if (!company_name && !contact_name) {
      return new Response(JSON.stringify({ error: 'company_name or contact_name required' }), {
        status: 400,
        headers: { ...CORS_HEADERS, 'Content-Type': 'application/json' },
      })
    }

    // Find or create the tradeshow record
    let tradeshowId: string | null = null
    if (tradeshow_name) {
      const { data: existing } = await supabase
        .from('tradeshows')
        .select('id')
        .ilike('name', tradeshow_name.trim())
        .maybeSingle()

      if (existing) {
        tradeshowId = existing.id
      } else {
        const { data: created } = await supabase
          .from('tradeshows')
          .insert({ name: tradeshow_name.trim() })
          .select('id')
          .maybeSingle()
        if (created) tradeshowId = created.id
      }
    }

    // Build the lead row
    const notes = title ? `LinkedIn Headline: ${title}` : null

    const lead = {
      company_name: company_name || null,
      contact_name: contact_name || null,
      email: email || null,
      phone: phone || null,
      linkedin_url: linkedin_url || null,
      website: website || null,
      city: city || null,
      country: country || null,
      notes: notes,
      tradeshow_id: tradeshowId,
      tradeshow_name: tradeshow_name || null,
      source_url: linkedin_url || null,
      status: 'new',
      priority: 'medium',
    }

    const { data, error } = await supabase
      .from('leads')
      .insert(lead)
      .select()
      .maybeSingle()

    if (error) {
      return new Response(JSON.stringify({ error: error.message }), {
        status: 500,
        headers: { ...CORS_HEADERS, 'Content-Type': 'application/json' },
      })
    }

    return new Response(JSON.stringify({ success: true, lead: data }), {
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
