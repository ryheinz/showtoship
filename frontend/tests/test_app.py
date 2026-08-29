"""
Frontend smoke test — run with:  python3 frontend/tests/test_app.py

Serves frontend/ locally and drives it with Playwright. Covers the onboarding
flow (invite link → set password), which had no implementation at all before,
plus the escaping and CSV helpers that data correctness depends on.
"""
import asyncio
import os
import subprocess
import sys
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, HTTPServer

from playwright.async_api import async_playwright

PORT = 8799
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
BASE = f"http://127.0.0.1:{PORT}/index.html"
ok = True
def check(name, cond, extra=""):
    global ok
    print(("PASS  " if cond else "FAIL  ")+name+(f"  {extra}" if extra else ""))
    ok = ok and cond

class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


async def main():
    server = HTTPServer(("127.0.0.1", PORT), partial(QuietHandler, directory=ROOT))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    time.sleep(0.5)
    try:
        return await run(server)
    finally:
        server.shutdown()


async def run(server):
    async with async_playwright() as pw:
        b = await pw.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = await b.new_context()
        page = await ctx.new_page()
        errors=[]
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append("console.error: "+m.text) if m.type=="error" else None)

        # ── 1. cold load: should show the sign-in view
        await page.goto(BASE, wait_until="networkidle")
        await page.wait_for_timeout(1200)
        check("no uncaught JS errors on load", not [e for e in errors if "Failed to load resource" not in e],
              "; ".join(errors[:3]))
        check("auth overlay visible", await page.is_visible("#auth-overlay"))
        check("sign-in view shown", await page.is_visible("#auth-view-signin"))
        check("set-password view hidden", not await page.is_visible("#auth-view-setpw"))
        check("forgot-password link present", await page.is_visible("#auth-view-signin .auth-link"))

        # ── 2. forgot-password view toggles
        await page.click("#auth-view-signin .auth-link")
        await page.wait_for_timeout(300)
        check("forgot view shown", await page.is_visible("#auth-view-forgot"))
        await page.click("#auth-view-forgot .auth-link")
        await page.wait_for_timeout(300)
        check("back to sign-in works", await page.is_visible("#auth-view-signin"))

        # ── 3. THE onboarding fix: an invite link must open the set-password screen
        errors.clear()
        await page.goto("about:blank")
        await page.goto(BASE + "?t=1#access_token=faketoken123&refresh_token=r1&type=invite")
        await page.wait_for_timeout(2500)
        check("invite link opens set-password screen", await page.is_visible("#auth-view-setpw"))
        check("invite link does NOT show plain sign-in", not await page.is_visible("#auth-view-signin"))
        check("token scrubbed from the URL", "access_token" not in page.url, page.url)

        # ── 4. set-password validation
        await page.fill("#setpw-password", "short")
        await page.fill("#setpw-confirm", "short")
        await page.click("#setpw-btn")
        await page.wait_for_timeout(400)
        err = await page.inner_text("#setpw-error")
        check("rejects a short password", "at least 8" in err, err)

        await page.fill("#setpw-password", "correcthorse1")
        await page.fill("#setpw-confirm", "differentone1")
        await page.click("#setpw-btn")
        await page.wait_for_timeout(400)
        err = await page.inner_text("#setpw-error")
        check("rejects mismatched passwords", "match" in err.lower(), err)

        # ── 5. expired-link handling
        await page.goto("about:blank")
        await page.goto(BASE + "?t=2#error=access_denied&error_code=otp_expired&error_description=Email+link+is+invalid+or+has+expired")
        await page.wait_for_timeout(1200)
        check("expired link shows sign-in with guidance", await page.is_visible("#auth-view-signin"))
        msg = await page.inner_text("#auth-error")
        check("expired link explains what to do", "expired" in msg.lower() and "resend" in msg.lower(), msg)

        # ── 6. helpers behave
        res = await page.evaluate("""() => ({
            esc: esc(`O'Brien <script> "x"`),
            safeJs: safeUrl('javascript:alert(1)'),
            safeBare: safeUrl('acme.de'),
            safeHttp: safeUrl('https://acme.de'),
            csvQuoted: readCSV('a,b\\r\\nx,"y,z"\\r\\n').rows[0],
            csvHeaders: readCSV('Company,First Name\\r\\nAcme,Jo\\r\\n').headers,
            mapped: mapCSVHeader('First Name'),
            lead: rowToLead(['Company','First Name','Last Name','Email'], ['Acme GmbH','Jo','Smith','jo@acme.de']),
        })""")
        check("esc escapes quotes and brackets",
              res["esc"] == "O&#39;Brien &lt;script&gt; &quot;x&quot;", res["esc"])
        check("safeUrl blocks javascript:", res["safeJs"] == "", repr(res["safeJs"]))
        check("safeUrl upgrades bare domains", res["safeBare"] == "https://acme.de", res["safeBare"])
        check("safeUrl passes https through", res["safeHttp"] == "https://acme.de")
        check("CSV handles quoted commas + CRLF", res["csvQuoted"] == ["x", "y,z"], str(res["csvQuoted"]))
        check("CSV headers have no stray \\r", res["csvHeaders"] == ["Company", "First Name"], str(res["csvHeaders"]))
        check("'First Name' maps", res["mapped"] == "first_name", str(res["mapped"]))
        check("row builds contact_name from first+last",
              res["lead"].get("contact_name") == "Jo Smith" and res["lead"].get("company_name") == "Acme GmbH",
              str(res["lead"]))

        await b.close()
    print("\nRESULT:", "ALL PASS" if ok else "FAILURES ABOVE")
    return 0 if ok else 1

sys.exit(asyncio.run(main()))
