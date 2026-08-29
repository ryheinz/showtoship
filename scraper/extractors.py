"""
extractors.py — Turn collected listing HTML into exhibitor rows
-----------------------------------------------------------------

Replaces the previous "one giant CSS schema" approach, whose baseSelector
included `table tbody tr, table tr`. That matched every table row on the page —
navigation, layout tables, footers — while a card grid using an unlisted class
name matched nothing at all. Junk rows then passed the "did we get anything?"
check, so the run reported success and wrote noise to the database.

Instead: try several candidate container selectors, score what each produces,
and keep the best one. If nothing scores well, say so — a caller that knows the
extraction failed can fall back to the LLM, which is far better than silently
writing menu items into the leads table.
"""

import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

# Candidate selectors for "one exhibitor", most specific first.
CONTAINER_SELECTORS = [
    "[data-exhibitor]", ".exhibitor-item", ".exhibitor-card", ".exhibitor-row",
    ".exhibitor", ".company-card", ".company-item", ".vendor-item",
    ".participant-item", ".booth-item", ".directory-item", ".listing-item",
    "li.exhibitor", "tr.exhibitor-row",
    "article[class*=exhibitor]", "div[class*=exhibitor]", "li[class*=exhibitor]",
    "div[class*=company]", "li[class*=company]",
    "article[class*=card]", "div[class*=card]",
    "table tbody tr",
]

NAME_SELECTORS = [
    ".company-name", ".exhibitor-name", ".name", ".title",
    "h1", "h2", "h3", "h4", "h5",
    "a[href*=exhibitor]", "a[href*=company]", "strong", "b", "td:nth-of-type(1)",
]

FIELD_SELECTORS = {
    "booth_number": [".booth", ".booth-number", ".stand", ".stand-number", "[data-booth]", ".booth-no"],
    "hall":         [".hall", ".pavilion", ".hall-name", "[data-hall]"],
    "country":      [".country", ".nation", ".flag-label", "[data-country]"],
    "category":     [".category", ".sector", ".industry", ".product-group", ".tags", ".tag"],
    "products":     [".products", ".product-list", ".services", "[data-products]"],
    "description":  [".description", ".profile", ".about", ".excerpt", ".summary", "p"],
    "city":         [".city", ".town", "[data-city]"],
}

# Navigation and boilerplate that shows up when a selector is too greedy.
JUNK_NAMES = {
    "home", "about", "contact", "search", "login", "log in", "sign in", "sign up",
    "register", "menu", "back", "next", "previous", "close", "submit", "filter",
    "filters", "all", "show all", "clear", "reset", "more", "load more", "view all",
    "exhibitors", "exhibitor list", "exhibitor directory", "companies", "products",
    "privacy policy", "terms", "cookie policy", "imprint", "sitemap", "share",
    "name", "company", "booth", "country", "category", "stand", "hall",
}

# A legal suffix is strong evidence a string really is a company name.
COMPANY_SUFFIXES = (
    "gmbh", "ag", "inc", "inc.", "corp", "corp.", "ltd", "ltd.", "limited", "llc",
    "llp", "lp", "plc", "sa", "s.a.", "bv", "b.v.", "nv", "n.v.", "pty", "kg",
    "gbr", "e.v.", "e.k.", "s.l.", "s.p.a.", "spa", "sas", "sarl", "srl", "oy",
    "ab", "aps", "as", "a/s", "co", "co.", "company", "group", "holding",
    "technologies", "technology", "systems", "solutions", "industries",
    "international", "marine", "engineering", "electronics", "equipment",
)


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def _first_text(node, selectors: list[str]) -> str:
    for sel in selectors:
        try:
            found = node.select_one(sel)
        except Exception:
            continue
        if found:
            text = _clean(found.get_text(" "))
            if text:
                return text
    return ""


def looks_like_company(name: str) -> bool:
    """Conservative test for 'is this a company name or page furniture?'."""
    n = _clean(name)
    if len(n) < 2 or len(n) > 120:
        return False
    low = n.lower().strip(" .,")
    if low in JUNK_NAMES:
        return False
    if not re.search(r"[A-Za-z]", n):
        return False
    if n.count("\n") or low.startswith(("http", "www.", "©", "tel:", "mailto:")):
        return False
    # A bare single lowercase word is almost always a nav item.
    if " " not in n and n.islower():
        return False
    return True


def score_rows(rows: list[dict]) -> float:
    """
    How much does this extraction look like a real exhibitor list?

    Rewards volume, plausible names, and the presence of the fields that only a
    genuine directory has (booth numbers, countries, detail links).
    """
    named = [r for r in rows if looks_like_company(r.get("company_name", ""))]
    if not named:
        return 0.0

    unique = {r["company_name"].strip().lower() for r in named}
    uniqueness = len(unique) / max(len(named), 1)

    with_suffix = sum(
        1 for n in unique
        if any(n.endswith(" " + s) or n.endswith(s) for s in COMPANY_SUFFIXES))
    extras = sum(
        1 for r in named
        if r.get("booth_number") or r.get("country") or r.get("detail_url"))

    return (
        len(unique)                       # volume matters most
        * (0.5 + 0.5 * uniqueness)        # penalise the same name repeated
        * (1 + 1.5 * with_suffix / max(len(unique), 1))
        * (1 + 1.0 * extras / max(len(named), 1))
    )


def extract_with_selector(soup: BeautifulSoup, selector: str, base_url: str) -> list[dict]:
    """Pull rows using one candidate container selector."""
    try:
        nodes = soup.select(selector)
    except Exception:
        return []
    if not nodes or len(nodes) < 3:      # a real directory has more than a couple
        return []

    rows = []
    for node in nodes:
        name = _first_text(node, NAME_SELECTORS) or _clean(node.get_text(" "))[:120]
        if not looks_like_company(name):
            continue

        row = {"company_name": name}
        for field, sels in FIELD_SELECTORS.items():
            val = _first_text(node, sels)
            if val and val != name:
                row[field] = val

        for a in node.select("a[href]"):
            href = a.get("href", "").strip()
            if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
                continue
            absolute = urljoin(base_url, href)
            if re.search(r"exhibitor|company|booth|profile|detail", absolute, re.I):
                row.setdefault("detail_url", absolute)
            elif not re.search(r"(?:^|\.)(facebook|twitter|linkedin|instagram|youtube)\.", absolute, re.I):
                row.setdefault("website", absolute)

        mail = node.select_one('a[href^="mailto:"]')
        if mail:
            row["email"] = mail["href"].split(":", 1)[1].split("?")[0].strip()

        rows.append(row)

    return rows


def extract_best(html: str, base_url: str) -> tuple[list[dict], str, float]:
    """
    Try every candidate selector and return (rows, winning_selector, score).
    An empty result means no selector produced anything credible.
    """
    soup = BeautifulSoup(html, "lxml")
    best: tuple[list[dict], str, float] = ([], "", 0.0)

    for selector in CONTAINER_SELECTORS:
        rows = extract_with_selector(soup, selector, base_url)
        score = score_rows(rows)
        if score > best[2]:
            best = (rows, selector, score)

    return best


def extract_from_pages(pages_html: list[str], base_url: str) -> tuple[list[dict], str]:
    """
    Extract across every collected page, choosing the selector on the first
    page and reusing it for the rest so results stay consistent.
    """
    if not pages_html:
        return [], ""

    rows, selector, score = extract_best(pages_html[0], base_url)
    if not rows:
        return [], ""

    all_rows = list(rows)
    for html in pages_html[1:]:
        soup = BeautifulSoup(html, "lxml")
        page_rows = extract_with_selector(soup, selector, base_url)
        # If the chosen selector stops working on a later page (some sites
        # change layout mid-listing), re-pick rather than losing that page.
        if len(page_rows) < 3:
            page_rows, _, _ = extract_best(html, base_url)
        all_rows.extend(page_rows)

    return dedupe(all_rows), selector


DETAIL_SELECTORS = {
    "country":       [".country", "[itemprop=addressCountry]", "[data-country]"],
    "city":          [".city", "[itemprop=addressLocality]", "[data-city]"],
    "booth_number":  [".booth", ".booth-number", ".stand", "[data-booth]"],
    "hall":          [".hall", ".pavilion", "[data-hall]"],
    # No a[href^="tel:"] here — _first_text would take the link's label ("Call")
    # rather than the number. The href is read explicitly below.
    "phone":         ["[itemprop=telephone]", ".phone", ".tel"],
    "description":   ["[itemprop=description]", ".description", ".profile", ".company-description",
                      ".about", ".exhibitor-description"],
    "products":      [".products", ".product-list", ".services", ".product-categories"],
    "category":      [".category", ".sector", ".industry", ".tags"],
    "contact_person": [".contact-name", ".contact-person", "[itemprop=employee]"],
}

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")

SOCIAL_HOSTS = {
    "social_linkedin": "linkedin.com",
    "social_twitter": "twitter.com",
}


def extract_detail_page(html: str, url: str) -> dict:
    """
    Pull what we can from an individual exhibitor profile page without an LLM.

    Deep scrape previously ran an LLM strategy unconditionally, so with no API
    key configured every profile came back empty and the run said nothing.
    """
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()

    out: dict = {}
    for field, sels in DETAIL_SELECTORS.items():
        val = _first_text(soup, sels)
        if val:
            out[field] = val[:2000]

    mail = soup.select_one('a[href^="mailto:"]')
    if mail:
        out["email"] = mail["href"].split(":", 1)[1].split("?")[0].strip()
    else:
        found = EMAIL_RE.search(soup.get_text(" "))
        if found:
            out["email"] = found.group(0)

    tel = soup.select_one('a[href^="tel:"]')
    if tel:
        # A tel: href is more reliable than any scraped text, so it wins.
        out["phone"] = tel["href"].split(":", 1)[1].strip()
    if out.get("phone") and not re.search(r"\d{5}", out["phone"]):
        out.pop("phone")        # a label, not a number

    for a in soup.select("a[href]"):
        href = urljoin(url, a.get("href", ""))
        for field, host in SOCIAL_HOSTS.items():
            if host in href and field not in out:
                out[field] = href
        if ("website" not in out
                and re.match(r"^https?://", href)
                and urlparse_host(href) not in (urlparse_host(url), "")
                and not any(h in href for h in SOCIAL_HOSTS.values())):
            out["website"] = href

    return {k: v for k, v in out.items() if v}


def urlparse_host(u: str) -> str:
    from urllib.parse import urlparse
    try:
        return urlparse(u).hostname or ""
    except Exception:
        return ""


def dedupe(rows: list[dict]) -> list[dict]:
    """Collapse rows by company name, keeping the most complete version."""
    by_name: dict[str, dict] = {}
    for row in rows:
        key = _clean(row.get("company_name", "")).lower()
        if not key:
            continue
        existing = by_name.get(key)
        if not existing:
            by_name[key] = row
            continue
        for field, value in row.items():
            if value and not existing.get(field):
                existing[field] = value
    return list(by_name.values())
