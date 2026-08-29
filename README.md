# ShowToShip 🔍

**Tradeshow Lead Intelligence Platform** — scrape exhibitor lists, find emails, enrich with LinkedIn, manage your sales pipeline. Entirely free using GitHub.

---

## Architecture

```
GitHub Pages (frontend) ←→ Supabase (database) ←→ GitHub Actions (scraper)
                                                         ↕
                                    Playwright + extractors + email_finder + Phantombuster
```

| Component | Service | Cost |
|---|---|---|
| Web app | GitHub Pages | Free |
| Scraper engine | GitHub Actions | Free (2000 min/mo) |
| Database | Supabase | Free (500MB, unlimited rows) |
| LinkedIn enrichment | Phantombuster | Free tier (2hr/day) |

---

## Deployment

### 1 — Create the GitHub repository
Create a new repo (private recommended) and push these files.

### 2 — Set up the Supabase database
1. [supabase.com](https://supabase.com) → New project (free tier is enough)
2. SQL Editor → New Query → paste `supabase/schema.sql` → Run
3. If you are upgrading an existing install, also run every file in
   `supabase/migrations/` in filename order.

> **Check RLS is actually in effect.** `schema.sql` enables it, but a live
> database can drift — this one had it switched off on `leads` and
> `tradeshows`, which left every lead readable and writable by anyone holding
> the anon key from the page source. `migrations/20260829_03_enforce_rls_on_leads.sql`
> diagnoses and repairs that. Set the Actions `SUPABASE_KEY` secret to the
> service_role key *before* running it, or the scraper will stop writing.
4. Settings → API → copy the **Project URL** and the **anon/public** key
5. Put both into `frontend/index.html` (`SB_URL` / `SB_KEY` near the top of the
   script block). The anon key is *meant* to be public — access is controlled by
   the row-level security policies in `schema.sql`, not by hiding the key.

### 3 — Deploy the edge functions
```bash
supabase link --project-ref YOUR-PROJECT-REF
supabase functions deploy chat dispatch-scrape find-contacts admin-users linkedin-leads
```

Then set the function secrets (Dashboard → Edge Functions → Secrets):

| Secret | Needed for | Notes |
|---|---|---|
| `SERVICE_ROLE_KEY` | all functions | Settings → API → service_role key |
| `GITHUB_PAT` | `dispatch-scrape` | scopes: `repo` + `workflow` |
| `GITHUB_REPO` | `dispatch-scrape` | e.g. `ryheinz/showtoship` |
| `APP_URL` | `admin-users` | your Pages URL — invite links redirect here |
| `OPENAI_API_KEY` *or* `ANTHROPIC_API_KEY` | `chat` | either one; the function picks whichever is set |
| `HUNTER_API_KEY` | `find-contacts` | optional; users can supply their own in Settings |
| `EXTENSION_API_KEY` | `linkedin-leads` | shared secret for the Chrome extension |
| `RESEND_API_KEY`, `RESEND_FROM` | `admin-users` | optional admin notification emails |

### 4 — Allow the invite redirect
Supabase Dashboard → Authentication → URL Configuration → add your Pages URL to
**Redirect URLs**. Without this, invite and password-reset links bounce to the
site root and the set-password screen never receives the token.

### 5 — Add the GitHub Actions secrets
Settings → Secrets and variables → Actions:

| Secret | Value |
|---|---|
| `SUPABASE_URL` | your Project URL |
| `SUPABASE_KEY` | **service_role** key — see the warning below |
| `OPENAI_API_KEY` | optional, for LLM extraction |
| `PHANTOMBUSTER_API_KEY`, `PB_SEARCH_PHANTOM_ID`, `PB_PROFILE_PHANTOM_ID` | optional |

> **`SUPABASE_KEY` must be the service_role key.** RLS only grants access to the
> `authenticated` role, and the scraper runs unauthenticated. With the anon key
> every database write fails, the job never completes, and the UI shows a scrape
> that appears to hang.

### 6 — Enable GitHub Pages
The deploy workflow pushes the built site to a `gh-pages` branch, so set
Settings → Pages → Source: **Deploy from a branch** → `gh-pages` / `(root)`.
(Not "GitHub Actions" — that mode expects a different workflow.)

Also set Settings → Actions → General → **Read and write permissions**.

### 7 — Create the first user
The first account to sign up becomes an admin automatically (see
`handle_new_user` in `schema.sql`). Create it in
Supabase → Authentication → Users → Add user, then sign in at your Pages URL.

From then on, add teammates from the **Admin** page inside the app — they get an
invite email, click it, choose a password, and they're in.

## Features

- **Scrape exhibitor lists** from any tradeshow website
- **Email finder** — hunts emails on company websites, web search, pattern guessing
- **LinkedIn import** — upload Sales Navigator CSV exports
- **Phantombuster integration** — automated LinkedIn enrichment
- **Lead pipeline** — New → Contacted → Qualified → Opportunity → Closed
- **Team assignment** — assign leads to team members
- **Priority + notes** per lead
- **Filter by show, status, assignee**
- **Export to CSV** at any time
- **10-person team** — everyone uses the same Supabase database

---

## File Structure
```
showtoship/
├── .github/workflows/
│   ├── scrape.yml           # Scraper job (dispatched from the UI)
│   └── deploy-pages.yml     # Publishes frontend/ to the gh-pages branch
├── frontend/
│   └── index.html           # The whole web app
├── scraper/
│   ├── run_pipeline.py      # Orchestrator — what Actions runs
│   ├── exhibitor_scraper.py # Phase 1+2: exhibitor list + profile pages
│   ├── list_crawler.py      # Walks every page of a paginated listing
│   ├── extractors.py        # Picks the best selector, pulls the fields
│   ├── llm_extract.py       # Model-based extraction fallback
│   ├── email_finder.py      # Phase 3: email hunting
│   ├── linkedin_enricher.py # Phase 4: LinkedIn via Phantombuster
│   ├── db_writer.py         # Supabase writer with deduplication
│   ├── site_configs.py      # Site-specific scrapers (SMM, MapYourShow, A2Z…)
│   └── tests/               # python3 scraper/tests/test_extractors.py
└── supabase/
    ├── schema.sql           # Tables, indexes, RLS policies
    ├── migrations/          # Run these on an existing database
    └── functions/           # Edge functions (chat, dispatch, admin, …)
```

## Scraping notes

The scraper tries three things, in order:

1. **A site-specific config** (`scraper/site_configs.py`) — best quality, because
   it reads the show's own API. Currently: SMM Hamburg, MapYourShow, A2Z Events,
   Euronaval.
2. **The generic crawler** — walks all pages of the listing (next-links, numbered
   pagers, load-more buttons, infinite scroll), then scores several candidate CSS
   selectors and uses whichever produces the most credible exhibitor rows.
3. **LLM extraction** — only when enabled and the selectors came up short.

If a show returns few or no exhibitors, the fastest fix is usually to add a
config for that domain in `site_configs.py`. Turning on **LLM Extraction** is the
generic escape hatch.

Caps you can tune: `--max-list-pages` (default 100) and `--max-detail-pages`
(default 0 = no limit).

## Running the tests
```bash
python3 scraper/tests/test_extractors.py   # extraction + dedup, no network
python3 scraper/tests/test_pagination.py   # multi-page crawl against localhost
```
