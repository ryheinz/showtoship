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

-- ── Unique constraint: one company per show ──────────────────
CREATE UNIQUE INDEX IF NOT EXISTS idx_leads_company_show ON leads(company_name, tradeshow_id);

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

-- ── Row Level Security ────────────────────────────────────────
-- Enables public access (anyone with anon key can read/write).
-- For production with sensitive data, enable auth and replace
-- the USING expressions with `auth.role() = 'authenticated'`.
ALTER TABLE leads          ENABLE ROW LEVEL SECURITY;
ALTER TABLE tradeshows     ENABLE ROW LEVEL SECURITY;
ALTER TABLE lead_activities ENABLE ROW LEVEL SECURITY;
ALTER TABLE scrape_jobs    ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Public access — leads"          ON leads          FOR ALL USING (true) WITH CHECK (true);
CREATE POLICY "Public access — tradeshows"     ON tradeshows     FOR ALL USING (true) WITH CHECK (true);
CREATE POLICY "Public access — lead_activities" ON lead_activities FOR ALL USING (true) WITH CHECK (true);
CREATE POLICY "Public access — scrape_jobs"    ON scrape_jobs    FOR ALL USING (true) WITH CHECK (true);

-- ── Sample tradeshow data ────────────────────────────────────
INSERT INTO tradeshows (name, location, country, industry, date_start, date_end, attending)
VALUES
  ('Hannover Messe 2025', 'Hannover', 'Germany', 'Industrial Technology', '2025-03-31', '2025-04-04', true),
  ('CES 2026', 'Las Vegas', 'USA', 'Consumer Electronics', '2026-01-06', '2026-01-09', false),
  ('Mobile World Congress 2026', 'Barcelona', 'Spain', 'Telecoms', '2026-03-02', '2026-03-05', true)
ON CONFLICT DO NOTHING;
