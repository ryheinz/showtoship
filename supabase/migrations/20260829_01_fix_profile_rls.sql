-- ============================================================
-- Fix privilege escalation on user_profiles
-- Run in Supabase → SQL Editor → New Query. Safe to re-run.
-- ============================================================
-- ── 1. Privilege escalation ───────────────────────────────────
-- The old policy was FOR ALL on user_profiles, so any signed-in user could
-- PATCH their own row to role='admin' and unlock the admin-users function.
-- Roles are now writable only by the service role (i.e. the edge function),
-- which bypasses RLS entirely.

DROP POLICY IF EXISTS "Auth access — user_profiles" ON user_profiles;

-- Everyone signed in may READ profiles (the app needs the team member list
-- for the "Assign to" dropdown). That is the only client-side access.
CREATE POLICY "profiles readable by authenticated"
  ON user_profiles FOR SELECT
  USING (auth.role() = 'authenticated');

-- Deliberately NO insert/update/delete policy. Profiles are written by the
-- handle_new_user trigger (SECURITY DEFINER) and by the admin-users edge
-- function under the service role; both bypass RLS. Note that a policy on
-- user_profiles must never sub-select from user_profiles — Postgres raises
-- "infinite recursion detected in policy" — which is why role changes live
-- in the edge function rather than in a WITH CHECK here.

