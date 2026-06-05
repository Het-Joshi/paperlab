# paperlab

A local research workbench for reviewing, fact-checking, and pressure-testing papers — built for any and all fields. It runs on a 16 GB laptop, keeps the model under 10 GB, and does all of its analysis on your own machine. (You can try playing with smaller models if your system specs are lower)

---

## What this is — and what it is not

**paperlab is an aid, not a reviewer.** It speeds up the mechanical parts of
engaging with a paper — surfacing candidate methodology issues, ethics concerns,
related work, and checkable claims, so you spend your attention where it
matters. It does not, and cannot, replace reading and judgment.

- **Read the paper in full first.** The developers strongly recommend that
  reviewers read a paper in its entirety before using paperlab. Every output
  here is a *flag to look closer*, never a verdict to defer to. The
  accept / weak-accept / reject banner is a calibration gut-check from a small
  local model — treat it as a prompt to reflect on your own reasoning, not as a
  program-committee outcome.
- **For authors too.** Run paperlab on your own draft before submitting. The
  same critiques that help a reviewer triage will help you find your weak
  points — unsupported claims, a confound a referee will catch, a thin ethics
  section, an unclear contribution — while you can still fix them.
- **It is small-model output.** An 8B-class model flags issues and can be wrong
  or shallow. Verify everything against the actual paper.

## Privacy

The paper, the language model, and all analysis run **entirely on your
machine** through Ollama. The PDF and its full text never leave your computer.

The *only* outbound network traffic comes from features you explicitly trigger:
the arXiv tab, the Web tab, and Fact-check send short **search query strings**
to search engines through your own self-hosted SearXNG instance (for
fact-check, those queries are brief claim phrases extracted from the paper). If
you never open those tabs, nothing about the paper leaves the machine at all.
Chats, the corpus, and cached text are stored locally under `~/.paperlab`.

---

## Requirements

- Linux or macOS (Windows via WSL2)
- Python 3.10+
- ~6–10 GB free disk for the model weights
- 16 GB RAM recommended (for a full paper in a 32k context)
- Optional, only for Web search / Fact-check: **Podman** (preferred) or Docker

---

## Setup

### 1. Install Ollama, models, and Python deps

```bash
bash setup.sh
source .venv/bin/activate
```

This installs Ollama, pulls `qwen3:8b` (~5 GB) and `nomic-embed-text` (~270 MB),
creates a virtualenv, and installs the Python dependencies
(`pymupdf4llm`, `requests`, `fastapi`, `uvicorn`, `python-multipart`).

### 2. (Optional) Web search via SearXNG

Needed only for the **Web** tab and **Fact-check**. Use Podman — it's daemonless,
so there's no background service to run at boot.

```bash
sudo apt install -y podman          # or: brew install podman

# config: a real secret_key, the limiter OFF (it 500s otherwise when hit
# directly), and the JSON API ON (off by default, paperlab needs it)
mkdir -p searxng
cat > searxng/settings.yml <<CFG
use_default_settings: true
server:
  secret_key: "$(openssl rand -hex 32)"
  limiter: false
  image_proxy: true
search:
  formats:
    - html
    - json
CFG

podman run -d --name searxng -p 8080:8080 \
  -v "$PWD/searxng:/etc/searxng" \
  docker.io/searxng/searxng

# verify the JSON API actually returns results:
curl -s 'http://localhost:8080/search?q=test&format=json' | head -c 200
```

If `curl` returns JSON, you're set. If it 500s, confirm your config loaded:
`podman exec searxng cat /etc/searxng/settings.yml` should show `limiter: false`.

### 3. Run it

```bash
./lab.sh up          # start Ollama only (review, brainstorm, corpus)
./lab.sh up-web      # start Ollama + SearXNG (adds Web search + Fact-check)
python server.py     # opens http://127.0.0.1:8000
# when done:
./lab.sh down        # stop everything
```

`server.py` binds to `127.0.0.1` only. `lab.sh` brings services up on demand and
leaves them off at boot, so nothing idles in the background.

### 4. Keep services off at startup (one time)

Ollama and Docker install as always-on services. To stop them auto-starting
(Podman needs nothing — it has no daemon):

```bash
# Linux
sudo systemctl disable --now ollama
# macOS: System Settings -> Login Items -> remove Ollama
```

---

## Using it

1. **Upload** a PDF (drag onto the sidebar or click **+ Upload PDF**) and click
   it to view in the native PDF pane.
2. **Review** tab: run one persona or **Run all**. With Novelty included it
   fetches related work and lists every source. After Run all it produces an
   **overall recommendation**.
3. **Fact-check claims**: extracts the paper's checkable claims, searches each,
   and shows supported / contradicted / unclear with source links.
4. **Brainstorm**: chat with the whole paper in context; history is saved.
5. **Corpus / Web / arXiv**: search your own paper collection, the web, or arXiv.
6. Toggle **show model thinking** in the sidebar to reveal the model's reasoning.

---

## Features

- **Reviewer personas** — Methodology (confounds, sampling/vantage bias, ground
  truth, confirmed-vs-suspected), Ethics (IRB, disclosure, harm, PII, consent),
  Novelty (contribution + delta, grounded in fetched related work), Scoping
  (one-sentence contribution, what to cut). Each quotes the passage it reacts to.
- **Overall decision** — synthesizes the four critiques into accept / weak
  accept / weak reject / reject, with a 1–5 score, confidence, and a for/against
  summary.
- **Fact-check** — claim extraction → web search → verdict + links. Deterministic
  pipeline; the model only extracts and judges.
- **Websites & sources fetched** — every link pulled during a review is listed
  with its origin (arXiv / Exa / corpus).
- **Brainstorm chat** — persistent per-paper history under `~/.paperlab/chats`.
- **Corpus search** — embed papers (`nomic-embed-text`) into plain SQLite, search
  semantically. No vector-DB server.
- **Web (SearXNG)** and **arXiv** search.
- **Rich rendering** — markdown, code, tables, and LaTeX math (`$x_i$`, `$$...$$`)
  via KaTeX. Math and markdown are decoupled, so neither can break the other.
- **Thinking** — model reasoning captured into a collapsible block, hidden by
  default, stripped from saved context.

## CLI

```bash
python paperlab.py review paper.pdf [--persona methodology]
python paperlab.py factcheck paper.pdf
python paperlab.py chat paper.pdf
python paperlab.py add related.pdf
python paperlab.py search "ACR fingerprinting"
python paperlab.py web "smart TV data collection"
python paperlab.py arxiv "smart TV ACR privacy"
```

## Configuration (environment variables)

| var | default | purpose |
|---|---|---|
| `PAPERLAB_MODEL` | `gemma4:latest` | reasoning model for stronger critique + vision + 128k context (tighter on RAM) |
| `PAPERLAB_EMBED` | `nomic-embed-text` | embedding model |
| `PAPERLAB_CTX` | `32768` | context window — **do not lower** or papers get truncated |
| `PAPERLAB_DIR` | `~/.paperlab` | where papers, cache, corpus, and chats live |
| `SEARXNG_URL` | `http://localhost:8080` | your SearXNG instance |
| `EXA_API_KEY` | — | optional semantic search for the Sources panel |
| `OLLAMA_HOST` | `http://localhost:11434` | Ollama endpoint |

## Design notes

Your Python orchestrates; the local model only does narrow, grounded text
tasks — small models are unreliable at agentic tool-calling, so paperlab never
asks the model to decide what to do. Single-paper work loads the whole paper
into a 32k context (no RAG; chunking hurts review quality). RAG is used only for
cross-paper corpus search.

## Troubleshooting

- **`404 ... /api/chat` / "Model not found"** — the model isn't pulled.
  `ollama list`, then `ollama pull <PAPERLAB_MODEL>`.
- **SearXNG 500, log says `X-Forwarded-For nor X-Real-IP`** — the limiter is on.
  Ensure your `settings.yml` has `limiter: false` and is actually mounted
  (`podman exec searxng cat /etc/searxng/settings.yml`).
- **Podman `unauthorized: incorrect username or password`** — stale Docker Hub
  creds. `podman logout --all` and move aside `~/.docker/config.json`.
- **`docker.service` won't start** — use Podman instead; it's daemonless and
  avoids the containerd dependency entirely.
- **Markdown shows as raw `**`/`#`** — hard-refresh (Ctrl-Shift-R); if it
  persists, check the browser console for a failed CDN load.
- **"Run all" is slow** — it's four sequential full-context generations on a
  local model. Run one persona at a time, or use a smaller/faster model.

## Files

- `paperlab.py` — core logic + CLI
- `server.py` — FastAPI app (endpoints, streaming)
- `index.html` — the entire UI in one file
- `lab.sh` — on-demand service launcher
- `setup.sh` — installer

---

## In short

paperlab exists to make you faster, not to think for you. Read the paper, form
your own view, and let the tool help you check it, find what you missed, and
catch your own weak points before someone else does, all without your draft
ever leaving your machine.
