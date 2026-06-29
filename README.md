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

## Deployment (15 minutes total)

### 1 — Create GitHub Repository
Create a new repo (private recommended). Upload all files.

### 2 — Set up Supabase database
1. Go to [supabase.com](https://supabase.com) → New project (free)
2. Settings → API → copy **Project URL** and **anon/public key**
3. SQL Editor → New Query → paste contents of `supabase/schema.sql` → Run

### 3 — Add GitHub Secrets
Settings → Secrets and variables → Actions → New repository secret:

| Secret | Value |
|---|---|
| `SUPABASE_URL` | Your Supabase Project URL |
| `SUPABASE_KEY` | Your Supabase anon key |
| `OPENAI_API_KEY` | (optional) For LLM extraction |
| `PHANTOMBUSTER_API_KEY` | (optional) For LinkedIn enrichment |
| `PB_SEARCH_PHANTOM_ID` | (optional) Phantombuster phantom ID |
| `PB_PROFILE_PHANTOM_ID` | (optional) Phantombuster phantom ID |

### 4 — Enable GitHub Pages
Settings → Pages → Source: **GitHub Actions** → Save

### 5 — Enable workflow write permissions
Settings → Actions → General → **Read and write permissions** ✓

### 6 — Get a Personal Access Token
[github.com/settings/tokens](https://github.com/settings/tokens) → New token → scopes: `repo` + `workflow`

### 7 — Open your app
`https://YOUR-USERNAME.github.io/showtoship/`

1. Settings page → enter your GitHub PAT + repo name + Supabase URL + key → Save
2. Add your tradeshows (Shows page)
3. Click **New Scrape** → enter exhibitor list URL → Run

---

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
