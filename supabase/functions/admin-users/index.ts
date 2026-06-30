import { serve } from 'https://deno.land/std@0.168.0/http/server.ts'
import { createClient } from 'https://esm.sh/@supabase/supabase-js@2'

serve(async (req) => {
  const authHeader = req.headers.get('Authorization')!
  if (!authHeader) return new Response('Unauthorized', { status: 401 })

  const supabase = createClient(
    Deno.env.get('SUPABASE_URL')!,
    Deno.env.get('SUPABASE_ANON_KEY')!,
    { global: { headers: { Authorization: authHeader } } }
  )

  const { data: { user } } = await supabase.auth.getUser()
  if (!user) return new Response('Unauthorized', { status: 401 })

  const { data: profile } = await supabase
    .from('user_profiles')
    .select('role')
    .eq('id', user.id)
    .single()

  if (profile?.role !== 'admin') return new Response('Forbidden', { status: 403 })

  const adminClient = createClient(
    Deno.env.get('SUPABASE_URL')!,
    Deno.env.get('SERVICE_ROLE_KEY')!
  )

  const url = new URL(req.url)
  const path = url.pathname.replace(/\/$/, '')

  // GET /users - list all users
  if (req.method === 'GET' && path.endsWith('/users')) {
    const { data, error } = await adminClient.auth.admin.listUsers()
    if (error) return new Response(error.message, { status: 500 })
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
    return new Response(JSON.stringify({ users }), {
      headers: { 'Content-Type': 'application/json' },
    })
  }

  // POST /users - create a new user
  if (req.method === 'POST' && path.endsWith('/users')) {
    const { email, password } = await req.json()
    const { data, error } = await adminClient.auth.admin.createUser({
      email, password, email_confirm: true,
    })
    if (error) return new Response(JSON.stringify({ error: error.message }), { status: 400 })
    if (data.user) {
      await supabase.from('user_profiles').insert({
        id: data.user.id,
        email: data.user.email,
        role: 'user',
      }).maybeSingle()
    }
    return new Response(JSON.stringify(data), {
      headers: { 'Content-Type': 'application/json' },
    })
  }

  // DELETE /users/:id - delete a user
  if (req.method === 'DELETE' && path.includes('/users/')) {
    const userId = path.split('/').pop()
    if (!userId) return new Response('Missing user ID', { status: 400 })
    await supabase.from('user_profiles').delete().eq('id', userId)
    const { error } = await adminClient.auth.admin.deleteUser(userId)
    if (error) return new Response(error.message, { status: 500 })
    return new Response('OK')
  }

  return new Response('Not Found', { status: 404 })
})
