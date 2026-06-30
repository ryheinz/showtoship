import { serve } from 'https://deno.land/std@0.168.0/http/server.ts'
import { createClient } from 'https://esm.sh/@supabase/supabase-js@2'

const CORS_HEADERS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'GET, POST, DELETE, OPTIONS',
  'Access-Control-Allow-Headers': 'Authorization, Content-Type',
}

function json(data: unknown, status = 200) {
  return new Response(JSON.stringify(data), {
    status, headers: { ...CORS_HEADERS, 'Content-Type': 'application/json' },
  })
}

serve(async (req) => {
  if (req.method === 'OPTIONS') return new Response('ok', { headers: CORS_HEADERS })

  const authHeader = req.headers.get('Authorization')!
  if (!authHeader) return json({ error: 'Unauthorized' }, 401)

  const supabase = createClient(
    Deno.env.get('SUPABASE_URL')!,
    Deno.env.get('SUPABASE_ANON_KEY')!,
    { global: { headers: { Authorization: authHeader } } }
  )

  const { data: { user } } = await supabase.auth.getUser()
  if (!user) return json({ error: 'Unauthorized' }, 401)

  const { data: profile } = await supabase
    .from('user_profiles')
    .select('role')
    .eq('id', user.id)
    .single()

  if (profile?.role !== 'admin') return json({ error: 'Forbidden' }, 403)

  const adminClient = createClient(
    Deno.env.get('SUPABASE_URL')!,
    Deno.env.get('SERVICE_ROLE_KEY')!
  )

  const url = new URL(req.url)
  const path = url.pathname.replace(/\/$/, '')

  // GET /users - list all users
  if (req.method === 'GET' && path.endsWith('/users')) {
    const { data, error } = await adminClient.auth.admin.listUsers()
    if (error) return json({ error: error.message }, 500)
    const userIds = data.users.map(u => u.id)
    const { data: profiles } = await supabase
      .from('user_profiles')
      .select('id, role')
      .in('id', userIds)
    const roleMap: Record<string, string> = {}
    if (profiles) profiles.forEach(p => { roleMap[p.id] = p.role })
    const users = data.users.map(u => ({
      id: u.id,
      email: u.email,
      created_at: u.created_at,
      role: roleMap[u.id] || 'user',
    }))
    return json({ users })
  }

  // POST /users - create a new user
  if (req.method === 'POST' && path.endsWith('/users')) {
    const { email, password } = await req.json()
    const { data, error } = await adminClient.auth.admin.createUser({
      email, password, email_confirm: true,
    })
    if (error) return json({ error: error.message }, 400)
    if (data.user) {
      await supabase.from('user_profiles').insert({
        id: data.user.id,
        email: data.user.email,
        role: 'user',
      }).maybeSingle()
    }
    return json(data)
  }

  // DELETE /users/:id - delete a user
  if (req.method === 'DELETE' && path.includes('/users/')) {
    const userId = path.split('/').pop()
    if (!userId) return json({ error: 'Missing user ID' }, 400)
    await supabase.from('user_profiles').delete().eq('id', userId)
    const { error } = await adminClient.auth.admin.deleteUser(userId)
    if (error) return json({ error: error.message }, 500)
    return json({ ok: true })
  }

  return json({ error: 'Not found' }, 404)
})
