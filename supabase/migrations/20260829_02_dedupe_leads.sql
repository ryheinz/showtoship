-- ============================================================
-- Collapse duplicate leads and make the dedup key actually hold
-- Run AFTER 20260829_01_fix_profile_rls.sql. Safe to re-run.
--
-- WARNING: step 2 DELETES rows. Take a database snapshot first.
-- ============================================================

-- ── 2. Deduplicate leads before tightening the index ──────────
-- The old unique index was leads(company_name, tradeshow_id). Postgres treats
-- NULLs as distinct, so every CSV import (which never set tradeshow_id) could
-- insert unlimited copies. Collapse existing duplicates, keeping the richest
-- row of each group: most non-null fields wins, oldest breaks the tie.

WITH ranked AS (
  SELECT
    id,
    ROW_NUMBER() OVER (
      PARTITION BY lower(trim(company_name)),
                   COALESCE(tradeshow_id::text, lower(coalesce(tradeshow_name, '')))
      ORDER BY
        (   (email        IS NOT NULL)::int
          + (phone        IS NOT NULL)::int
          + (contact_name IS NOT NULL)::int
          + (website      IS NOT NULL)::int
          + (linkedin_url IS NOT NULL)::int
          + (booth_number IS NOT NULL)::int
        ) DESC,
        created_at ASC
    ) AS rn
  FROM leads
  WHERE company_name IS NOT NULL AND trim(company_name) <> ''
)
DELETE FROM leads WHERE id IN (SELECT id FROM ranked WHERE rn > 1);

-- ── 3. A dedup key that actually holds ────────────────────────
-- Case-insensitive, and falls back to the show NAME when tradeshow_id is null
-- so CSV imports dedupe against scraped rows for the same show.

DROP INDEX IF EXISTS idx_leads_company_show;

CREATE UNIQUE INDEX IF NOT EXISTS idx_leads_company_show
  ON leads (
    lower(trim(company_name)),
    COALESCE(tradeshow_id::text, lower(coalesce(tradeshow_name, '')))
  );

-- ── 4. Backfill tradeshow_id where the name already matches ───
UPDATE leads l
   SET tradeshow_id = t.id
  FROM tradeshows t
 WHERE l.tradeshow_id IS NULL
   AND l.tradeshow_name IS NOT NULL
   AND lower(trim(l.tradeshow_name)) = lower(trim(t.name));

-- ── 5. Contact title column (was applied inline in schema.sql) ─
ALTER TABLE leads ADD COLUMN IF NOT EXISTS contact_title TEXT;
