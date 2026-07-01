"""
site_configs.py — Per-site scraping configs (CSS selectors or API-based)
"""

import asyncio
from datetime import datetime


CONFIGS: dict[str, dict] = {
    "smm-hamburg.com": {
        "name": "SMM Hamburg",
        "type": "api",
        "api_config": {
            "endpoint": "https://live.messebackend.aws.corussoft.de/webservice/search",
            "method": "POST",
            "content_type": "application/x-www-form-urlencoded",
            "base_params": {
                "os": "web",
                "appUrl": "https://www.smm-hamburg.com",
                "clientVersion": "1.15.0",
                "topic": "2026_smm",
                "apiVersion": "52",
                "browserLang": "en-US",
                "filterlist": "entity_orga,,cur_curated",
                "order": "lexic",
                "timezoneOffset": "0",
                "lang": "en",
            },
            "page_size": 200,
            "total_count": 0,
            "response_type": "xml",
            "entity_path": ".//entities/organization",
            "field_map": {
                "company_name": {"attr": "name"},
                "email": {"attr": "email"},
                "website": {"attr": "web"},
                "country": {"attr": "country"},
                "country_code": {"attr": "countryCode"},
                "city": {"attr": "city"},
                "hall": {"path": "stands/stand", "attr": "hallNr"},
                "booth_number": {"path": "stands/stand", "attr": "standNr"},
                "description": {"path": "description/teaser", "text": True},
            },
            "init_page": "https://www.smm-hamburg.com/exhibit-visit/exhibitor-directory",
        },
    },
    "euronaval.com": {
        "name": "Euronaval",
        "type": "playwright",
        "playwright_scraper": "scrape_euronaval",
    },
    "mapyourshow.com": {
        "name": "MapYourShow",
        "type": "playwright",
        "playwright_scraper": "scrape_mapyourshow",
    },
    "a2zinc.net": {
        "name": "A2Z Events",
        "type": "playwright",
        "playwright_scraper": "scrape_a2z",
    },
}


def get_config_for_domain(url: str) -> dict | None:
    for domain, config in CONFIGS.items():
        if domain in url:
            return config
    lower = url.lower()
    if "/public/exhibitors.aspx" in lower or "/public/eventmap.aspx" in lower:
        return CONFIGS.get("a2zinc.net")
    return None


def get_playwright_scraper(name: str):
    scrapers = {
        "scrape_euronaval": _scrape_euronaval,
        "scrape_mapyourshow": _scrape_mapyourshow,
        "scrape_a2z": _scrape_a2z,
    }
    return scrapers.get(name)


async def _scrape_euronaval(url: str) -> list[dict]:
    """Direct Playwright extraction for Euronaval exhibitor table."""
    from playwright.async_api import async_playwright

    rows = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--disable-gpu", "--no-sandbox", "--disable-dev-shm-usage"],
        )
        page = await browser.new_page()
        await page.goto(url, wait_until="networkidle", timeout=30000)
        await page.wait_for_timeout(3000)

        entries = await page.evaluate("""
            () => {
                const results = [];
                const table = document.querySelector('table');
                if (!table) return results;
                const rows = table.querySelectorAll('tr');
                for (const row of rows) {
                    const cells = row.querySelectorAll('td');
                    if (cells.length >= 3) {
                        const name = cells[0].textContent.trim();
                        const country = cells[1].textContent.trim();
                        const link = cells[2].querySelector('a');
                        const website = link ? link.href : cells[2].textContent.trim();
                        if (name) {
                            results.push({
                                company_name: name,
                                country: country,
                                website: website
                            });
                        }
                    }
                }
                return results;
            }
        """)
        await browser.close()

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    for r in entries:
        r["source_url"] = url
        r["scraped_at"] = now
    rows = entries

    print(f"  ✓ Euronaval: {len(rows)} exhibitors extracted via direct Playwright")
    return rows


async def _scrape_mapyourshow(url: str) -> list[dict]:
    """
    Playwright scraper for MapYourShow.com Vue-based exhibitor alphalist pages.
    Works with any show hosted on *.mapyourshow.com.

    Clicks each letter button (Show All, A-Z, 0-9, #) and captures ALL
    API responses to get the full exhibitor roster from the internal API:
      action=search&search=<letter>&searchtype=exhibitoralpha&show=all
    """
    from playwright.async_api import async_playwright

    base = url.rstrip("/")
    if ".cfm" in base:
        base = base.split(".cfm")[0] + ".cfm"
    if "?" in base:
        base = base.split("?")[0]

    api_base = base.rsplit("/", 2)[0] if "/explore/" in base else base
    all_hits = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--disable-gpu", "--no-sandbox", "--disable-blink-features=AutomationControlled"],
        )
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = await context.new_page()

        async def capture_api(resp):
            nonlocal all_hits
            if "action=search" in resp.url and resp.status == 200:
                try:
                    data = await resp.json()
                    if data.get("SUCCESS"):
                        exhibitor = data.get("DATA", {}).get("results", {}).get("exhibitor", {})
                        hits = exhibitor.get("hit", [])
                        if hits:
                            before = len(all_hits)
                            all_hits.extend(hits)
                            print(f"  → {len(hits)} exhibitors (total: {len(all_hits)})")
                except Exception:
                    pass

        page.on("response", capture_api)

        print(f"  → Loading {base}")
        await page.goto(base, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(3000)

        letters = ["*", "0", "#", "A", "B", "C", "D", "E", "F", "G", "H",
                   "I", "J", "K", "L", "M", "N", "O", "P", "Q", "R",
                   "S", "T", "U", "V", "W", "X", "Y", "Z"]

        for letter in letters:
            link = await page.query_selector(f'a[href*="alpha/{letter}"], a[href*="alpha/%{letter}"]')
            if not link:
                print(f"  ✗ Letter '{letter}' not found")
                continue

            await link.click()
            await page.wait_for_timeout(2000)

            if letter == "*":
                label = "Show All"
            else:
                label = letter
            print(f"  → Clicked '{label}'")

        if not all_hits:
            print("  → API capture yielded no results, extracting from DOM...")
            entries = await page.evaluate("""
                () => {
                    const seen = new Set();
                    const links = document.querySelectorAll('a[href*="exhibitor-details"]');
                    const results = [];
                    links.forEach(a => {
                        const name = a.textContent.trim();
                        if (name && name.length > 2 && !seen.has(name)) {
                            seen.add(name);
                            results.push({ company_name: name, detail_url: a.href });
                        }
                    });
                    return results;
                }
            """)
            for r in entries:
                r["source_url"] = base
                r["scraped_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
            await browser.close()
            print(f"  ✓ MapYourShow: {len(entries)} exhibitors extracted via DOM")
            return entries

        await browser.close()

    deduped = []
    seen = set()
    for h in all_hits:
        fields = h.get("fields", {})
        name = fields.get("exhname_t", "").strip()
        if not name:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        exhid = fields.get("exhid_l", "")
        booth_list = fields.get("booths_la", [])
        hall_list = fields.get("hallid_la", [])
        booth = booth_list[0].replace("randomstring", "") if booth_list else ""
        hall = hall_list[0] if hall_list else ""
        desc = fields.get("exhdesc_t", "").strip()
        detail_url = f"{api_base}/exhibitor/exhibitor-details.cfm?exhid={exhid}" if exhid else ""
        deduped.append({
            "company_name": name,
            "exhibitor_id": exhid,
            "booth_number": booth,
            "hall": hall,
            "description": desc,
            "detail_url": detail_url,
            "source_url": base,
            "scraped_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        })

    print(f"  ✓ MapYourShow: {len(deduped)} exhibitors extracted")
    return deduped


async def _scrape_a2z(url: str) -> list[dict]:
    """
    Playwright scraper for A2Z Events (a2zinc.net) exhibitor pages.
    Works with:
      - /Public/Exhibitors.aspx (table-based list)
      - /Public/EventMap.aspx (floorplan view)

    Handles ASP.NET pagination (postback).
    """
    from playwright.async_api import async_playwright

    results = []
    page_num = 0

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--disable-gpu", "--no-sandbox", "--disable-blink-features=AutomationControlled"],
        )
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = await context.new_page()

        print(f"  → Loading {url}")
        await page.goto(url, wait_until="networkidle", timeout=60000)
        await page.wait_for_timeout(2000)

        while True:
            page_num += 1
            entries = await page.evaluate("""
                () => {
                    const table = document.querySelector('table.table-striped.table-hover');
                    if (!table) return [];
                    const results = [];
                    const rows = table.querySelectorAll('tr');
                    for (let i = 0; i < rows.length; i++) {
                        const cells = rows[i].querySelectorAll('td');
                        if (cells.length < 3) continue;
                        const nameCell = cells[1];
                        const boothCell = cells[2];
                        const nameLink = nameCell?.querySelector('a.exhibitorName');
                        if (!nameLink) continue;
                        const name = nameLink.textContent.trim();
                        if (!name) continue;
                        const boothLink = boothCell?.querySelector('a.boothLabel');
                        results.push({
                            company_name: name,
                            booth_number: boothLink?.textContent?.trim() || '',
                            detail_url: nameLink.href || '',
                            map_url: boothLink?.href || '',
                        });
                    }
                    return results;
                }
            """)
            print(f"  → Page {page_num}: {len(entries)} exhibitors")
            results.extend(entries)

            has_next = await page.evaluate("""
                () => {
                    const pager = document.querySelector('.pagination, [class*="pager"], table.DG');
                    if (!pager) return false;
                    const links = pager.querySelectorAll('a');
                    for (const a of links) {
                        const text = a.textContent.trim().toLowerCase();
                        if (text === 'next' || text === '>' || text === '\\u00bb') {
                            if (!a.parentElement?.classList.contains('disabled') && !a.classList.contains('aspNetDisabled')) return true;
                        }
                    }
                    return false;
                }
            """)

            if not has_next:
                break

            clicked = await page.evaluate("""
                () => {
                    const pager = document.querySelector('.pagination, [class*="pager"], table.DG');
                    if (!pager) return false;
                    const links = pager.querySelectorAll('a');
                    for (const a of links) {
                        const text = a.textContent.trim().toLowerCase();
                        if (text === 'next' || text === '>' || text === '\\u00bb') {
                            if (!a.parentElement?.classList.contains('disabled') && !a.classList.contains('aspNetDisabled')) {
                                a.click();
                                return true;
                            }
                        }
                    }
                    return false;
                }
            """)

            if not clicked:
                print(f"  ✗ Could not click next page")
                break

            await page.wait_for_timeout(4000)

        await browser.close()

    seen = set()
    deduped = []
    for r in results:
        key = r.get("company_name", "").strip().lower()
        if key and key not in seen:
            seen.add(key)
            r["source_url"] = url
            r["scraped_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
            deduped.append(r)

    print(f"  ✓ A2Z Events: {len(deduped)} exhibitors extracted")
    return deduped
