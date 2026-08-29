"""
Extraction tests — run with:  python3 scraper/tests/test_extractors.py

These cover the failure that made scrape quality poor: a container selector
that matched page furniture, and multi-page listings that were never merged.
"""
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from extractors import extract_best, extract_from_pages, looks_like_company, dedupe, extract_detail_page

NAV = """
<nav><a href="/">Home</a><a href="/about">About</a><a href="/contact">Contact</a></nav>
<table class="layout"><tr><td>Search</td><td>Login</td></tr></table>
"""

# 1. Card-grid directory
CARDS = NAV + """
<div class="results">
""" + "".join(f"""
  <div class="exhibitor-card">
    <h3 class="company-name">Acme Marine {i} GmbH</h3>
    <span class="booth">A{i}0</span>
    <span class="country">Germany</span>
    <a href="/exhibitors/acme-{i}">Details</a>
    <a href="https://acme{i}.example.com">Website</a>
  </div>""" for i in range(1, 26)) + "</div>"

# 2. Table directory (the shape the old catch-all selector mangled)
ROWS = NAV + """
<table class="exhibitor-table"><tbody>
""" + "".join(f"""
  <tr class="exhibitor-row">
    <td><a class="exhibitorName" href="/e/{i}">Nordic Shipping {i} AS</a></td>
    <td class="country">Norway</td>
    <td class="booth">B{i}</td>
  </tr>""" for i in range(1, 31)) + "</tbody></table>"

# 3. A page with NO exhibitors — must not invent any
EMPTY = NAV + "<main><h1>Exhibitor list coming soon</h1><p>Check back in January.</p></main>"

def check(name, cond, extra=""):
    print(("PASS  " if cond else "FAIL  ") + name + (f"  {extra}" if extra else ""))
    return cond

ok = True

rows, sel, score = extract_best(CARDS, "https://show.example.com/exhibitors")
ok &= check("cards: found 25", len(rows) == 25, f"got {len(rows)} via {sel!r}")
ok &= check("cards: booth captured", rows[0].get("booth_number") == "A10", str(rows[0]))
ok &= check("cards: country captured", rows[0].get("country") == "Germany")
ok &= check("cards: detail_url absolute",
            rows[0].get("detail_url", "").startswith("https://show.example.com/exhibitors/acme-"),
            rows[0].get("detail_url", ""))
ok &= check("cards: no nav junk", not any(r["company_name"].lower() in ("home", "about", "contact") for r in rows))

rows2, sel2, _ = extract_best(ROWS, "https://show.example.com/e")
ok &= check("table: found 30", len(rows2) == 30, f"got {len(rows2)} via {sel2!r}")
ok &= check("table: name not the whole row",
            rows2[0]["company_name"] == "Nordic Shipping 1 AS", rows2[0]["company_name"])

rows3, sel3, score3 = extract_best(EMPTY, "https://show.example.com")
ok &= check("empty page yields nothing", len(rows3) == 0, f"got {len(rows3)}: {[r['company_name'] for r in rows3][:5]}")

# Multi-page: pagination is the headline fix, so results must accumulate
p1 = NAV + "<div>" + "".join(f'<div class="exhibitor-card"><h3 class="company-name">Alpha {i} Ltd</h3><span class="booth">A{i}</span></div>' for i in range(1, 11)) + "</div>"
p2 = NAV + "<div>" + "".join(f'<div class="exhibitor-card"><h3 class="company-name">Beta {i} Ltd</h3><span class="booth">B{i}</span></div>' for i in range(1, 11)) + "</div>"
p3 = NAV + "<div>" + "".join(f'<div class="exhibitor-card"><h3 class="company-name">Gamma {i} Ltd</h3><span class="booth">C{i}</span></div>' for i in range(1, 11)) + "</div>"
merged, msel = extract_from_pages([p1, p2, p3], "https://show.example.com")
ok &= check("3 pages merge to 30", len(merged) == 30, f"got {len(merged)}")

# Duplicates across pages collapse, richer row wins
dup = dedupe([
    {"company_name": "Acme GmbH", "booth_number": "A1"},
    {"company_name": "acme gmbh", "country": "Germany", "email": "a@acme.de"},
])
ok &= check("dedupe collapses case-insensitively", len(dup) == 1, str(dup))
ok &= check("dedupe merges fields",
            dup[0].get("booth_number") == "A1" and dup[0].get("email") == "a@acme.de", str(dup[0]))

# Name heuristics
ok &= check("rejects nav labels", not looks_like_company("Next") and not looks_like_company("home"))
ok &= check("accepts real names", looks_like_company("Rolls-Royce Marine AS") and looks_like_company("ABB"))

# Detail page extraction (deep scrape without an LLM)
DETAIL = """<html><body>
 <h1>Acme Marine GmbH</h1>
 <div class="description">We build propulsion systems.</div>
 <span class="country">Germany</span><span class="city">Hamburg</span>
 <a href="mailto:info@acme.de">Email</a>
 <a href="tel:+4940123456">Call</a>
 <a href="https://www.linkedin.com/company/acme">LinkedIn</a>
 <a href="https://acme.de">Site</a>
</body></html>"""
d = extract_detail_page(DETAIL, "https://show.example.com/e/1")
ok &= check("detail: email", d.get("email") == "info@acme.de", str(d.get("email")))
ok &= check("detail: phone", d.get("phone") == "+4940123456", str(d.get("phone")))
ok &= check("detail: country", d.get("country") == "Germany")
ok &= check("detail: linkedin", "linkedin.com" in d.get("social_linkedin", ""))
ok &= check("detail: website not the show host", d.get("website") == "https://acme.de", str(d.get("website")))

print("\nRESULT:", "ALL PASS" if ok else "FAILURES ABOVE")
sys.exit(0 if ok else 1)
