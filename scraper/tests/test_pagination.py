"""
Pagination test — run with:  python3 scraper/tests/test_pagination.py

Serves a synthetic 5-page exhibitor directory on localhost and checks that the
crawler walks all of it. This is the regression the whole rewrite exists for:
the old scraper loaded page 1, extracted, and reported success — so a 5-page
show came back 80% short with nothing to indicate anything was missing.

Requires playwright + chromium. Skips (exit 0) if they aren't installed.
"""

import asyncio
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

PAGES = 5
PER_PAGE = 12
PORT = 8731


def build_page(n: int) -> bytes:
    cards = "".join(
        f'<div class="exhibitor-card">'
        f'<h3 class="company-name">Exhibitor {(n - 1) * PER_PAGE + i} Ltd</h3>'
        f'<span class="booth">P{n}-{i}</span>'
        f'<span class="country">Norway</span>'
        f'</div>'
        for i in range(1, PER_PAGE + 1))
    nxt = f'<a class="next" href="/?page={n + 1}">Next</a>' if n < PAGES else '<span>Next</span>'
    return f"""<!doctype html><html><body>
      <nav><a href="/">Home</a><a href="/about">About</a></nav>
      <h1>Exhibitor Directory — page {n} of {PAGES}</h1>
      <div class="results">{cards}</div>
      <div class="pagination">{nxt}</div>
    </body></html>""".encode()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        page = 1
        if "page=" in self.path:
            try:
                page = int(self.path.split("page=")[1].split("&")[0])
            except ValueError:
                page = 1
        body = build_page(max(1, min(page, PAGES)))
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


async def main() -> int:
    try:
        import playwright  # noqa: F401
    except ImportError:
        print("SKIP  playwright not installed")
        return 0

    from list_crawler import ListCrawler
    from extractors import extract_from_pages

    server = HTTPServer(("127.0.0.1", PORT), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    try:
        crawler = ListCrawler(max_pages=20, verbose=True)
        pages = await crawler.collect(f"http://127.0.0.1:{PORT}/?page=1")
        rows, selector = extract_from_pages(pages, f"http://127.0.0.1:{PORT}/")
    except Exception as e:
        print(f"SKIP  browser unavailable: {type(e).__name__}: {e}")
        return 0
    finally:
        server.shutdown()

    expected = PAGES * PER_PAGE
    ok = True

    def check(name, cond, extra=""):
        nonlocal ok
        print(("PASS  " if cond else "FAIL  ") + name + (f"  {extra}" if extra else ""))
        ok = ok and cond

    check(f"collected all {PAGES} pages", len(pages) == PAGES, f"got {len(pages)}")
    check(f"extracted all {expected} exhibitors", len(rows) == expected, f"got {len(rows)}")
    check("used the card selector", selector == ".exhibitor-card", f"got {selector!r}")
    names = {r["company_name"] for r in rows}
    check("first and last exhibitor both present",
          "Exhibitor 1 Ltd" in names and f"Exhibitor {expected} Ltd" in names)
    check("no nav junk", not (names & {"Home", "About", "Next"}))

    print("\nRESULT:", "ALL PASS" if ok else "FAILURES ABOVE")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
