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
}


def get_config_for_domain(url: str) -> dict | None:
    for domain, config in CONFIGS.items():
        if domain in url:
            return config
    return None


def get_playwright_scraper(name: str):
    scrapers = {
        "scrape_euronaval": _scrape_euronaval,
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
