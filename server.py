#!/usr/bin/env python3
"""
paperlab web UI — a single FastAPI process serving one HTML page.
No node, no build step, no node_modules. Reuses all of paperlab.py.

Run:  python server.py     (opens http://127.0.0.1:8000)
"""
import json
import os
import webbrowser

import requests
from fastapi import FastAPI, UploadFile, File, Body
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse

from paperlab import (
    extract, load_paper, add_to_corpus, corpus_search, arxiv_search,
    exa_search, web_search, factcheck, llm, PERSONAS, MODEL, NUM_CTX,
    OLLAMA, SEARXNG, HOME,
)

PAPERS = os.path.join(HOME, "papers")
CHATS = os.path.join(HOME, "chats")
os.makedirs(PAPERS, exist_ok=True)
os.makedirs(CHATS, exist_ok=True)
HERE = os.path.dirname(os.path.abspath(__file__))


def chat_file(name):
    import hashlib
    h = hashlib.md5(os.path.basename(name).encode()).hexdigest()
    return os.path.join(CHATS, h + ".json")

app = FastAPI()


def safe_path(name):
    """Prevent path traversal; only files inside PAPERS are reachable."""
    p = os.path.join(PAPERS, os.path.basename(name))
    return p


def ollama_stream(messages, temperature=0.2):
    with requests.post(
        f"{OLLAMA}/api/chat",
        json={"model": MODEL, "messages": messages, "stream": True,
              "options": {"num_ctx": NUM_CTX, "temperature": temperature}},
        stream=True, timeout=1800,
    ) as r:
        if r.status_code == 404:
            yield (f"\u26a0 Model '{MODEL}' is not installed in Ollama. "
                   f"Pull it first:  ollama pull {MODEL}")
            return
        opened = closed = False
        for line in r.iter_lines():
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "error" in obj:
                yield f"\u26a0 {obj['error']}"
                return
            msg = obj.get("message", {})
            th = msg.get("thinking")
            if th:
                if not opened:
                    yield "<think>"; opened = True
                yield th
            ct = msg.get("content", "")
            if ct:
                if opened and not closed:
                    yield "</think>"; closed = True
                yield ct
        if opened and not closed:
            yield "</think>"


# --------------------------------------------------------------------------- routes
@app.get("/")
def index():
    return FileResponse(os.path.join(HERE, "index.html"))


@app.get("/papers")
def list_papers():
    files = sorted(f for f in os.listdir(PAPERS) if f.lower().endswith(".pdf"))
    return files


@app.post("/upload")
async def upload(file: UploadFile = File(...)):
    dest = safe_path(file.filename)
    with open(dest, "wb") as f:
        f.write(await file.read())
    return {"name": os.path.basename(dest)}


@app.get("/pdf/{name}")
def pdf(name: str):
    return FileResponse(safe_path(name), media_type="application/pdf")


@app.post("/review/sources")
def review_sources(payload: dict = Body(...)):
    """Derive a query from the paper and fetch related work. Returns every link
    so the UI can show exactly what was fetched during the review."""
    paper = load_paper(safe_path(payload["name"]))
    q = llm(
        "From this paper excerpt, output ONLY 4-6 comma-separated search keywords "
        "for finding related academic work. No other text, no explanation.\n\n"
        + paper[:2500],
        num_ctx=4096,
    ).strip().splitlines()[0][:120]

    web = []
    try:
        for t, s, u in arxiv_search(q, k=6):
            web.append({"title": t, "url": u, "summary": s[:300], "via": "arxiv"})
    except Exception:
        pass
    exa = exa_search(q, k=6)
    if exa:
        for t, s, u in exa:
            web.append({"title": t, "url": u, "summary": s[:300], "via": "exa"})

    corpus = [{"title": t, "text": x[:300], "score": round(sc, 3)}
              for sc, t, x in corpus_search(paper[:2000], k=5)]
    return {"query": q, "web": web, "corpus": corpus}


@app.post("/review")
def review(payload: dict = Body(...)):
    name, persona = payload["name"], payload["persona"]
    sources = payload.get("sources") or {}
    paper = load_paper(safe_path(name))
    system, task = PERSONAS[persona]
    context = ""
    if persona == "novelty":
        lines = []
        for s in sources.get("web", []):
            lines.append(f"- [{s['via']}] {s['title']}: {s['summary']}")
        for c in sources.get("corpus", []):
            lines.append(f"- [corpus] {c['title']}: {c['text']}")
        if not lines:  # fallback if UI didn't pass sources
            lines = [f"- [corpus] {t}: {x[:300]}"
                     for _, t, x in corpus_search(paper[:2000], k=5)]
        context = ("\n\nPRIOR-WORK CONTEXT (fetched during this review):\n"
                   + "\n".join(lines)) if lines else \
                  "\n\nPRIOR-WORK CONTEXT: (none found — add related papers / set EXA_API_KEY)"
    prompt = f"PAPER:\n{paper}{context}\n\nTASK:\n{task}"
    msgs = [{"role": "system", "content": system},
            {"role": "user", "content": prompt}]
    return StreamingResponse(ollama_stream(msgs), media_type="text/plain")


@app.post("/chat")
def chat(payload: dict = Body(...)):
    name = payload["name"]
    history = payload.get("history", [])
    message = payload["message"]
    paper = load_paper(safe_path(name))
    msgs = [
        {"role": "system", "content":
         "You are a sharp research collaborator pressure-testing an internet-"
         "measurement paper. Be concrete and skeptical; ground claims in the paper "
         "text. Distinguish confirmed from suspected findings."},
        {"role": "user", "content": f"Here is the paper:\n\n{paper}"},
        {"role": "assistant", "content": "Read it. What do you want to dig into?"},
    ] + history + [{"role": "user", "content": message}]
    return StreamingResponse(ollama_stream(msgs, temperature=0.4),
                             media_type="text/plain")


@app.post("/corpus/add")
def corpus_add(payload: dict = Body(...)):
    name = payload["name"]
    add_to_corpus(safe_path(name), title=name)
    return {"ok": True}


@app.get("/corpus/search")
def corpus_search_route(q: str):
    return [{"score": round(s, 3), "title": t, "text": x[:400]}
            for s, t, x in corpus_search(q)]


@app.get("/arxiv")
def arxiv_route(q: str):
    try:
        return [{"title": t, "summary": s, "url": u} for t, s, u in arxiv_search(q)]
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=502)


@app.get("/chat/history")
def chat_history(name: str):
    f = chat_file(name)
    return json.load(open(f, encoding="utf-8")) if os.path.exists(f) else []


@app.get("/websearch")
def websearch_route(q: str):
    """Free general web search via SearXNG."""
    try:
        return [{"title": t, "summary": s, "url": u} for t, s, u in web_search(q)]
    except requests.exceptions.RequestException:
        return JSONResponse(
            {"error": f"SearXNG not reachable at {SEARXNG}. Start it (see README) "
                      "or set SEARXNG_URL."}, status_code=502)


@app.post("/factcheck")
def factcheck_route(payload: dict = Body(...)):
    name = payload["name"]
    path = safe_path(name)

    def gen():
        try:
            for claim, verdict, sources in factcheck(path):
                yield json.dumps({
                    "claim": claim, "verdict": verdict,
                    "sources": [{"title": t, "url": u} for t, u in sources],
                }) + "\n"
        except RuntimeError as e:
            yield json.dumps({"error": str(e)}) + "\n"

    return StreamingResponse(gen(), media_type="application/x-ndjson")


@app.post("/chat/save")
def chat_save(payload: dict = Body(...)):
    with open(chat_file(payload["name"]), "w", encoding="utf-8") as f:
        json.dump(payload.get("history", []), f)
    return {"ok": True}


@app.post("/decision")
def decision(payload: dict = Body(...)):
    paper = load_paper(safe_path(payload["name"]))
    reviews = payload.get("reviews") or {}
    rv = "\n\n".join(f"## {k} review\n{v.strip()}" for k, v in reviews.items() if v.strip())
    basis = rv or "(no per-aspect reviews supplied — judge from the paper directly)"
    system = ("You are the program-committee meta-reviewer for a top internet-"
              "measurement venue (IMC). You weigh novelty, methodology soundness, "
              "ethics, and clarity, and you are calibrated and decisive — most "
              "submissions are not accepts.")
    task = ("Give an overall recommendation for this paper. Reply in EXACTLY this "
            "format, nothing before or after:\n"
            "RECOMMENDATION: accept | weak accept | weak reject | reject\n"
            "SCORE: <integer 1-5, 5=clear accept>\n"
            "CONFIDENCE: low | medium | high\n"
            "SUMMARY: 2-3 sentences naming the strongest reason for and against.\n\n"
            f"ASPECT REVIEWS:\n{basis}")
    prompt = f"PAPER (excerpt):\n{paper[:20000]}\n\n{task}"
    msgs = [{"role": "system", "content": system},
            {"role": "user", "content": prompt}]
    return StreamingResponse(ollama_stream(msgs, temperature=0.2), media_type="text/plain")


if __name__ == "__main__":
    import uvicorn
    webbrowser.open("http://127.0.0.1:8000")
    uvicorn.run(app, host="127.0.0.1", port=8000)
