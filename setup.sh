#!/usr/bin/env bash
set -e

# 1. Ollama (skip if installed). macOS alt: brew install ollama
if ! command -v ollama >/dev/null 2>&1; then
  echo ">> installing Ollama..."
  curl -fsSL https://ollama.com/install.sh | sh
fi

# 2. Models (~5GB + ~270MB). Needs `ollama serve` running.
echo ">> pulling models..."
ollama pull gemma4:latest
ollama pull nomic-embed-text

# 3. Python env + deps.
echo ">> python env..."
python3 -m venv .venv
. .venv/bin/activate
pip install --upgrade pip
pip install pymupdf4llm requests fastapi "uvicorn[standard]" python-multipart

echo ""
echo "Done."
echo "  source .venv/bin/activate"
echo "  python server.py          # UI at http://127.0.0.1:8000"
echo ""
echo "For web search / fact-check, also run SearXNG (free, no key, no rate limit):"
echo "  docker run -d --name searxng -p 8080:8080 \\"
echo "    -e SEARXNG_SETTINGS_PATH=/etc/searxng/settings.yml searxng/searxng"
echo "  # then enable JSON: add 'json' under search.formats in settings.yml and restart."
echo "  # point paperlab at it if not on :8080 ->  export SEARXNG_URL=http://localhost:8080"
