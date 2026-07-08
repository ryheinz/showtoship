"""
db_writer.py — Save scraped exhibitor leads to Supabase
---------------------------------------------------------
Handles:
  - Upsert (insert new, update existing by company+show)
  - Deduplication across shows (same company, different shows = separate leads)
  - Job logging (scrape_jobs table)
  - LinkedIn enrichment storage

Requires env vars:
  SUPABASE_URL       https://xxxx.supabase.co
  SUPABASE_KEY       your anon or service_role key
"""

import os
import json
import asyncio
import aiohttp
from datetime import datetime, timezone
from typing import Optional


SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")


def _headers():
    return {
        "apikey":        SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type":  "application/json",
        "Prefer":        "return=representation",
    }


def _url(table: str) -> str:
    return f"{SUPABASE_URL}/rest/v1/{table}"


# ── Generic helpers ───────────────────────────────────────────────────────────

async def _get(session: aiohttp.ClientSession, table: str, params: dict) -> list:
    async with session.get(_url(table), headers=_headers(), params=params) as r:
        if r.status == 200:
            return await r.json()
        text = await r.text()
        print(f"  GET {table} error {r.status}: {text[:200]}")
        return []


async def _post(session: aiohttp.ClientSession, table: str, data: dict) -> Optional[dict]:
    async with session.post(_url(table), headers=_headers(), json=data) as r:
        body = await r.json()
        if r.status in (200, 201):
            return body[0] if isinstance(body, list) else body
        print(f"  POST {table} error {r.status}: {str(body)[:200]}")
        return None


async def _patch(session: aiohttp.ClientSession, table: str, match: dict, data: dict) -> Optional[dict]:
    params = {k: f"eq.{v}" for k, v in match.items()}
    headers = {**_headers(), "Prefer": "return=representation"}
    async with session.patch(_url(table), headers=headers, params=params, json=data) as r:
        body = await r.json()
        if r.status == 200:
            return body[0] if isinstance(body, list) and body else None
        print(f"  PATCH {table} error {r.status}: {str(body)[:200]}")
        return None


# ── Tradeshow helpers ─────────────────────────────────────────────────────────

async def get_or_create_tradeshow(session: aiohttp.ClientSession,
                                   name: str, tradeshow_id: str = None) -> Optional[str]:
    """Return tradeshow UUID, creating it if it doesn't exist."""
    if tradeshow_id:
        return tradeshow_id

    rows = await _get(session, "tradeshows", {"name": f"eq.{name}", "select": "id"})
    if rows:
        return rows[0]["id"]

    # Create new
    result = await _post(session, "tradeshows", {"name": name})
    return result["id"] if result else None


# ── Lead upsert ───────────────────────────────────────────────────────────────

def _clean_lead(row: dict, tradeshow_id: str, tradeshow_name: str) -> dict:
    """Map scraper output dict → leads table columns."""
    def s(v): return str(v).strip() if v else None

    return {
        "company_name":    s(row.get("company_name")),
        "website":         s(row.get("website")),
        "country":         s(row.get("country")),
        "city":            s(row.get("city")),
        "tradeshow_id":    tradeshow_id,
        "tradeshow_name":  tradeshow_name,
        "booth_number":    s(row.get("booth_number")),
        "hall":            s(row.get("hall")),
        "industry":        s(row.get("industry")),
        "category":        s(row.get("category")),
        "products":        s(row.get("products")),
        "description":     s(row.get("description")),
        "contact_name":    s(row.get("contact_person")),
        "email":           s(row.get("email")),
        "email_alts":      s(row.get("email_alts")),
        "email_source":    s(row.get("email_source")),
        "email_confidence":s(row.get("email_confidence")),
        "phone":           s(row.get("phone")),
        "linkedin_url":    s(row.get("social_linkedin")),
        "source_url":      s(row.get("source_url")),
        "scraped_at":      row.get("scraped_at") or datetime.now(timezone.utc).isoformat(),
        "status":          "new",
    }


async def upsert_lead(session: aiohttp.ClientSession,
                       row: dict, tradeshow_id: str, tradeshow_name: str) -> tuple[str, bool]:
    """
    Insert or update a lead.
    Returns (lead_id, is_new).
    Dedup key: company_name + tradeshow_id
    """
    company = str(row.get("company_name", "")).strip()
    if not company:
        return None, False

    # Check existing
    existing = await _get(session, "leads", {
        "company_name": f"eq.{company}",
        "tradeshow_id": f"eq.{tradeshow_id}",
        "select": "id,email,status"
    })

    clean = _clean_lead(row, tradeshow_id, tradeshow_name)

    if existing:
        lead_id = existing[0]["id"]
        # Only update fields that have new/better data
        updates = {}
        for field in ["email", "phone", "contact_name", "linkedin_url",
                       "description", "products", "booth_number", "hall"]:
            if clean.get(field) and not existing[0].get(field):
                updates[field] = clean[field]
        # Always update email enrichment fields
        for field in ["email_alts", "email_source", "email_confidence"]:
            if clean.get(field):
                updates[field] = clean[field]

        if updates:
            await _patch(session, "leads", {"id": lead_id}, updates)
        return lead_id, False
    else:
        result = await _post(session, "leads", clean)
        return (result["id"] if result else None), True


# ── Bulk upsert pipeline ──────────────────────────────────────────────────────

async def save_leads(rows: list[dict],
                      tradeshow_name: str,
                      tradeshow_id: str = None,
                      job_id: str = None) -> dict:
    """
    Main entry point.
    Save all scraped rows to Supabase.
    Returns summary dict.
    """
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("⚠️  SUPABASE_URL / SUPABASE_KEY not set — skipping DB write")
        return {"saved": 0, "new": 0, "updated": 0}

    async with aiohttp.ClientSession() as session:
        # Resolve tradeshow
        ts_id = await get_or_create_tradeshow(session, tradeshow_name, tradeshow_id)
        if not ts_id:
            print("⚠️  Could not resolve tradeshow — skipping DB write")
            return {"saved": 0, "new": 0, "updated": 0}

        new_count = updated_count = error_count = 0
        semaphore = asyncio.Semaphore(5)

        async def save_one(row):
            nonlocal new_count, updated_count, error_count
            async with semaphore:
                try:
                    lead_id, is_new = await upsert_lead(session, row, ts_id, tradeshow_name)
                    if lead_id:
                        if is_new: new_count += 1
                        else:      updated_count += 1
                    else:
                        error_count += 1
                except Exception as e:
                    print(f"  Error saving {row.get('company_name')}: {e}")
                    error_count += 1

        await asyncio.gather(*[save_one(r) for r in rows])

        total = new_count + updated_count
        print(f"\n✅  DB: {new_count} new leads, {updated_count} updated, {error_count} errors")

        # Update job record
        if job_id:
            await _patch(session, "scrape_jobs", {"id": job_id}, {
                "status":         "done",
                "leads_found":    len(rows),
                "leads_new":      new_count,
                "leads_updated":  updated_count,
                "completed_at":   datetime.now(timezone.utc).isoformat(),
            })

        return {"saved": total, "new": new_count, "updated": updated_count}


async def update_job_status(job_id: str, status: str, error: str = None, github_run_id: str = None):
    """Update an existing job's status without creating a new record."""
    async with aiohttp.ClientSession() as session:
        data: dict[str, str] = {"status": status}
        if error:
            data["error"] = error
        if github_run_id:
            data["github_run_id"] = github_run_id
        await _patch(session, "scrape_jobs", {"id": job_id}, data)


# ── Job management ────────────────────────────────────────────────────────────

async def create_job(tradeshow_name: str, urls: list[str],
                      options: dict, github_run_id: str = None) -> Optional[str]:
    async with aiohttp.ClientSession() as session:
        result = await _post(session, "scrape_jobs", {
            "tradeshow_name": tradeshow_name,
            "urls":           urls,
            "options":        options,
            "github_run_id":  github_run_id,
            "status":         "running",
        })
        return result["id"] if result else None


async def fail_job(job_id: str, error: str):
    async with aiohttp.ClientSession() as session:
        await _patch(session, "scrape_jobs", {"id": job_id}, {
            "status":       "failed",
            "error":        error,
            "completed_at": datetime.now(timezone.utc).isoformat(),
        })
