-- ============================================================
-- Verify and repair RLS on the LIVE database
--
-- schema.sql enables row-level security and defines the policies. The live
-- project had drifted from it: RLS was not in effect on `leads` or
-- `tradeshows`, so the anon key — embedded in the public frontend and readable
-- by anyone who views source — could read AND write every row.
--
-- Verified against the live project on 2026-08-29: an unauthenticated request
-- to /rest/v1/leads returned a count of 4,309, and an unauthenticated PATCH was
-- accepted. scrape_jobs, lead_activities and user_profiles all returned zero
-- rows, so RLS was working there — which points at those two tables having been
-- switched off deliberately, most likely so the Actions scraper could write
-- using the anon key.
--
-- Re-running schema.sql fixes this too; this file exists to make the drift
-- visible and to be safe to run on its own against a populated database. It
-- deliberately does NOT touch the dedup index or company_name_key — those are
-- owned by schema.sql and the scraper's ON CONFLICT depends on their exact
-- shape.
--
--   ORDER MATTERS. Set the GitHub Actions SUPABASE_KEY secret to the
--   **service_role** key BEFORE running this. The scraper runs
--   unauthenticated; under RLS the anon key cannot write, so enabling this
--   first would leave scrapes completing having saved nothing.
--
-- Run in Supabase → SQL Editor. Safe to re-run.
-- ============================================================

-- ── 1. Current state — read this before continuing ────────────
-- rls_enabled must be true for all five tables. Any policy whose `roles`
-- include `anon` or `public` is what grants unauthenticated access; note its
-- name, because step 3 cannot guess it.

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

-- ── 2. Re-enable RLS ──────────────────────────────────────────
-- Policies are defined in schema.sql and are left alone here; this only
-- restores enforcement, which is the part that had drifted.

ALTER TABLE public.leads           ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.tradeshows      ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.lead_activities ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.scrape_jobs     ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.user_profiles   ENABLE ROW LEVEL SECURITY;

-- ── 3. Drop any policy that grants anon/public ────────────────
-- One permissive anon policy re-opens the table regardless of the above.
-- Uncomment with the names step 1 printed:
--
-- DROP POLICY IF EXISTS "<name from step 1>" ON public.leads;
-- DROP POLICY IF EXISTS "<name from step 1>" ON public.tradeshows;

-- ── 4. Verify ─────────────────────────────────────────────────
-- Re-run step 1: rls_enabled true everywhere, no policy listing anon/public.
--
-- Then confirm from outside the database. This must return an empty array,
-- not rows (ANON_KEY is the SB_KEY constant in frontend/index.html):
--
--   curl "https://<project>.supabase.co/rest/v1/leads?select=id&limit=1" \
--     -H "apikey: ANON_KEY" -H "Authorization: Bearer ANON_KEY"
--
-- Finally, confirm the app still works signed in, and that a scrape still
-- writes — it needs the service_role key, per the warning at the top.
