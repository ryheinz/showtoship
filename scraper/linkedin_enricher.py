"""
linkedin_enricher.py — Enrich leads with LinkedIn data via Phantombuster
-------------------------------------------------------------------------
Phantombuster has a free tier (2hr/day execution time) and works with
LinkedIn Sales Navigator. No official LinkedIn API approval needed.

Two approaches supported:
  A) Phantombuster API  — automated, works in GitHub Actions
  B) Sales Navigator CSV export  — manual, paste into the app

Phantombuster Phantoms used:
  - "LinkedIn Search Export"  → search by company name, get profile URLs
  - "LinkedIn Profile Scraper" → get full profile data from URLs

Setup:
  1. Sign up at phantombuster.com (free tier works)
  2. Connect your LinkedIn account in Phantombuster
  3. Get your Phantombuster API key
  4. Set PHANTOMBUSTER_API_KEY env var / GitHub secret

Env vars required:
  PHANTOMBUSTER_API_KEY    your Phantombuster API key
  SUPABASE_URL             for writing results back
  SUPABASE_KEY
"""

import os
import asyncio
import json
import aiohttp
from datetime import datetime, timezone
from typing import Optional


PHANTOMBUSTER_API = "https://api.phantombuster.com/api/v2"
PB_KEY = os.environ.get("PHANTOMBUSTER_API_KEY", "")

# Phantombuster Phantom IDs (these are the standard public phantoms)
# You need to have these set up in your Phantombuster account
PHANTOM_SEARCH_ID  = os.environ.get("PB_SEARCH_PHANTOM_ID", "")   # LinkedIn Search Export
PHANTOM_PROFILE_ID = os.environ.get("PB_PROFILE_PHANTOM_ID", "")  # LinkedIn Profile Scraper


def _pb_headers():
    return {
        "X-Phantombuster-Key": PB_KEY,
        "Content-Type": "application/json",
    }


# ── Phantombuster API helpers ─────────────────────────────────────────────────

async def launch_phantom(session: aiohttp.ClientSession,
                          phantom_id: str, args: dict) -> Optional[str]:
    """Launch a Phantombuster phantom and return the container ID."""
    async with session.post(
        f"{PHANTOMBUSTER_API}/agents/launch",
        headers=_pb_headers(),
        json={"id": phantom_id, "arguments": json.dumps(args), "saveArguments": False}
    ) as r:
        if r.status == 200:
            data = await r.json()
            return data.get("containerId")
        text = await r.text()
        print(f"  Phantombuster launch error: {r.status} {text[:200]}")
        return None


async def wait_for_phantom(session: aiohttp.ClientSession,
                            container_id: str, timeout_s: int = 300) -> Optional[list]:
    """Poll until phantom completes, return output rows."""
    start = asyncio.get_event_loop().time()
    max_attempts = max(2, timeout_s // 15 + 1)
    for _ in range(max_attempts):
        await asyncio.sleep(15)
        if asyncio.get_event_loop().time() - start > timeout_s:
            print("  Phantom timed out")
            return None
        async with session.get(
            f"{PHANTOMBUSTER_API}/containers/fetch-output",
            headers=_pb_headers(),
            params={"id": container_id}
        ) as r:
            if r.status != 200:
                continue
            data = await r.json()
            status = data.get("status")
            if status == "finished":
                result_url = data.get("resultObject")
                if result_url:
                    return await fetch_phantom_results(session, result_url)
                return []
            if status == "error":
                print(f"  Phantom error: {data.get('output','')[-200:]}")
                return None

    print("  Phantom timed out (max attempts)")
    return None


async def fetch_phantom_results(session: aiohttp.ClientSession, url: str) -> list:
    async with session.get(url) as r:
        if r.status == 200:
            text = await r.text()
            try:
                return json.loads(text)
            except Exception:
                # Try CSV
                import csv, io
                reader = csv.DictReader(io.StringIO(text))
                return list(reader)
    return []


# ── LinkedIn Search → Profile pipeline ───────────────────────────────────────

async def search_company_on_linkedin(session: aiohttp.ClientSession,
                                      company_name: str) -> Optional[str]:
    """
    Use Phantombuster LinkedIn Search Export to find the company page URL.
    Returns the LinkedIn company URL if found.
    """
    if not PHANTOM_SEARCH_ID:
        return None

    args = {
        "search":    company_name,
        "type":      "companies",
        "numberOfResultsPerLaunch": 3,
    }
    container_id = await launch_phantom(session, PHANTOM_SEARCH_ID, args)
    if not container_id:
        return None

    results = await wait_for_phantom(session, container_id, timeout_s=120)
    if results:
        # Return the first matching company URL
        for r in results:
            url = r.get("linkedinUrl") or r.get("url") or r.get("profileUrl")
            if url and "linkedin.com/company" in url:
                return url
    return None


async def scrape_linkedin_profile(session: aiohttp.ClientSession,
                                   profile_url: str) -> Optional[dict]:
    """
    Use Phantombuster LinkedIn Profile Scraper to get full company data.
    """
    if not PHANTOM_PROFILE_ID:
        return None

    args = {
        "spreadsheetUrl": profile_url,  # can be single URL or Google Sheet URL
        "numberOfResultsPerLaunch": 1,
    }
    container_id = await launch_phantom(session, PHANTOM_PROFILE_ID, args)
    if not container_id:
        return None

    results = await wait_for_phantom(session, container_id, timeout_s=180)
    if results:
        return results[0] if results else None
    return None


# ── Main enrichment function ──────────────────────────────────────────────────

async def enrich_lead_with_linkedin(lead: dict) -> dict:
    """
    Given a lead dict with company_name, try to find and attach LinkedIn data.
    Returns enriched lead dict.
    """
    if not PB_KEY:
        print("  ⚠️  PHANTOMBUSTER_API_KEY not set — skipping LinkedIn enrichment")
        return lead

    company = lead.get("company_name", "")
    if not company:
        return lead

    async with aiohttp.ClientSession() as session:
        # Step 1: find LinkedIn URL
        li_url = lead.get("linkedin_url") or await search_company_on_linkedin(session, company)

        if not li_url:
            print(f"  – {company[:40]}: no LinkedIn found")
            return lead

        lead["linkedin_url"] = li_url

        # Step 2: scrape profile
        profile = await scrape_linkedin_profile(session, li_url)
        if profile:
            lead["linkedin_enriched"]   = profile
            lead["linkedin_checked_at"] = datetime.now(timezone.utc).isoformat()

            # Map common Phantombuster fields back to lead fields
            if not lead.get("description") and profile.get("description"):
                lead["description"] = profile["description"]
            if not lead.get("industry") and profile.get("industry"):
                lead["industry"] = profile["industry"]
            if not lead.get("city") and profile.get("city"):
                lead["city"] = profile["city"]
            if not lead.get("contact_name") and profile.get("generalStaff"):
                lead["contact_name"] = profile["generalStaff"]

            print(f"  ✓ {company[:40]}: LinkedIn enriched")
        else:
            print(f"  – {company[:40]}: LinkedIn profile scrape failed")

    return lead


async def enrich_all(leads: list[dict], concurrency: int = 2) -> list[dict]:
    """Enrich a list of leads with LinkedIn data."""
    semaphore = asyncio.Semaphore(concurrency)

    async def bounded(lead):
        async with semaphore:
            await asyncio.sleep(2)
            try:
                return await enrich_lead_with_linkedin(lead)
            except Exception as e:
                print(f"  LinkedIn enrichment error for {lead.get('company_name','')}: {e}")
                return lead

    return await asyncio.gather(*[bounded(l) for l in leads])


# ── Manual CSV import (Sales Navigator export) ────────────────────────────────

def parse_sales_navigator_csv(csv_path: str) -> list[dict]:
    """
    Parse a LinkedIn Sales Navigator export CSV.
    Maps Sales Navigator column names to our lead schema.

    Sales Navigator export columns vary but typically include:
    First Name, Last Name, Title, Company, LinkedIn Member URL,
    Email Address, Company Website, ...
    """
    import csv
    leads = []
    with open(csv_path, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            # Normalise column names
            r = {k.lower().strip().replace(" ", "_"): v for k, v in row.items()}

            company = r.get("company") or r.get("company_name") or ""
            if not company:
                continue

            leads.append({
                "company_name":   company,
                "contact_name":   f"{r.get('first_name','')} {r.get('last_name','')}".strip(),
                "email":          r.get("email_address") or r.get("email") or "",
                "phone":          r.get("phone") or "",
                "linkedin_url":   r.get("linkedin_member_url") or r.get("linkedin_url") or "",
                "website":        r.get("company_website") or r.get("website") or "",
                "industry":       r.get("industry") or "",
                "city":           r.get("city") or r.get("location") or "",
                "country":        r.get("country") or "",
                "products":       r.get("job_title") or r.get("title") or "",  # repurpose for context
                "source_url":     "linkedin_sales_navigator",
                "scraped_at":     datetime.now(timezone.utc).isoformat(),
            })
    print(f"  Parsed {len(leads)} leads from Sales Navigator CSV")
    return leads
