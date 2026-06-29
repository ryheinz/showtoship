"""
exhibitor_scraper.py — Scrape EXHIBITOR lists from tradeshow websites
-----------------------------------------------------------------------
Two-phase approach:
  Phase 1 — scrape the exhibitor LIST page (company names, booths, links)
  Phase 2 — follow each exhibitor's detail link for deeper info (optional)

Usage:
    python exhibitor_scraper.py --url "https://example-show.com/exhibitors"
    python exhibitor_scraper.py --url "https://example-show.com/exhibitors" --deep
    python exhibitor_scraper.py --urls urls.txt --out my_exhibitors.xlsx

Requirements:
    pip install crawl4ai openpyxl playwright
    playwright install chromium
"""

import asyncio
import json
import re
import argparse
from datetime import datetime
from pathlib import Path

from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode
from crawl4ai.extraction_strategy import LLMExtractionStrategy, JsonCssExtractionStrategy
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter


# ══════════════════════════════════════════════════════════════════════════════
#  STEP 1 — INSPECT YOUR TARGET PAGE
#  Before running, open the exhibitor list page in Chrome and press F12.
#  In the Elements tab, right-click on one exhibitor card → "Copy selector".
#  Paste it into EXHIBITOR_SCHEMA["baseSelector"] below.
# ══════════════════════════════════════════════════════════════════════════════

EXHIBITOR_SCHEMA = {
    "name": "ExhibitorList",

    # ── baseSelector: the repeating container for ONE exhibitor entry ──────
    # Common patterns (update this for your specific site):
    #   ".exhibitor-item"      most custom show sites
    #   "tr.exhibitor-row"     table-based lists
    #   ".company-card"        card grid layouts
    #   "li.exhibitor"         simple list layouts
    #   "[data-exhibitor]"     data-attribute based
    "baseSelector": (
        ".exhibitor-item, .company-card, .exhibitor-card, "
        "tr.exhibitor-row, li.exhibitor, [data-exhibitor], "
        ".booth-item, .participant-item, .vendor-item"
    ),

    "fields": [
        # Company name — try common title selectors
        {
            "name": "company_name",
            "selector": "h2, h3, h4, .company-name, .exhibitor-name, .name, .title, strong",
            "type": "text"
        },
        # Booth / stand number
        {
            "name": "booth_number",
            "selector": ".booth, .booth-number, .stand, .hall-booth, [data-booth], .booth-no",
            "type": "text"
        },
        # Hall / pavilion
        {
            "name": "hall",
            "selector": ".hall, .pavilion, .hall-name, [data-hall]",
            "type": "text"
        },
        # Country / nationality
        {
            "name": "country",
            "selector": ".country, .nation, .flag-label, [data-country]",
            "type": "text"
        },
        # Product category / sector
        {
            "name": "category",
            "selector": ".category, .sector, .industry, .product-group, .tags, .tag",
            "type": "text"
        },
        # Short description / profile text
        {
            "name": "description",
            "selector": "p, .description, .profile, .about, .excerpt, .summary",
            "type": "text"
        },
        # Website (from href attribute)
        {
            "name": "website",
            "selector": "a.website, a.company-link, a[href*='http']:not([href*='exhibitor'])",
            "type": "attribute",
            "attribute": "href"
        },
        # Exhibitor detail page link (for Phase 2 deep scrape)
        {
            "name": "detail_url",
            "selector": "a.exhibitor-link, a.more, a[href*='exhibitor'], a[href*='company'], a[href*='booth']",
            "type": "attribute",
            "attribute": "href"
        },
    ]
}


# ══════════════════════════════════════════════════════════════════════════════
#  LLM PROMPT — used when CSS selectors don't match your page structure
#  (pass --llm flag to activate)
# ══════════════════════════════════════════════════════════════════════════════

EXHIBITOR_LLM_PROMPT = """
You are extracting exhibitor/company data from a trade show or exhibition website.

Extract EVERY company or exhibitor listed on this page.
Return a JSON array where each object has these fields (use null if not found):

- company_name: full official company name
- booth_number: booth or stand number/code (e.g. "A12", "Hall 3 Stand 45")
- hall: hall or pavilion name/number
- country: country of origin
- category: product category, industry sector, or tags
- description: company profile or product description (max 3 sentences)
- website: company website URL
- detail_url: URL to the exhibitor's profile page on this site
- email: contact email if shown
- phone: contact phone if shown
- products: main products or services listed

Return ONLY a valid JSON array. No markdown, no prose.
"""


# ══════════════════════════════════════════════════════════════════════════════
#  DETAIL PAGE PROMPT — Phase 2 deep scrape of individual exhibitor profiles
# ══════════════════════════════════════════════════════════════════════════════

DETAIL_PAGE_PROMPT = """
Extract detailed exhibitor/company information from this profile page.
Return a single JSON object with:

- company_name
- booth_number
- hall
- country
- city
- address
- website
- email
- phone
- contact_person
- category / products
- description (full profile text, max 5 sentences)
- social_linkedin
- social_twitter
- year_founded
- employee_count
- brands (list any brand names mentioned)

Return ONLY a valid JSON object. No prose.
"""


# ══════════════════════════════════════════════════════════════════════════════
#  SCRAPER
# ══════════════════════════════════════════════════════════════════════════════

class ExhibitorScraper:

    def __init__(self, use_llm: bool = False, deep: bool = False,
                 llm_provider: str = "openai/gpt-4o-mini", max_detail_pages: int = 50):
        self.use_llm = use_llm
        self.deep = deep
        self.llm_provider = llm_provider
        self.max_detail_pages = max_detail_pages
        self.results: list[dict] = []

    # ── browser config (shared) ──────────────────────────────────────────────

    def _browser_cfg(self) -> BrowserConfig:
        return BrowserConfig(
            headless=True,
            verbose=False,
            extra_args=["--disable-gpu", "--no-sandbox", "--disable-dev-shm-usage"],
        )

    # ── extraction strategy ──────────────────────────────────────────────────

    def _list_strategy(self):
        if self.use_llm:
            return LLMExtractionStrategy(
                provider=self.llm_provider,
                instruction=EXHIBITOR_LLM_PROMPT,
                extraction_type="block",
            )
        return JsonCssExtractionStrategy(EXHIBITOR_SCHEMA, verbose=True)

    def _detail_strategy(self):
        return LLMExtractionStrategy(
            provider=self.llm_provider,
            instruction=DETAIL_PAGE_PROMPT,
            extraction_type="block",
        )

    # ── Phase 1: scrape exhibitor list ───────────────────────────────────────

    async def scrape_list_page(self, url: str) -> list[dict]:
        """
        STEP 3 in the workflow.
        Scrapes the exhibitor listing/directory page.
        Handles pagination automatically via scroll + next-page detection.
        """
        run_cfg = CrawlerRunConfig(
            cache_mode=CacheMode.BYPASS,
            extraction_strategy=self._list_strategy(),
            wait_for="body",
            page_timeout=45000,
            remove_overlay_elements=True,
            excluded_tags=["nav", "footer", "script", "style"],
            # Scroll to load lazy-loaded exhibitors
            js_code="""
                window.scrollTo(0, document.body.scrollHeight);
                await new Promise(r => setTimeout(r, 2000));
                window.scrollTo(0, document.body.scrollHeight);
                await new Promise(r => setTimeout(r, 1000));
            """,
            wait_for_images=False,
        )

        rows = []
        async with AsyncWebCrawler(config=self._browser_cfg()) as crawler:
            result = await crawler.arun(url=url, config=run_cfg)

        if not result.success:
            print(f"  ✗ Failed to load: {url}\n    Error: {result.error_message}")
            return []

        if result.extracted_content:
            try:
                data = json.loads(result.extracted_content)
                rows = data if isinstance(data, list) else [data]
            except json.JSONDecodeError:
                rows = self._markdown_fallback(result.markdown)

        # Normalise and tag
        base = url.rstrip("/").rsplit("/", 1)[0]
        for r in rows:
            r.setdefault("source_url", url)
            r.setdefault("scraped_at", datetime.now().strftime("%Y-%m-%d %H:%M"))
            # Make relative detail_url absolute
            du = r.get("detail_url", "")
            if du and not du.startswith("http"):
                r["detail_url"] = base + "/" + du.lstrip("/")

        print(f"  ✓ List page: {len(rows)} exhibitors  ←  {url}")
        return rows

    # ── Phase 2 (optional): deep-scrape individual company profiles ──────────

    async def scrape_detail_pages(self, rows: list[dict]) -> list[dict]:
        """
        STEP 4 (optional, activated by --deep flag).
        For each exhibitor that has a detail_url, fetches the profile page
        and merges richer data back into the row.
        """
        to_fetch = [r for r in rows if r.get("detail_url")][:self.max_detail_pages]
        if not to_fetch:
            print("  ℹ No detail URLs found — skipping deep scrape.")
            return rows

        print(f"\n  📄  Deep-scraping {len(to_fetch)} exhibitor profile pages…")
        semaphore = asyncio.Semaphore(2)  # polite concurrency

        async with AsyncWebCrawler(config=self._browser_cfg()) as crawler:
            async def fetch_one(row: dict) -> dict:
                async with semaphore:
                    url = row["detail_url"]
                    run_cfg = CrawlerRunConfig(
                        cache_mode=CacheMode.BYPASS,
                        extraction_strategy=self._detail_strategy(),
                        wait_for="body",
                        page_timeout=30000,
                        remove_overlay_elements=True,
                        excluded_tags=["nav", "footer", "script", "style"],
                    )
                    result = await crawler.arun(url=url, config=run_cfg)

                    if result.success and result.extracted_content:
                        try:
                            detail = json.loads(result.extracted_content)
                            if isinstance(detail, list) and detail:
                                detail = detail[0]
                            if isinstance(detail, dict):
                                for k, v in detail.items():
                                    if v and not row.get(k):
                                        row[k] = v
                        except json.JSONDecodeError:
                            pass
                    return row

            tasks = [fetch_one(r) for r in to_fetch]
            enriched = await asyncio.gather(*tasks, return_exceptions=True)

        # Merge enriched rows back
        enriched_map = {r["detail_url"]: r for r in enriched if isinstance(r, dict)}
        return [enriched_map.get(r.get("detail_url"), r) for r in rows]

    # ── Main pipeline ────────────────────────────────────────────────────────

    async def run(self, urls: list[str]) -> list[dict]:
        all_rows = []
        for url in urls:
            rows = await self.scrape_list_page(url)
            if self.deep and rows:
                rows = await self.scrape_detail_pages(rows)
            all_rows.extend(rows)
        self.results = all_rows
        return all_rows

    def _markdown_fallback(self, markdown: str) -> list[dict]:
        if not markdown:
            return []
        rows = []
        for m in re.finditer(r"(?m)^\*\*(.+?)\*\*|^#{1,3}\s+(.+)$", markdown):
            name = m.group(1) or m.group(2)
            if name:
                rows.append({"company_name": name.strip()})
        return rows[:200]


# ══════════════════════════════════════════════════════════════════════════════
#  EXCEL EXPORT
# ══════════════════════════════════════════════════════════════════════════════

COLUMNS = [
    ("Company Name",      28),
    ("Booth / Stand",     14),
    ("Hall / Pavilion",   16),
    ("Country",           16),
    ("City",              16),
    ("Category / Sector", 24),
    ("Products / Services",28),
    ("Description",       45),
    ("Website",           32),
    ("Email",             26),
    ("Phone",             18),
    ("Contact Person",    22),
    ("LinkedIn",          30),
    ("Detail Profile URL",36),
    ("Source URL",        36),
    ("Scraped At",        18),
]

FIELD_MAP = [
    "company_name", "booth_number", "hall", "country", "city",
    "category", "products", "description",
    "website", "email", "phone", "contact_person",
    "social_linkedin", "detail_url", "source_url", "scraped_at",
]

HDR_FILL   = PatternFill("solid", start_color="1A3C5E")
HDR_FONT   = Font(bold=True, color="FFFFFF", size=11, name="Calibri")
ALT_FILL   = PatternFill("solid", start_color="E8F1F8")
NORM_FILL  = PatternFill("solid", start_color="FFFFFF")
LINK_FONT  = Font(color="0563C1", underline="single", name="Calibri", size=10)
BODY_FONT  = Font(name="Calibri", size=10)
CENTER     = Alignment(horizontal="center", vertical="center", wrap_text=True)
LEFT       = Alignment(horizontal="left",   vertical="center", wrap_text=True)
THIN       = Side(style="thin", color="C0D9E8")
BORDER     = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
URL_FIELDS = {"website", "social_linkedin", "detail_url", "source_url"}


def export_to_excel(rows: list[dict], path: str = "exhibitors.xlsx"):
    wb = Workbook()
    ws = wb.active
    ws.title = "Exhibitors"
    ws.freeze_panes = "A2"

    # Header
    for ci, (header, width) in enumerate(COLUMNS, 1):
        c = ws.cell(row=1, column=ci, value=header)
        c.font, c.fill, c.alignment, c.border = HDR_FONT, HDR_FILL, CENTER, BORDER
        ws.column_dimensions[get_column_letter(ci)].width = width
    ws.row_dimensions[1].height = 24

    # Data
    for ri, item in enumerate(rows, 2):
        fill = ALT_FILL if ri % 2 == 0 else NORM_FILL
        for ci, field in enumerate(FIELD_MAP, 1):
            val = item.get(field) or ""
            c = ws.cell(row=ri, column=ci, value=val)
            c.border, c.fill = BORDER, fill
            if field in URL_FIELDS and val and str(val).startswith("http"):
                c.hyperlink = val
                c.font, c.alignment = LINK_FONT, LEFT
            else:
                c.font, c.alignment = BODY_FONT, LEFT
        ws.row_dimensions[ri].height = 18

    ws.auto_filter.ref = f"A1:{get_column_letter(len(COLUMNS))}1"

    # Summary sheet
    ws2 = wb.create_sheet("Summary")
    n = len(rows) + 1
    summary_data = [
        ("Total Exhibitors",    f"=COUNTA(Exhibitors!A2:A{n})"),
        ("Unique Countries",    f"=IFERROR(SUMPRODUCT(1/COUNTIF(Exhibitors!D2:D{n},Exhibitors!D2:D{n})),0)"),
        ("Unique Categories",   f"=IFERROR(SUMPRODUCT(1/COUNTIF(Exhibitors!F2:F{n},Exhibitors!F2:F{n})),0)"),
        ("With Website",        f"=COUNTA(Exhibitors!I2:I{n})"),
        ("With Email",          f"=COUNTA(Exhibitors!J2:J{n})"),
        ("Generated",           datetime.now().strftime("%Y-%m-%d %H:%M")),
    ]
    ws2["A1"] = "Exhibitor Data Summary"
    ws2["A1"].font = Font(bold=True, size=14, name="Calibri", color="1A3C5E")
    for i, (label, val) in enumerate(summary_data, 3):
        ws2.cell(i, 1, label).font = Font(bold=True, name="Calibri", size=10)
        ws2.cell(i, 2, val).font   = Font(name="Calibri", size=10)
    ws2.column_dimensions["A"].width = 22
    ws2.column_dimensions["B"].width = 18

    wb.save(path)
    print(f"\n✅  Saved → {path}  ({len(rows)} exhibitors)")


def export_to_csv(rows: list[dict], path: str):
    import csv
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELD_MAP, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    print(f"✅  CSV  → {path}")


# ══════════════════════════════════════════════════════════════════════════════
#  CLI
# ══════════════════════════════════════════════════════════════════════════════

async def main():
    p = argparse.ArgumentParser(description="Scrape exhibitor lists → Excel")
    p.add_argument("--url",    help="Single exhibitor-list URL")
    p.add_argument("--urls",   help="Text file with one URL per line")
    p.add_argument("--llm",    action="store_true", help="Use LLM extraction (needs OPENAI_API_KEY)")
    p.add_argument("--deep",   action="store_true", help="Also scrape individual exhibitor profile pages")
    p.add_argument("--emails", action="store_true", help="Phase 3: hunt for email addresses on the web")
    p.add_argument("--out",    default="exhibitors.xlsx")
    p.add_argument("--csv",    action="store_true")
    args = p.parse_args()

    if args.url:
        urls = [args.url]
    elif args.urls:
        urls = [l.strip() for l in Path(args.urls).read_text().splitlines()
                if l.strip() and not l.startswith("#")]
    else:
        print("Provide --url or --urls. Example:\n  python exhibitor_scraper.py --url https://myshow.com/exhibitors")
        return

    print(f"\n🔍  Scraping {len(urls)} URL(s) | LLM={'on' if args.llm else 'off'} | Deep={'on' if args.deep else 'off'} | Emails={'on' if args.emails else 'off'}\n")
    scraper = ExhibitorScraper(use_llm=args.llm, deep=args.deep)
    rows = await scraper.run(urls)

    if not rows:
        print("⚠️  No exhibitors found. See STEP 2 in the guide to inspect selectors.")
        return

    # ── Phase 3: email enrichment ─────────────────────────────────────────────
    if args.emails:
        try:
            from email_finder import EmailFinder, update_excel_with_emails
            print(f"\n📧  Phase 3 — hunting emails for {len(rows)} companies…\n")
            finder = EmailFinder(concurrency=3, verify_mx=True, use_web_search=True)
            rows = await finder.enrich(rows)
            # Save Excel with email columns added
            export_to_excel(rows, args.out)
            # Re-open and inject colour-coded confidence columns
            out_emails = args.out.replace(".xlsx", "_emails.xlsx")
            update_excel_with_emails(rows, args.out, out_emails)
            print(f"\n✅  Final file with emails → {out_emails}")
        except ImportError:
            print("⚠️  email_finder.py not found next to this script. Skipping email phase.")
            export_to_excel(rows, args.out)
    else:
        export_to_excel(rows, args.out)

    if args.csv:
        export_to_csv(rows, args.out.replace(".xlsx", ".csv"))


if __name__ == "__main__":
    asyncio.run(main())
