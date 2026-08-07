-- ============================================================
-- ShowToShip Database Schema
-- Run this in Supabase: Dashboard → SQL Editor → New Query
-- ============================================================

-- ── Tradeshows ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS tradeshows (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name          TEXT NOT NULL,
  website       TEXT,
  location      TEXT,
  country       TEXT,
  industry      TEXT,
  date_start    DATE,
  date_end      DATE,
  attending     BOOLEAN DEFAULT false,
  notes         TEXT,
  created_at    TIMESTAMPTZ DEFAULT NOW(),
  created_by    TEXT
);

-- ── Leads (one row per exhibitor/company) ─────────────────────
CREATE TABLE IF NOT EXISTS leads (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),

  -- Identity
  company_name    TEXT NOT NULL,
  website         TEXT,
  country         TEXT,
  city            TEXT,

  -- Show context
  tradeshow_id    UUID REFERENCES tradeshows(id) ON DELETE SET NULL,
  tradeshow_name  TEXT,          -- denormalised for easy display
  booth_number    TEXT,
  hall            TEXT,

  -- Classification
  industry        TEXT,
  category        TEXT,
  products        TEXT,
  description     TEXT,

  -- Contact info
  contact_name    TEXT,
  contact_title   TEXT,
  email           TEXT,
  email_alts      TEXT,
  email_source    TEXT,
  email_confidence TEXT,         -- high / medium / low
  phone           TEXT,

  -- LinkedIn
  linkedin_url       TEXT,
  linkedin_enriched  JSONB,      -- raw data from LinkedIn/Phantombuster
  linkedin_checked_at TIMESTAMPTZ,

  -- Lead management
  status          TEXT DEFAULT 'new',
                  -- new | contacted | qualified | disqualified | opportunity | closed
  assigned_to     TEXT,          -- team member name or email
  priority        TEXT DEFAULT 'medium',  -- high | medium | low
  score           INT DEFAULT 0, -- 0–100 lead score
  notes           TEXT,
  tags            TEXT[],

  -- Audit
  source_url      TEXT,
  scraped_at      TIMESTAMPTZ,
  created_at      TIMESTAMPTZ DEFAULT NOW(),
  updated_at      TIMESTAMPTZ DEFAULT NOW(),
  updated_by      TEXT
);

-- ── Activity log (team actions on a lead) ─────────────────────
CREATE TABLE IF NOT EXISTS lead_activities (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  lead_id     UUID REFERENCES leads(id) ON DELETE CASCADE,
  actor       TEXT NOT NULL,     -- team member
  action      TEXT NOT NULL,     -- e.g. "status_changed", "note_added", "email_sent"
  detail      TEXT,
  created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- ── Scrape jobs log ───────────────────────────────────────────
CREATE TABLE IF NOT EXISTS scrape_jobs (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tradeshow_id    UUID REFERENCES tradeshows(id) ON DELETE SET NULL,
  tradeshow_name  TEXT,
  urls            TEXT[],
  status          TEXT DEFAULT 'pending',  -- pending|running|done|failed
  leads_found     INT DEFAULT 0,
  leads_new       INT DEFAULT 0,
  leads_updated   INT DEFAULT 0,
  options         JSONB,
  github_run_id   TEXT,
  error           TEXT,
  started_at      TIMESTAMPTZ DEFAULT NOW(),
  completed_at    TIMESTAMPTZ
);

-- ── Unique constraint: one company per show (case/whitespace-insensitive) ─
-- company_name_key normalizes the name so "Acme Corp" and "ACME Corp "
-- collide on the same lead instead of creating duplicate rows, and lets
-- the scraper use an atomic INSERT ... ON CONFLICT upsert.
ALTER TABLE leads ADD COLUMN IF NOT EXISTS company_name_key TEXT
  GENERATED ALWAYS AS (lower(trim(company_name))) STORED;

-- Databases that already had case/whitespace-variant duplicates (e.g. "Solé"
-- scraped twice with different trailing whitespace for the same show) will
-- collide once normalized and block the unique index below. Merge those
-- duplicates first so this script stays safe to re-run against a live,
-- already-populated database: keep the "most complete" row per group, fold in
-- any contact/company fields it's missing from the others, concatenate notes,
-- then drop the losers. status/priority/assigned_to are NOT smart-merged —
-- the winner keeps whatever it already had.
DO $$
DECLARE
  grp RECORD;
  winner_id UUID;
BEGIN
  FOR grp IN
    SELECT lower(trim(company_name)) AS key, tradeshow_id
    FROM leads
    GROUP BY lower(trim(company_name)), tradeshow_id
    HAVING count(*) > 1
  LOOP
    SELECT id INTO winner_id
    FROM leads
    WHERE lower(trim(company_name)) = grp.key AND tradeshow_id IS NOT DISTINCT FROM grp.tradeshow_id
    ORDER BY (
      (email IS NOT NULL)::int + (phone IS NOT NULL)::int + (contact_name IS NOT NULL)::int +
      (contact_title IS NOT NULL)::int + (linkedin_url IS NOT NULL)::int + (description IS NOT NULL)::int +
      (products IS NOT NULL)::int + (booth_number IS NOT NULL)::int + (hall IS NOT NULL)::int
    ) DESC, created_at ASC
    LIMIT 1;

    UPDATE leads w SET
      email          = COALESCE(w.email, l.email),
      phone          = COALESCE(w.phone, l.phone),
      contact_name   = COALESCE(w.contact_name, l.contact_name),
      contact_title  = COALESCE(w.contact_title, l.contact_title),
      linkedin_url   = COALESCE(w.linkedin_url, l.linkedin_url),
      description    = COALESCE(w.description, l.description),
      products       = COALESCE(w.products, l.products),
      booth_number   = COALESCE(w.booth_number, l.booth_number),
      hall           = COALESCE(w.hall, l.hall),
      website        = COALESCE(w.website, l.website),
      notes          = NULLIF(trim(both E'\n' from concat_ws(E'\n', w.notes, l.notes)), '')
    FROM leads l
    WHERE w.id = winner_id
      AND lower(trim(l.company_name)) = grp.key
      AND l.tradeshow_id IS NOT DISTINCT FROM grp.tradeshow_id
      AND l.id <> winner_id;

    DELETE FROM leads
    WHERE lower(trim(company_name)) = grp.key
      AND tradeshow_id IS NOT DISTINCT FROM grp.tradeshow_id
      AND id <> winner_id;
  END LOOP;
END $$;

DROP INDEX IF EXISTS idx_leads_company_show;
CREATE UNIQUE INDEX IF NOT EXISTS idx_leads_company_show ON leads(company_name_key, tradeshow_id);

-- ── Indexes ───────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_leads_tradeshow   ON leads(tradeshow_id);
CREATE INDEX IF NOT EXISTS idx_leads_status      ON leads(status);
CREATE INDEX IF NOT EXISTS idx_leads_assigned    ON leads(assigned_to);
CREATE INDEX IF NOT EXISTS idx_leads_company     ON leads(company_name);
CREATE INDEX IF NOT EXISTS idx_leads_email       ON leads(email);
CREATE INDEX IF NOT EXISTS idx_activities_lead   ON lead_activities(lead_id);

-- ── Auto-update updated_at ────────────────────────────────────
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN NEW.updated_at = NOW(); RETURN NEW; END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE TRIGGER leads_updated_at
  BEFORE UPDATE ON leads
  FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- ── User profiles (extends Supabase Auth) ────────────────────
CREATE TABLE IF NOT EXISTS user_profiles (
  id         UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
  email      TEXT NOT NULL,
  role       TEXT DEFAULT 'user' CHECK (role IN ('admin', 'user')),
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Auto-create profile on signup (triggered by auth.users insert)
CREATE OR REPLACE FUNCTION handle_new_user()
RETURNS TRIGGER AS $$
BEGIN
  INSERT INTO public.user_profiles (id, email, role)
  VALUES (NEW.id, NEW.email,
    CASE WHEN (SELECT COUNT(*) FROM public.user_profiles) = 0 THEN 'admin' ELSE 'user' END
  )
  ON CONFLICT (id) DO NOTHING;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users;
CREATE TRIGGER on_auth_user_created
  AFTER INSERT ON auth.users
  FOR EACH ROW EXECUTE FUNCTION handle_new_user();

-- Make the first user an admin (run once after setting up auth)
-- UPDATE user_profiles SET role = 'admin' WHERE id = (SELECT id FROM auth.users ORDER BY created_at LIMIT 1);

-- ── Row Level Security ────────────────────────────────────────
-- Only authenticated users can access data.
ALTER TABLE leads          ENABLE ROW LEVEL SECURITY;
ALTER TABLE tradeshows     ENABLE ROW LEVEL SECURITY;
ALTER TABLE lead_activities ENABLE ROW LEVEL SECURITY;
ALTER TABLE scrape_jobs    ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_profiles  ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Auth access — leads" ON leads;
CREATE POLICY "Auth access — leads"          ON leads          FOR ALL USING (auth.role() = 'authenticated') WITH CHECK (auth.role() = 'authenticated');
DROP POLICY IF EXISTS "Auth access — tradeshows" ON tradeshows;
CREATE POLICY "Auth access — tradeshows"     ON tradeshows     FOR ALL USING (auth.role() = 'authenticated') WITH CHECK (auth.role() = 'authenticated');
DROP POLICY IF EXISTS "Auth access — lead_activities" ON lead_activities;
CREATE POLICY "Auth access — lead_activities" ON lead_activities FOR ALL USING (auth.role() = 'authenticated') WITH CHECK (auth.role() = 'authenticated');
DROP POLICY IF EXISTS "Auth access — scrape_jobs" ON scrape_jobs;
CREATE POLICY "Auth access — scrape_jobs"    ON scrape_jobs    FOR ALL USING (auth.role() = 'authenticated') WITH CHECK (auth.role() = 'authenticated');

-- user_profiles holds the `role` column that gates admin access (see admin-users
-- edge function), so it can't use the same blanket "any authenticated user can
-- write anything" policy as the other tables — that let any signed-in user run
-- `UPDATE user_profiles SET role='admin' WHERE id=<self>` directly against
-- PostgREST and self-promote to admin.
DROP POLICY IF EXISTS "Auth access — user_profiles" ON user_profiles;
CREATE POLICY "Profiles readable by authenticated users" ON user_profiles
  FOR SELECT USING (auth.role() = 'authenticated');
CREATE POLICY "Users update own profile" ON user_profiles
  FOR UPDATE USING (auth.uid() = id) WITH CHECK (auth.uid() = id);
-- No INSERT/DELETE policy for authenticated/anon: rows are created only by the
-- handle_new_user() trigger and deleted only by the admin-users edge function,
-- both of which run with elevated privileges that bypass RLS.

-- Belt-and-suspenders: even a user updating their own row (allowed above for
-- editing their email) can never write the role column — only migrations
-- and the service-role key (which bypasses column grants like RLS) can.
REVOKE UPDATE (role) ON user_profiles FROM authenticated, anon;

-- ── Migration: add contact_title to existing database ──────────
ALTER TABLE leads ADD COLUMN IF NOT EXISTS contact_title TEXT;

-- ── Sample tradeshow data ────────────────────────────────────
INSERT INTO tradeshows (name, location, country, industry, date_start, date_end, attending)
VALUES
  ('Hannover Messe 2025', 'Hannover', 'Germany', 'Industrial Technology', '2025-03-31', '2025-04-04', true),
  ('CES 2026', 'Las Vegas', 'USA', 'Consumer Electronics', '2026-01-06', '2026-01-09', false),
  ('Mobile World Congress 2026', 'Barcelona', 'Spain', 'Telecoms', '2026-03-02', '2026-03-05', true)
ON CONFLICT DO NOTHING;
