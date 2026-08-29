-- ============================================================
-- Enforce RLS on leads and tradeshows
--
-- The live database diverged from schema.sql: RLS was not in effect on
-- `leads` or `tradeshows`, so the anon key — which is embedded in the public
-- frontend and readable by anyone who views source — could read AND write
-- every row. Verified against the live project: an unauthenticated request
-- returned a count of 4,309 leads, and an unauthenticated PATCH was accepted.
--
-- `scrape_jobs`, `lead_activities` and `user_profiles` were already enforcing
-- correctly, which is what makes divergence the likely cause rather than a
-- schema bug: RLS was probably switched off on these two tables so that the
-- GitHub Actions scraper could write using the anon key.
--
--   ORDER MATTERS. Before running this, set the SUPABASE_KEY secret in
--   GitHub Actions to the **service_role** key. The scraper runs
--   unauthenticated; under RLS the anon key cannot write, so enabling this
--   first would leave scrapes completing having saved nothing.
--
-- Run in Supabase → SQL Editor. Safe to re-run.
-- ============================================================

-- ── 1. What is the current state? ─────────────────────────────
-- Read the output before continuing. relrowsecurity should be true for all
-- five tables; any policy whose roles include `anon` or `public` is what
-- grants unauthenticated access and must be dropped by name below.

SELECT c.relname AS table_name,
       c.relrowsecurity AS rls_enabled,
       c.relforcerowsecurity AS rls_forced
  FROM pg_class c
  JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE n.nspname = 'public'
   AND c.relname IN ('leads', 'tradeshows', 'scrape_jobs', 'lead_activities', 'user_profiles')
 ORDER BY c.relname;

SELECT tablename, policyname, roles, cmd, qual, with_check
  FROM pg_policies
 WHERE schemaname = 'public'
 ORDER BY tablename, policyname;

-- ── 2. Turn RLS back on ───────────────────────────────────────
ALTER TABLE public.leads          ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.tradeshows     ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.lead_activities ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.scrape_jobs    ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.user_profiles  ENABLE ROW LEVEL SECURITY;

-- ── 3. Recreate the team-access policies ──────────────────────
-- Dropped and recreated so the definition is known, rather than whatever the
-- live database happens to hold.

DROP POLICY IF EXISTS "Auth access — leads"           ON public.leads;
DROP POLICY IF EXISTS "Auth access — tradeshows"      ON public.tradeshows;
DROP POLICY IF EXISTS "Auth access — lead_activities" ON public.lead_activities;
DROP POLICY IF EXISTS "Auth access — scrape_jobs"     ON public.scrape_jobs;

CREATE POLICY "Auth access — leads"           ON public.leads
  FOR ALL TO authenticated USING (true) WITH CHECK (true);
CREATE POLICY "Auth access — tradeshows"      ON public.tradeshows
  FOR ALL TO authenticated USING (true) WITH CHECK (true);
CREATE POLICY "Auth access — lead_activities" ON public.lead_activities
  FOR ALL TO authenticated USING (true) WITH CHECK (true);
CREATE POLICY "Auth access — scrape_jobs"     ON public.scrape_jobs
  FOR ALL TO authenticated USING (true) WITH CHECK (true);

-- `TO authenticated` is the meaningful change: the previous policies were
-- written as `USING (auth.role() = 'authenticated')` with no role restriction,
-- so they applied to every role and depended entirely on that expression. The
-- role clause makes the intent explicit and cannot be satisfied by anon.

-- ── 4. If step 1 listed any policy granting anon/public, drop it ──
-- Uncomment and fill in the names it printed. A single permissive policy for
-- `anon` re-opens the table no matter what the rest of this file does.
--
-- DROP POLICY IF EXISTS "<name from step 1>" ON public.leads;
-- DROP POLICY IF EXISTS "<name from step 1>" ON public.tradeshows;

-- ── 5. Verify ─────────────────────────────────────────────────
-- Re-run step 1: rls_enabled must be true for all five tables and no policy
-- may list anon or public in `roles`.
--
-- Then confirm from outside the database — this must return an empty array,
-- not rows (replace ANON_KEY with the key from frontend/index.html):
--
--   curl "https://<project>.supabase.co/rest/v1/leads?select=id&limit=1" \
--     -H "apikey: ANON_KEY" -H "Authorization: Bearer ANON_KEY"
--
-- And confirm the app still works when signed in, and that a scrape still
-- writes (it needs the service_role key — see the warning at the top).
