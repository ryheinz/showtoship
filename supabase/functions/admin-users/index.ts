import { serve } from 'https://deno.land/std@0.168.0/http/server.ts'
import { createClient } from 'https://esm.sh/@supabase/supabase-js@2'

const CORS_HEADERS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'GET, POST, PATCH, DELETE, OPTIONS',
  'Access-Control-Allow-Headers': 'Authorization, Content-Type',
}

function json(data: unknown, status = 200) {
  return new Response(JSON.stringify(data), {
    status, headers: { ...CORS_HEADERS, 'Content-Type': 'application/json' },
  })
}

serve(async (req) => {
  if (req.method === 'OPTIONS') return new Response('ok', { headers: CORS_HEADERS })

  const authHeader = req.headers.get('Authorization')
  if (!authHeader) return json({ error: 'Unauthorized' }, 401)

  const supabaseAnonKey = Deno.env.get('SUPABASE_ANON_KEY')
  const supabaseUrl = Deno.env.get('SUPABASE_URL')
  if (!supabaseAnonKey) return json({ error: 'Missing SUPABASE_ANON_KEY env' }, 500)
  if (!supabaseUrl) return json({ error: 'Missing SUPABASE_URL env' }, 500)

  const supabase = createClient(
    supabaseUrl,
    supabaseAnonKey,
    { global: { headers: { Authorization: authHeader } } }
  )

  const { data: { user }, error: getUserError } = await supabase.auth.getUser()
  if (getUserError) return json({ error: 'Auth getUser failed: ' + getUserError.message }, 500)
  if (!user) return json({ error: 'Unauthorized' }, 401)

  const { data: profile } = await supabase
    .from('user_profiles')
    .select('role')
    .eq('id', user.id)
    .single()

  if (profile?.role !== 'admin') return json({ error: 'Forbidden - not admin' }, 403)

  const serviceRoleKey = Deno.env.get('SERVICE_ROLE_KEY')
  if (!serviceRoleKey) return json({ error: 'Missing SERVICE_ROLE_KEY secret' }, 500)

  const adminClient = createClient(supabaseUrl, serviceRoleKey)

  const url = new URL(req.url)
  const path = url.pathname.replace(/\/$/, '')

  // GET /users - list all users
  if (req.method === 'GET' && path.endsWith('/users')) {
    const { data, error } = await adminClient.auth.admin.listUsers()
    if (error) return json({ error: 'listUsers: ' + error.message }, 500)
    const userIds = data.users.map(u => u.id)
    const { data: profiles } = await adminClient
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
      // The admin page needs to distinguish "invited, never signed in" from
      // "active" — that distinction is what made onboarding opaque before.
      confirmed: Boolean(u.email_confirmed_at || u.confirmed_at),
      last_sign_in_at: u.last_sign_in_at ?? null,
    }))
    return json({ users })
  }

  // POST /users - invite a new user by email
  if (req.method === 'POST' && path.endsWith('/users')) {
    const { email, role: requestedRole } = await req.json()
    if (!email) return json({ error: 'Email required' }, 400)
    if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(String(email).trim())) {
      return json({ error: 'That does not look like a valid email address' }, 400)
    }
    const newRole = requestedRole === 'admin' ? 'admin' : 'user'
    // redirectTo must be an allowed URL in Supabase Auth → URL Configuration,
    // otherwise the invite link bounces to the site root and the set-password
    // screen never sees the token.
    const appUrl = Deno.env.get('APP_URL')
    const { data, error } = await adminClient.auth.admin.inviteUserByEmail(
      String(email).trim(),
      appUrl ? { redirectTo: appUrl } : undefined,
    )
    if (error) return json({ error: error.message }, 400)
    if (data.user) {
      // adminClient (service role): the tightened RLS on user_profiles no longer
      // grants INSERT to the caller's own authenticated session. upsert rather
      // than insert because handle_new_user may already have created the row.
      await adminClient.from('user_profiles').upsert({
        id: data.user.id,
        email: data.user.email,
        role: newRole,
      }, { onConflict: 'id' })
      // Notify the admin who created this user
      const resendKey = Deno.env.get('RESEND_API_KEY')
      if (resendKey && user.email) {
        try {
          await fetch('https://api.resend.com/emails', {
            method: 'POST',
            headers: { Authorization: 'Bearer ' + resendKey, 'Content-Type': 'application/json' },
            body: JSON.stringify({
              from: Deno.env.get('RESEND_FROM') || 'ShowToShip <onboarding@resend.dev>',
              to: [user.email],
              subject: 'New user invited to ShowToShip',
              text: `${email} was invited to ShowToShip by ${user.email}.`,
            }),
          })
        } catch { /* notification is best-effort */ }
      }
    }
    return json(data)
  }

  // POST /users/:id/resend - re-send the invite email to someone who never
  // completed setup (or lost the link — invite tokens expire).
  if (req.method === 'POST' && path.endsWith('/resend')) {
    const parts = path.split('/')
    const userId = parts[parts.length - 2]
    const { data: target, error: lookupError } = await adminClient.auth.admin.getUserById(userId)
    if (lookupError || !target.user?.email) return json({ error: 'User not found' }, 404)

    const appUrl = Deno.env.get('APP_URL')
    // inviteUserByEmail refuses an address that already exists, so generate a
    // fresh invite link for the existing account instead.
    const { data: link, error: linkError } = await adminClient.auth.admin.generateLink({
      type: 'invite',
      email: target.user.email,
      options: appUrl ? { redirectTo: appUrl } : undefined,
    })
    if (linkError) return json({ error: linkError.message }, 400)
    return json({ ok: true, email: target.user.email, action_link: link?.properties?.action_link ?? null })
  }

  // PATCH /users/:id/role - promote or demote
  if (req.method === 'PATCH' && path.endsWith('/role')) {
    const parts = path.split('/')
    const userId = parts[parts.length - 2]
    const { role: newRole } = await req.json()
    if (newRole !== 'admin' && newRole !== 'user') {
      return json({ error: "role must be 'admin' or 'user'" }, 400)
    }
    if (userId === user.id && newRole !== 'admin') {
      // Guard against an admin locking everyone out of the admin page.
      const { count } = await adminClient
        .from('user_profiles')
        .select('id', { count: 'exact', head: true })
        .eq('role', 'admin')
      if ((count ?? 0) <= 1) {
        return json({ error: 'You are the only admin — promote someone else first' }, 400)
      }
    }
    const { error } = await adminClient
      .from('user_profiles')
      .update({ role: newRole })
      .eq('id', userId)
    if (error) return json({ error: error.message }, 500)
    return json({ ok: true, role: newRole })
  }

  // DELETE /users/:id - delete a user
  if (req.method === 'DELETE' && path.includes('/users/')) {
    const userId = path.split('/').pop()
    if (!userId) return json({ error: 'Missing user ID' }, 400)
    if (userId === user.id) return json({ error: 'You cannot delete your own account' }, 400)
    // adminClient (service role): tightened RLS no longer grants DELETE to the
    // caller's session. Delete the auth user FIRST — user_profiles cascades from
    // auth.users, so removing the profile first would strand the account if this
    // call then failed.
    const { error } = await adminClient.auth.admin.deleteUser(userId)
    if (error) return json({ error: error.message }, 500)
    await adminClient.from('user_profiles').delete().eq('id', userId)
    return json({ ok: true })
  }

  return json({ error: 'Not found' }, 404)
})
