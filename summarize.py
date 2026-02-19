#!/usr/bin/env python3
"""
Supawork AI Scholar Summarizer

Summarize and translate academic papers using the Supawork AI Scholar API.
Uses Playwright for stealth content extraction. No API key required.

Accepts either a URL or a local PDF file.

Usage:
    python summarize.py <url_or_pdf> [--output <path>] [--translate <lang>]
    python summarize.py https://arxiv.org/abs/1706.03762
    python summarize.py paper.pdf
    python summarize.py paper.pdf --translate chinese
"""

import argparse
import json
import random
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright

SUPAWORK_API_URL = "https://supawork.ai/supawork/headshot/api/media/ext/gpt/chat"

# Rate limiting: track requests in a local file
RATE_LIMIT_FILE = Path(__file__).parent / ".rate_limit_log"
MIN_INTERVAL_SECONDS = 5
MAX_REQUESTS_PER_HOUR = 10

LANGUAGES = {
    "chinese": "中文",
    "english": "English",
    "spanish": "Spanish",
    "french": "French",
    "german": "German",
    "japanese": "Japanese",
    "korean": "Korean",
    "portuguese": "Portuguese",
    "russian": "Russian",
    "arabic": "Arabic",
    "hindi": "Hindi",
    "italian": "Italian",
}


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------

def check_rate_limit():
    """Enforce rate limits."""
    now = time.time()
    window = 3600

    timestamps = []
    if RATE_LIMIT_FILE.exists():
        try:
            timestamps = json.loads(RATE_LIMIT_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            timestamps = []

    timestamps = [ts for ts in timestamps if now - ts < window]

    if timestamps:
        elapsed = now - max(timestamps)
        if elapsed < MIN_INTERVAL_SECONDS:
            wait = MIN_INTERVAL_SECONDS - elapsed
            print(f"Rate limit: waiting {wait:.1f}s between requests...")
            time.sleep(wait)

    if len(timestamps) >= MAX_REQUESTS_PER_HOUR:
        oldest = min(timestamps)
        retry_after = window - (now - oldest)
        print(
            f"Rate limit reached ({MAX_REQUESTS_PER_HOUR} requests/hour). "
            f"Try again in {retry_after / 60:.0f} minute(s).",
            file=sys.stderr,
        )
        sys.exit(1)


def record_request():
    """Record a request timestamp for rate limiting."""
    now = time.time()
    window = 3600

    timestamps = []
    if RATE_LIMIT_FILE.exists():
        try:
            timestamps = json.loads(RATE_LIMIT_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            timestamps = []

    timestamps = [ts for ts in timestamps if now - ts < window]
    timestamps.append(now)
    RATE_LIMIT_FILE.write_text(json.dumps(timestamps))


# ---------------------------------------------------------------------------
# Proxy rotation
# ---------------------------------------------------------------------------

_PROXY_FORCE: bool | None = None


def _load_proxy_config() -> dict:
    config_path = Path.home() / ".scholar-proxies.json"
    try:
        return json.loads(config_path.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {"enabled": False, "proxies": []}


def _get_proxy_url() -> str | None:
    config = _load_proxy_config()
    enabled = _PROXY_FORCE if _PROXY_FORCE is not None else config.get("enabled", False)
    if not enabled:
        return None
    proxies = config.get("proxies", [])
    if not proxies:
        return None
    return random.choice(proxies)


def _get_proxy() -> dict | None:
    url = _get_proxy_url()
    if not url:
        return None
    return {"http": url, "https": url}


# ---------------------------------------------------------------------------
# Stealth Playwright browser
# ---------------------------------------------------------------------------

def _launch_browser(p):
    proxy_url = _get_proxy_url()
    browser = p.chromium.launch(
        headless=True,
        proxy={"server": proxy_url} if proxy_url else None,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-extensions",
        ],
    )

    context = browser.new_context(
        user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
        viewport={"width": 1280, "height": 720},
        locale="en-US",
        timezone_id="America/New_York",
        java_script_enabled=True,
    )

    page = context.new_page()

    page.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        Object.defineProperty(navigator, 'plugins', {
            get: () => [1, 2, 3, 4, 5],
        });
        Object.defineProperty(navigator, 'languages', {
            get: () => ['en-US', 'en'],
        });
        window.chrome = { runtime: {} };
    """)

    return browser, context, page


def _wait_for_cloudflare(page, timeout_seconds: int = 30):
    start = time.time()
    while time.time() - start < timeout_seconds:
        title = page.title().lower()
        body_text = page.text_content("body") or ""
        if "just a moment" in title or "checking your browser" in body_text.lower():
            print("Cloudflare challenge detected, waiting for it to resolve...")
            time.sleep(2)
            continue
        break


# ---------------------------------------------------------------------------
# Content extraction
# ---------------------------------------------------------------------------

def _rewrite_to_pdf_url(url: str) -> str | None:
    import re as _re
    m = _re.match(r"https?://arxiv\.org/abs/(.+?)(?:\?.*)?$", url)
    if m:
        return f"https://arxiv.org/pdf/{m.group(1)}"
    m = _re.match(r"https?://arxiv\.org/html/(.+?)(?:\?.*)?$", url)
    if m:
        return f"https://arxiv.org/pdf/{m.group(1)}"
    return None


def _download_pdf_text(url: str) -> tuple[str, str | None]:
    import tempfile
    from pdfminer.high_level import extract_text

    print(f"Downloading PDF: {url}")
    resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=60, proxies=_get_proxy())
    if not resp.ok:
        print(f"Failed to download PDF (HTTP {resp.status_code})", file=sys.stderr)
        sys.exit(1)
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        f.write(resp.content)
        tmp_path = f.name
    try:
        text = extract_text(tmp_path)
    finally:
        Path(tmp_path).unlink(missing_ok=True)
    return text.strip(), None


def _extract_text_from_url(url: str) -> tuple[str, str | None]:
    pdf_url = _rewrite_to_pdf_url(url)
    if pdf_url:
        return _download_pdf_text(pdf_url)

    print(f"Extracting content from URL: {url}")
    with sync_playwright() as p:
        browser, context, page = _launch_browser(p)

        page.goto(url, wait_until="networkidle", timeout=60000)
        _wait_for_cloudflare(page)

        page_title = page.evaluate("() => document.title || null")
        text = page.evaluate("() => document.body.innerText")

        browser.close()

        if not text or len(text) < 50:
            print("Warning: extracted very little text from the page.", file=sys.stderr)

        return text, page_title


def _extract_text_from_pdf(pdf_path: str) -> str:
    from pdfminer.high_level import extract_text

    pdf_file = Path(pdf_path).resolve()
    if not pdf_file.exists():
        print(f"File not found: {pdf_file}", file=sys.stderr)
        sys.exit(1)
    if pdf_file.stat().st_size > 50 * 1024 * 1024:
        print("File too large (>50MB).", file=sys.stderr)
        sys.exit(1)

    print(f"Extracting text from PDF: {pdf_file.name}")
    text = extract_text(str(pdf_file))

    if not text or len(text.strip()) < 50:
        print("Warning: extracted very little text from the PDF.", file=sys.stderr)

    return text.strip()


# ---------------------------------------------------------------------------
# Supawork AI Scholar API call
# ---------------------------------------------------------------------------

def _build_summarize_prompt(text: str) -> str:
    """Build a summarization prompt."""
    return (
        "Please provide a comprehensive summary of the following academic paper. "
        "Include: (1) the main research question, (2) methodology, (3) key findings, "
        "and (4) conclusions. Format the output in markdown.\n\n"
        f"Content:\n{text}"
    )


def _build_translate_prompt(text: str, language: str) -> str:
    """Build a translation prompt matching the extension's template."""
    lang_name = LANGUAGES.get(language, language)
    return (
        f"Translate the content I provide into {lang_name} in full, "
        "return it to me hierarchically in .md format. "
        "If the translated content is obviously garbled and unreadable, "
        "you need to organize it for normal reading. "
        "Do not comment, summarize, omit, delete, or output any non-translated content.\n\n"
        f"Content:\n{text}"
    )


def call_supawork_api(prompt: str, debug: bool = False) -> str:
    """Call the Supawork AI Scholar chat endpoint."""
    print("Requesting from Supawork AI Scholar...")

    resp = requests.post(
        SUPAWORK_API_URL,
        json={"prompt": prompt, "stream": False},
        headers={"Content-Type": "application/json;charset=UTF-8"},
        timeout=90,
        proxies=_get_proxy(),
    )

    if debug:
        print(f"Supawork API response status: {resp.status_code}")
        try:
            data = resp.json()
            # Truncate data field for debug output
            if isinstance(data, dict) and "data" in data and len(str(data["data"])) > 500:
                debug_data = {**data, "data": str(data["data"])[:500] + "..."}
                print(json.dumps(debug_data, indent=2, ensure_ascii=False))
            else:
                print(json.dumps(data, indent=2, ensure_ascii=False))
        except Exception:
            print(resp.text[:1000])

    if resp.status_code == 429:
        print("Error: rate limited by Supawork API. Try again later.", file=sys.stderr)
        sys.exit(1)

    if not resp.ok:
        print(f"API error (HTTP {resp.status_code}): {resp.text[:500]}", file=sys.stderr)
        sys.exit(1)

    data = resp.json()
    code = data.get("code")

    if code not in (100000, 200):
        msg = data.get("message", "Unknown error")
        print(f"API error (code {code}): {msg}", file=sys.stderr)
        sys.exit(1)

    result = data.get("data", "")
    if not result:
        print("Error: no result received from server.", file=sys.stderr)
        sys.exit(1)

    # data may be a dict with a "text" key or a plain string
    if isinstance(result, dict):
        result = result.get("text", "") or json.dumps(result, ensure_ascii=False)
    # data may be a Python repr string of a dict (e.g. "{'text': '...'}")
    if isinstance(result, str) and result.startswith("{'text':"):
        try:
            import ast
            parsed = ast.literal_eval(result)
            if isinstance(parsed, dict):
                result = parsed.get("text", result)
        except (ValueError, SyntaxError):
            pass

    return result


# ---------------------------------------------------------------------------
# Markdown conversion
# ---------------------------------------------------------------------------

def to_markdown(result: str, url: str, mode: str, page_title: str | None = None) -> str:
    lines = []

    title = page_title or "Untitled Paper"
    lines.append(f"# {title}")
    lines.append("")
    lines.append(f"**Source:** {url}")
    lines.append(f"**Mode:** {mode}")
    lines.append("")
    lines.append("---")
    lines.append("")

    # The result is already markdown from the API
    lines.append(result.strip())
    lines.append("")
    lines.append("---")
    lines.append(f"*Generated by Supawork AI Scholar on {datetime.now().strftime('%Y-%m-%d')}*")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def slugify(text: str) -> str:
    slug = text.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = slug.strip("-")
    return slug[:80] if slug else "summary"


def _is_local_file(input_str: str) -> bool:
    if input_str.startswith(("http://", "https://")):
        return False
    return Path(input_str).expanduser().exists()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Summarize and translate academic papers using Supawork AI Scholar"
    )
    parser.add_argument(
        "input",
        nargs="?",
        help="URL of the paper or path to a local PDF",
    )
    parser.add_argument(
        "--output", "-o",
        help="Output file path (default: auto-generated from title)",
    )
    parser.add_argument(
        "--translate", "-t",
        choices=list(LANGUAGES.keys()),
        default=None,
        help="Translate instead of summarize (specify target language)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Dump raw API response details",
    )
    proxy_group = parser.add_mutually_exclusive_group()
    proxy_group.add_argument("--proxy", dest="use_proxy", action="store_true", default=None,
                             help="Force proxy usage (overrides ~/.scholar-proxies.json)")
    proxy_group.add_argument("--no-proxy", dest="use_proxy", action="store_false",
                             help="Disable proxy (overrides ~/.scholar-proxies.json)")
    args = parser.parse_args()

    global _PROXY_FORCE
    _PROXY_FORCE = args.use_proxy

    if not args.input:
        parser.error("Please provide a URL or PDF file path")

    check_rate_limit()

    # Extract content
    page_title = None
    if _is_local_file(args.input):
        content = _extract_text_from_pdf(args.input)
        url = f"file://{Path(args.input).resolve()}"
    else:
        content, page_title = _extract_text_from_url(args.input)
        url = args.input

    # Build prompt based on mode
    if args.translate:
        prompt = _build_translate_prompt(content, args.translate)
        mode = f"translate → {args.translate}"
    else:
        prompt = _build_summarize_prompt(content)
        mode = "summarize"

    # Call Supawork API
    result = call_supawork_api(prompt, debug=args.debug)
    record_request()

    # Convert to Markdown
    md = to_markdown(result, url, mode, page_title=page_title)

    # Write output
    if args.output:
        out_path = Path(args.output)
    else:
        output_dir = Path(__file__).parent / "output"
        output_dir.mkdir(exist_ok=True)
        slug = slugify(page_title or "summary")
        out_path = output_dir / f"{slug}.md"

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(md, encoding="utf-8")
    print(f"Output saved to: {out_path}")


if __name__ == "__main__":
    main()
