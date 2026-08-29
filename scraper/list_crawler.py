"""
list_crawler.py — Multi-page collection of exhibitor list pages
-----------------------------------------------------------------

The generic scraper used to load exactly one URL, scroll it, and extract.
Any show whose directory paginated ("Page 1 of 34") returned only page 1, and
nothing said so — the job reported success with a fraction of the exhibitors.

This module walks a listing to the end using, in order of preference:

  1. "Load more" / infinite scroll  — click and scroll until the count stops growing
  2. A rel=next / "Next" pager      — follow it, page after page
  3. Numbered pager links           — visit each one
  4. A ?page=N style URL template   — increment until a page adds nothing new

Every strategy stops on the same guards: no new content, a repeated page
fingerprint, a hard page cap, or a wall-clock budget. The result is the raw
HTML of every page visited, which the caller extracts from as one corpus.
"""

import asyncio
import hashlib
import re
from urllib.parse import urljoin, urlparse, parse_qs, urlencode, urlunparse

# Text on "go to next page" controls, lowercased and stripped.
NEXT_TEXTS = {"next", "next page", "next »", "›", "»", ">", "→", "weiter", "suivant", "siguiente"}

# Text on "show more results" controls.
MORE_TEXTS = {"load more", "show more", "more results", "view more", "see more",
              "load more exhibitors", "show all", "mehr laden", "voir plus"}

BROWSER_ARGS = ["--disable-gpu", "--no-sandbox", "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled"]

USER_AGENT = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")


def _fingerprint(html: str) -> str:
    """Cheap content hash used to notice we've been served the same page twice."""
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html or ""))
    return hashlib.sha1(text.strip()[:200_000].encode("utf-8", "ignore")).hexdigest()


class ListCrawler:
    """Collects the HTML of every page of a paginated listing."""

    def __init__(self, max_pages: int = 100, page_timeout_ms: int = 45_000,
                 settle_ms: int = 1200, budget_seconds: int = 900, verbose: bool = True):
        self.max_pages = max_pages
        self.page_timeout_ms = page_timeout_ms
        self.settle_ms = settle_ms
        self.budget_seconds = budget_seconds
        self.verbose = verbose

    def _log(self, msg: str):
        if self.verbose:
            print(msg, flush=True)

    async def collect(self, url: str) -> list[str]:
        """Return the HTML of each page of the listing at `url`."""
        from playwright.async_api import async_playwright

        pages_html: list[str] = []
        seen_prints: set[str] = set()
        deadline = asyncio.get_event_loop().time() + self.budget_seconds

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True, args=BROWSER_ARGS)
            context = await browser.new_context(user_agent=USER_AGENT)
            page = await context.new_page()
            # Images and fonts are pure cost here; the data is in the markup.
            await page.route("**/*", lambda route: asyncio.ensure_future(
                route.abort() if route.request.resource_type in ("image", "media", "font")
                else route.continue_()))

            try:
                if not await self._goto(page, url):
                    return []

                await self._exhaust_in_page(page)
                html = await page.content()
                pages_html.append(html)
                seen_prints.add(_fingerprint(html))
                self._log(f"  → page 1 collected ({len(html):,} bytes)")

                # Strategy 2/3: follow the site's own pager.
                followed = await self._follow_pager(page, pages_html, seen_prints, deadline)

                # Strategy 4: only if the site exposed no pager at all.
                if not followed:
                    await self._walk_url_template(page, url, pages_html, seen_prints, deadline)

            finally:
                await browser.close()

        self._log(f"  ✓ collected {len(pages_html)} page(s)")
        return pages_html

    # ── navigation helpers ────────────────────────────────────────────────

    async def _goto(self, page, url: str, attempts: int = 3) -> bool:
        """Load a URL, retrying transient failures instead of giving up at once."""
        for attempt in range(1, attempts + 1):
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=self.page_timeout_ms)
                try:
                    await page.wait_for_load_state("networkidle", timeout=10_000)
                except Exception:
                    pass          # networkidle never settles on sites with polling
                await page.wait_for_timeout(self.settle_ms)
                return True
            except Exception as e:
                self._log(f"  ! load attempt {attempt}/{attempts} failed for {url}: {type(e).__name__}")
                if attempt == attempts:
                    self._log(f"  ✗ giving up on {url}")
                    return False
                await asyncio.sleep(2 * attempt)
        return False

    async def _exhaust_in_page(self, page):
        """Scroll to the bottom and click any 'load more' control until spent."""
        stable_rounds = 0
        last_height = 0

        for _ in range(60):
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(700)

            clicked = await self._click_more(page)
            if clicked:
                await page.wait_for_timeout(self.settle_ms)
                stable_rounds = 0
                continue

            height = await page.evaluate("document.body.scrollHeight")
            if height == last_height:
                stable_rounds += 1
                # Two quiet rounds means lazy loading has finished.
                if stable_rounds >= 2:
                    break
            else:
                stable_rounds = 0
            last_height = height

    async def _click_more(self, page) -> bool:
        """Click a visible 'load more' button. True if one was clicked."""
        return await page.evaluate(
            """(texts) => {
                const els = [...document.querySelectorAll('button, a, [role=button], input[type=button]')];
                for (const el of els) {
                    const label = (el.innerText || el.value || '').trim().toLowerCase();
                    if (!label || !texts.includes(label)) continue;
                    if (el.disabled || el.getAttribute('aria-disabled') === 'true') continue;
                    const r = el.getBoundingClientRect();
                    if (r.width === 0 && r.height === 0) continue;
                    el.click();
                    return true;
                }
                return false;
            }""",
            list(MORE_TEXTS),
        )

    async def _follow_pager(self, page, pages_html, seen_prints, deadline) -> bool:
        """
        Walk a next-link pager. Returns True if the site had one at all, so the
        caller knows not to fall back to URL guessing.
        """
        had_pager = False

        while len(pages_html) < self.max_pages:
            if asyncio.get_event_loop().time() > deadline:
                self._log("  ! page budget exhausted — stopping pagination")
                break

            clicked = await page.evaluate(
                """(texts) => {
                    const isDisabled = el =>
                        el.getAttribute('aria-disabled') === 'true' ||
                        el.classList.contains('disabled') ||
                        el.classList.contains('aspNetDisabled') ||
                        (el.parentElement && el.parentElement.classList.contains('disabled'));

                    // rel="next" is the unambiguous case.
                    const rel = document.querySelector('a[rel~=next], link[rel~=next]');
                    if (rel && !isDisabled(rel)) { rel.click(); return true; }

                    const cands = [...document.querySelectorAll(
                        '.pagination a, .pager a, [class*=pagination] a, [class*=pager] a, nav a, a')];
                    for (const el of cands) {
                        const label = (el.innerText || el.getAttribute('aria-label') || '').trim().toLowerCase();
                        if (!texts.includes(label)) continue;
                        if (isDisabled(el)) continue;
                        const r = el.getBoundingClientRect();
                        if (r.width === 0 && r.height === 0) continue;
                        el.click();
                        return true;
                    }
                    return false;
                }""",
                list(NEXT_TEXTS),
            )

            if not clicked:
                break
            had_pager = True

            # Wait for the content to actually change rather than sleeping a
            # fixed interval and hoping — a slow postback used to mean the same
            # page got scraped twice and a later one was missed entirely.
            if not await self._wait_for_change(page, seen_prints):
                self._log("  ! next page never changed the content — stopping")
                break

            await self._exhaust_in_page(page)
            html = await page.content()
            fp = _fingerprint(html)
            if fp in seen_prints:
                self._log("  ! repeated page content — reached the end")
                break
            seen_prints.add(fp)
            pages_html.append(html)
            self._log(f"  → page {len(pages_html)} collected")

        return had_pager

    async def _wait_for_change(self, page, seen_prints, timeout_ms: int = 20_000) -> bool:
        """Poll until the page fingerprint is one we haven't seen."""
        waited = 0
        step = 400
        while waited < timeout_ms:
            await page.wait_for_timeout(step)
            waited += step
            try:
                fp = _fingerprint(await page.content())
            except Exception:
                continue
            if fp not in seen_prints:
                return True
        return False

    async def _walk_url_template(self, page, url: str, pages_html, seen_prints, deadline):
        """
        Last resort: if the URL carries a page/offset parameter, increment it.
        Only used when the page exposed no pager we could click.
        """
        parsed = urlparse(url)
        params = parse_qs(parsed.query)
        page_key = next((k for k in params
                         if k.lower() in ("page", "p", "pg", "pagenumber", "pageindex")), None)
        if not page_key:
            return

        try:
            start = int(params[page_key][0])
        except (ValueError, IndexError):
            start = 1

        for n in range(start + 1, start + self.max_pages):
            if asyncio.get_event_loop().time() > deadline:
                return
            params[page_key] = [str(n)]
            next_url = urlunparse(parsed._replace(query=urlencode(params, doseq=True)))
            if not await self._goto(page, next_url):
                return
            await self._exhaust_in_page(page)
            html = await page.content()
            fp = _fingerprint(html)
            if fp in seen_prints:
                self._log(f"  ! ?{page_key}={n} repeated earlier content — reached the end")
                return
            seen_prints.add(fp)
            pages_html.append(html)
            self._log(f"  → page {len(pages_html)} collected (?{page_key}={n})")
