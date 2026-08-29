"""
llm_extract.py — LLM extraction for pages the CSS selectors can't read
------------------------------------------------------------------------

The previous fallback called

    strategy.extract(text, ignore_llm=True)

which passes the page text where crawl4ai expects a URL *and* explicitly asks
it not to use the model. Turning on "LLM Extraction" therefore never called an
LLM at all — it fell through to a text heuristic that scraped headings and menu
items into the leads table.

This talks to the provider directly over HTTP so the behaviour is verifiable:
OpenAI-compatible chat completions, or a local Ollama.
"""

import json
import os
import re

import aiohttp

PROMPT = """You are extracting exhibitor data from a trade show directory page.

Extract EVERY company or exhibitor listed. Ignore navigation, filters, headers,
footers, cookie notices, and any text that is not an exhibitor entry.

Return a JSON array. Each object may contain these keys (omit a key entirely if
the page does not show that information — never invent a value):

  company_name, booth_number, hall, country, city, category, products,
  description, website, detail_url, email, phone

Return ONLY the JSON array. No markdown fences, no commentary.
"""

# Roughly 40k characters per request keeps well inside a small model's context.
CHUNK_CHARS = 40_000


def _provider() -> tuple[str, str, str]:
    """Return (kind, endpoint, model) for the configured provider."""
    explicit = os.environ.get("LLM_PROVIDER", "")

    if explicit.startswith("ollama/"):
        host = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
        return "ollama", f"{host.rstrip('/')}/api/chat", explicit.split("/", 1)[1]

    if os.environ.get("OPENAI_API_KEY"):
        model = explicit.split("/", 1)[1] if explicit.startswith("openai/") else "gpt-4o-mini"
        return "openai", "https://api.openai.com/v1/chat/completions", model

    if os.environ.get("ANTHROPIC_API_KEY"):
        model = explicit.split("/", 1)[1] if explicit.startswith("anthropic/") else "claude-haiku-4-5"
        return "anthropic", "https://api.anthropic.com/v1/messages", model

    host = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
    return "ollama", f"{host.rstrip('/')}/api/chat", "llama3"


def _parse_json_array(raw: str) -> list[dict]:
    """Pull a JSON array out of a model response that may be wrapped in prose."""
    if not raw:
        return []
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.M).strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\[[\s\S]*\]", text)
        if not match:
            return []
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            return []

    if isinstance(data, dict):
        for value in data.values():
            if isinstance(value, list):
                data = value
                break
        else:
            data = [data]

    return [d for d in data if isinstance(d, dict) and d.get("company_name")]


async def _call(session: aiohttp.ClientSession, kind: str, endpoint: str,
                model: str, content: str) -> str:
    timeout = aiohttp.ClientTimeout(total=180)

    if kind == "anthropic":
        headers = {
            "x-api-key": os.environ["ANTHROPIC_API_KEY"],
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model,
            "max_tokens": 8000,
            "system": PROMPT,
            "messages": [{"role": "user", "content": content}],
        }
        async with session.post(endpoint, headers=headers, json=payload, timeout=timeout) as r:
            if r.status != 200:
                print(f"  ✗ LLM HTTP {r.status}: {(await r.text())[:200]}")
                return ""
            body = await r.json()
            return "".join(b.get("text", "") for b in body.get("content", []))

    if kind == "openai":
        headers = {
            "Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": PROMPT},
                {"role": "user", "content": content},
            ],
        }
        async with session.post(endpoint, headers=headers, json=payload, timeout=timeout) as r:
            if r.status != 200:
                print(f"  ✗ LLM HTTP {r.status}: {(await r.text())[:200]}")
                return ""
            body = await r.json()
            return body.get("choices", [{}])[0].get("message", {}).get("content", "")

    # Ollama
    payload = {
        "model": model,
        "stream": False,
        "options": {"temperature": 0},
        "messages": [
            {"role": "system", "content": PROMPT},
            {"role": "user", "content": content},
        ],
    }
    async with session.post(endpoint, json=payload, timeout=timeout) as r:
        if r.status != 200:
            print(f"  ✗ LLM HTTP {r.status}: {(await r.text())[:200]}")
            return ""
        body = await r.json()
        return body.get("message", {}).get("content", "")


async def extract_exhibitors(page_text: str) -> list[dict]:
    """Run the model over the page text (in chunks) and merge the results."""
    kind, endpoint, model = _provider()
    print(f"  🤖  LLM extraction via {kind}/{model}")

    chunks = [page_text[i:i + CHUNK_CHARS] for i in range(0, len(page_text), CHUNK_CHARS)] or [""]
    if len(chunks) > 1:
        print(f"      page split into {len(chunks)} chunks")

    rows: list[dict] = []
    async with aiohttp.ClientSession() as session:
        for n, chunk in enumerate(chunks, 1):
            try:
                raw = await _call(session, kind, endpoint, model, chunk)
            except Exception as e:
                print(f"  ✗ LLM chunk {n} failed: {type(e).__name__}: {e}")
                continue
            found = _parse_json_array(raw)
            print(f"      chunk {n}/{len(chunks)} → {len(found)} exhibitors")
            rows.extend(found)

    return rows
