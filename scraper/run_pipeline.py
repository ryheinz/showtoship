"""
run_pipeline.py — Orchestrates all scraping phases + DB write
--------------------------------------------------------------
This is what GitHub Actions calls. It:
  1. Scrapes exhibitor list (Phase 1)
  2. Deep-scrapes profiles (Phase 2, optional)
  3. Finds emails (Phase 3, optional)
  4. Enriches with LinkedIn (Phase 4, optional)
  5. Saves to Supabase
  6. Exports to Excel

Usage:
  python run_pipeline.py \
    --urls /tmp/urls.txt \
    --show-name "Hannover Messe 2025" \
    --out /tmp/results.xlsx \
    [--llm] [--deep] [--emails] [--linkedin]
"""

import asyncio
import argparse
from pathlib import Path
from datetime import datetime

from exhibitor_scraper import ExhibitorScraper, export_to_excel, export_to_csv
from email_finder import EmailFinder, update_excel_with_emails
from db_writer import save_leads, create_job, fail_job
from linkedin_enricher import enrich_all


async def main():
    p = argparse.ArgumentParser()
    p.add_argument("--urls",      required=True)
    p.add_argument("--show-name", required=True, dest="show_name")
    p.add_argument("--out",       default="leads.xlsx")
    p.add_argument("--llm",       action="store_true")
    p.add_argument("--deep",      action="store_true")
    p.add_argument("--emails",    action="store_true")
    p.add_argument("--linkedin",  action="store_true")
    args = p.parse_args()

    urls = [l.strip() for l in Path(args.urls).read_text().splitlines()
            if l.strip() and not l.startswith("#")]

    options = {
        "llm": args.llm, "deep": args.deep,
        "emails": args.emails, "linkedin": args.linkedin
    }

    print(f"\n{'='*60}")
    print(f"  ShowToShip Pipeline")
    print(f"  Show: {args.show_name}")
    print(f"  URLs: {len(urls)}")
    print(f"  Options: {options}")
    print(f"{'='*60}\n")

    # Create job record in DB
    job_id = await create_job(args.show_name, urls, options)

    try:
        # ── Phase 1 + 2: scrape exhibitor data ─────────────────────────────
        print("📋  Phase 1/2 — Scraping exhibitor data…")
        scraper = ExhibitorScraper(use_llm=args.llm, deep=args.deep)
        rows = await scraper.run(urls)
        print(f"   → {len(rows)} exhibitors scraped\n")

        if not rows:
            await fail_job(job_id, "No exhibitors found")
            return

        # ── Phase 3: email finder ───────────────────────────────────────────
        if args.emails:
            print("📧  Phase 3 — Finding email addresses…")
            finder = EmailFinder(concurrency=3, verify_mx=True, use_web_search=True)
            rows = await finder.enrich(rows)
            found = sum(1 for r in rows if r.get("email"))
            print(f"   → {found}/{len(rows)} emails found\n")

        # ── Phase 4: LinkedIn enrichment ────────────────────────────────────
        if args.linkedin:
            print("🔗  Phase 4 — LinkedIn enrichment via Phantombuster…")
            rows = await enrich_all(rows, concurrency=2)
            enriched = sum(1 for r in rows if r.get("linkedin_url"))
            print(f"   → {enriched}/{len(rows)} leads LinkedIn-enriched\n")

        # ── Save to Supabase ────────────────────────────────────────────────
        print("💾  Saving to Supabase…")
        db_result = await save_leads(rows, args.show_name, job_id=job_id)

        # ── Export Excel ────────────────────────────────────────────────────
        print("📊  Exporting Excel…")
        export_to_excel(rows, args.out)
        if args.emails:
            email_out = args.out.replace(".xlsx", "_emails.xlsx")
            update_excel_with_emails(rows, args.out, email_out)

        print(f"\n{'='*60}")
        print(f"  ✅  Pipeline complete")
        print(f"  Exhibitors:  {len(rows)}")
        print(f"  DB new:      {db_result.get('new', 0)}")
        print(f"  DB updated:  {db_result.get('updated', 0)}")
        print(f"  Excel:       {args.out}")
        print(f"{'='*60}\n")

    except Exception as e:
        import traceback
        err = traceback.format_exc()
        print(f"❌  Pipeline failed: {e}\n{err}")
        await fail_job(job_id, str(e))
        raise


if __name__ == "__main__":
    asyncio.run(main())
