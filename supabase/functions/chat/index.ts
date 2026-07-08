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
    const { message, company_name, history } = body
    if (!message) {
      return new Response(JSON.stringify({ error: 'message is required' }), {
        status: 400, headers: { ...CORS_HEADERS, 'Content-Type': 'application/json' },
      })
    }

    const openaiKey = Deno.env.get('OPENAI_API_KEY')
    if (!openaiKey) {
      return new Response(JSON.stringify({ error: 'AI not configured — ask your admin to add OPENAI_API_KEY secret' }), {
        status: 503, headers: { ...CORS_HEADERS, 'Content-Type': 'application/json' },
      })
    }

    const serviceRoleKey = Deno.env.get('SERVICE_ROLE_KEY')
    if (!serviceRoleKey) {
      return new Response(JSON.stringify({ error: 'Server misconfigured' }), {
        status: 500, headers: { ...CORS_HEADERS, 'Content-Type': 'application/json' },
      })
    }

    const adminClient = createClient(supabaseUrl, serviceRoleKey)

    // ── Gather context ──
    let companyContext = ''
    if (company_name) {
      const { data: leads } = await adminClient
        .from('leads')
        .select('*')
        .ilike('company_name', `%${company_name}%`)
        .limit(5)
      if (leads && leads.length > 0) {
        companyContext = leads.map(l =>
          `- ${l.company_name} (${l.country || '?'}) — ${l.industry || ''} ${l.products || ''} — Contact: ${l.contact_name || '?'} ${l.contact_title || ''} — Email: ${l.email || '?'}`
        ).join('\n')
      }
    }

    const { data: recentLeads } = await adminClient
      .from('leads')
      .select('company_name, country, industry, category, products, status, email, contact_name, contact_title, website, linkedin_url')
      .limit(30)

    const leadsSummary = recentLeads && recentLeads.length > 0
      ? recentLeads.map(l =>
          `${l.company_name} | ${l.country || '?'} | ${l.industry || l.category || '?'} | ${l.status} | ${l.contact_name || ''}${l.contact_title ? ' ('+l.contact_title+')' : ''} | ${l.email || ''}`
        ).join('\n')
      : 'No leads found yet'

    const needContacts = /find.*(contact|person|people|ceo|founder|director)|who.*(ceo|founder|runs|leads)/i.test(message)
    let webSearchResults = ''
    if (needContacts && company_name) {
      try {
        const query = encodeURIComponent(`${company_name} CEO founder contact`)
        const searchRes = await fetch(`https://html.duckduckgo.com/html/?q=${query}`, {
          headers: { 'User-Agent': 'Mozilla/5.0' },
        })
        if (searchRes.ok) {
          const html = await searchRes.text()
          const results: string[] = []
          const linkRe = /<a[^>]+class="result__a"[^>]*>([\s\S]*?)<\/a>/gi
          const snippetRe = /<a[^>]+class="result__snippet"[^>]*>([\s\S]*?)<\/a>/gi
          let m: RegExpExecArray | null
          let idx = 0
          while ((m = linkRe.exec(html)) !== null && idx < 5) {
            results.push(m[1].replace(/<[^>]+>/g, '').trim())
            idx++
          }
          if (results.length === 0) {
            const allText = html.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ')
            const sentences = allText.match(/[^.]*?(?:CEO|founder|director|contact)[^.]*\./gi) || []
            sentences.slice(0, 5).forEach(s => results.push(s.trim()))
          }
          webSearchResults = results.length > 0
            ? 'Web search results for people at this company:\n' + results.join('\n')
            : ''
        }
      } catch {
        // web search failed, continue without it
      }
    }

    // ── Build system prompt ──
    const systemPrompt = `You are a sales intelligence assistant for ShowToShip, a tool that scrapes tradeshow exhibitor lists to find leads.

The user's current leads (company | country | industry | status | contact | email):
${leadsSummary}

${companyContext ? `\nCompany context:\n${companyContext}\n` : ''}
${webSearchResults ? `\n${webSearchResults}\n` : ''}

Rules:
- Answer naturally and conversationally
- When asked about a company, share what's in the database and suggest next steps
- When asked to find contacts/people, check the database first, then suggest web search results
- If you don't know something, say so — don't make up data
- Keep responses concise and actionable
- You can help: research companies, find contacts, analyze lead quality, suggest prioritization`

    const messages = [
      { role: 'system', content: systemPrompt },
      ...((history as Array<{role: string; content: string}> | undefined) || []).slice(-10),
      { role: 'user', content: message },
    ]

    const openaiRes = await fetch('https://api.openai.com/v1/chat/completions', {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${openaiKey}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        model: 'gpt-4o-mini',
        messages,
        temperature: 0.3,
        max_tokens: 800,
      }),
    })

    if (!openaiRes.ok) {
      const errText = await openaiRes.text()
      return new Response(JSON.stringify({ error: `AI error: ${errText.slice(0, 200)}` }), {
        status: 502, headers: { ...CORS_HEADERS, 'Content-Type': 'application/json' },
      })
    }

    const openaiData = await openaiRes.json()
    const reply = openaiData.choices?.[0]?.message?.content || ''

    return new Response(JSON.stringify({ reply }), {
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
