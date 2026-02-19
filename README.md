# Supawork AI Scholar CLI

CLI reimplementation of the [Supawork AI Scholar Helper](https://chromewebstore.google.com/detail/supawork-ai-scholar-helpe/nfkbblgbfkmbidomjccaejkaohlppbhp) Chrome extension. Summarizes and translates academic papers using the Supawork backend.

## How it works

1. **Text extraction**: Uses Playwright (stealth mode) to load web pages and extract text. For arXiv URLs, rewrites to PDF URL and downloads directly. Local PDFs extracted via pdfminer.six.
2. **API call**: Sends text to the Supawork AI Scholar endpoint (`POST /headshot/api/media/ext/gpt/chat`). No API key or authentication required. The backend proxies to a GPT model server-side.
3. **Two modes**: Summarize (default) or translate to 12+ languages.
4. **Output**: Saves a Markdown file with title, source URL, result, and date.

## Setup

```bash
pip install -r requirements.txt
python -m playwright install chromium
```

No API key needed. The Supawork backend is publicly accessible.

## Usage

### Summarize

```bash
python summarize.py <url_or_pdf>
python summarize.py <url_or_pdf> --output summary.md
```

### Translate

```bash
python summarize.py <url_or_pdf> --translate chinese
python summarize.py <url_or_pdf> --translate french
python summarize.py <url_or_pdf> --translate german
```

### Other flags

```bash
python summarize.py --debug                           # dump raw API response
python summarize.py --proxy / --no-proxy              # proxy control
```

### Supported languages

chinese, english, spanish, french, german, japanese, korean, portuguese, russian, arabic, hindi, italian

## Known limitations

- **Backend dependency**: Relies on the Supawork AI backend at `supawork.ai`. If the service goes offline or the URL changes, the CLI will break.
- **No model control**: The LLM model is server-side (likely GPT) and not configurable.
- **Timeout**: The backend has a 90-second timeout. Very long papers may time out.
- **Rate limiting**: Local rate limiter enforces 10 requests/hour and 5s minimum interval.

## Proxy support

Reads proxy configuration from `~/.scholar-proxies.json`:

```json
{
  "enabled": true,
  "proxies": ["http://proxy1:8080", "http://proxy2:8080"]
}
```

Override with `--proxy` or `--no-proxy` flags.
