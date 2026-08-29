# ShowToShip 🔍

**Tradeshow Lead Intelligence Platform** — scrape exhibitor lists, find emails, enrich with LinkedIn, manage your sales pipeline. Entirely free using GitHub.

---

## Architecture

```
GitHub Pages (frontend) ←→ Supabase (database) ←→ GitHub Actions (scraper)
                                                         ↕
                                              crawl4ai + email_finder + Phantombuster
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
│   ├── scrape.yml           # Scraper job (triggered from UI)
│   └── deploy-pages.yml     # Auto-deploys frontend
├── frontend/
│   └── index.html           # Full web app (GitHub Pages)
├── scraper/
│   ├── run_pipeline.py      # Main orchestrator
│   ├── exhibitor_scraper.py # Phase 1+2: exhibitor data
│   ├── email_finder.py      # Phase 3: email hunting
│   ├── linkedin_enricher.py # Phase 4: LinkedIn via Phantombuster
│   ├── db_writer.py         # Supabase writer with deduplication
│   └── site_configs.py      # Site-specific CSS selectors
└── supabase/
    └── schema.sql           # Database tables + indexes
```
