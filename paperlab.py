#!/usr/bin/env python3
"""
paperlab — a tiny, fully-local paper review & brainstorm tool for research work.

Design notes:
  * Python orchestrates; the local model only does narrow text generation.
    (Small models are unreliable at agentic tool-calling, so we don't ask them to.)
  * Single-paper review loads the WHOLE paper into a 32k context. No RAG for one paper.
  * RAG/corpus search is only for cross-paper novelty / related-work discovery.
  * Every critique persona is narrow + checklist-driven + must quote evidence.

Requires: Ollama running locally with `qwen3:8b` and `nomic-embed-text` pulled.
Optional web: free arXiv (no key) by default; set EXA_API_KEY for semantic search.
"""

import argparse
import json
import os
import re
import sqlite3
import sys
import textwrap
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

import requests

# ----------------------------------------------------------------------------- config
OLLAMA = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
MODEL = os.environ.get("PAPERLAB_MODEL", "gemma4:latest")
EMBED_MODEL = os.environ.get("PAPERLAB_EMBED", "nomic-embed-text")
NUM_CTX = int(os.environ.get("PAPERLAB_CTX", "32768"))  # <-- the truncation footgun fix
HOME = os.path.expanduser(os.environ.get("PAPERLAB_DIR", "~/.paperlab"))
SEARXNG = os.environ.get("SEARXNG_URL", "http://localhost:8080")  # free, no-key, no-limit
DB = os.path.join(HOME, "corpus.db")
CACHE = os.path.join(HOME, "cache")
MAX_PAPER_CHARS = 90_000  # ~26k tokens, leaves room for the critique in a 32k window

os.makedirs(CACHE, exist_ok=True)

# ----------------------------------------------------------------------------- model io
def llm(prompt, system="", temperature=0.2, num_ctx=NUM_CTX):
    try:
        r = requests.post(
            f"{OLLAMA}/api/chat",
            json={
                "model": MODEL,
                "messages": (
                    ([{"role": "system", "content": system}] if system else [])
                    + [{"role": "user", "content": prompt}]
                ),
                "stream": False,
                "options": {"num_ctx": num_ctx, "temperature": temperature},
            },
            timeout=900,
        )
        r.raise_for_status()
        content = r.json()["message"]["content"]
        # newer Ollama returns thinking separately; older embeds <think>..</think>
        content = re.sub(r"<think>[\s\S]*?</think>", "", content)
        return content.strip()
    except requests.exceptions.ConnectionError:
        raise RuntimeError(f"Can't reach Ollama at {OLLAMA}. Start it with ./lab.sh up")
    except requests.exceptions.HTTPError as e:
        if e.response is not None and e.response.status_code == 404:
            raise RuntimeError(
                f"Model '{MODEL}' is not installed in Ollama. Pull it first:  "
                f"ollama pull {MODEL}")
        raise


def embed(text):
    r = requests.post(
        f"{OLLAMA}/api/embed", json={"model": EMBED_MODEL, "input": text}, timeout=120
    )
    r.raise_for_status()
    return r.json()["embeddings"][0]


def cosine(a, b):
    import math
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb + 1e-9)


# ----------------------------------------------------------------------------- pdf
def extract(path):
    """PDF -> markdown, cached. Light + CPU-only via PyMuPDF4LLM."""
    import hashlib
    key = hashlib.md5(os.path.abspath(path).encode()).hexdigest()
    cached = os.path.join(CACHE, key + ".md")
    if os.path.exists(cached):
        return open(cached, encoding="utf-8").read()
    import pymupdf4llm
    md = pymupdf4llm.to_markdown(path)
    open(cached, "w", encoding="utf-8").write(md)
    return md


def load_paper(path):
    md = extract(path)
    if len(md) > MAX_PAPER_CHARS:
        print(f"  [note] paper is large ({len(md)} chars); truncating to "
              f"{MAX_PAPER_CHARS} to fit context. Long appendices may be cut.",
              file=sys.stderr)
        md = md[:MAX_PAPER_CHARS]
    return md


# ----------------------------------------------------------------------------- corpus db
def db():
    conn = sqlite3.connect(DB)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS chunks "
        "(paper TEXT, title TEXT, idx INT, text TEXT, emb TEXT)"
    )
    return conn


def add_to_corpus(path, title=None):
    md = extract(path)
    title = title or os.path.basename(path)
    # crude paragraph-ish chunking ~1500 chars; fine for a personal corpus
    chunks, buf = [], ""
    for para in md.split("\n\n"):
        if len(buf) + len(para) > 1500:
            if buf.strip():
                chunks.append(buf.strip())
            buf = para
        else:
            buf += "\n\n" + para
    if buf.strip():
        chunks.append(buf.strip())

    conn = db()
    conn.execute("DELETE FROM chunks WHERE paper=?", (os.path.abspath(path),))
    for i, ch in enumerate(chunks):
        e = embed(ch)
        conn.execute(
            "INSERT INTO chunks VALUES (?,?,?,?,?)",
            (os.path.abspath(path), title, i, ch, json.dumps(e)),
        )
    conn.commit()
    conn.close()
    print(f"Added '{title}' ({len(chunks)} chunks).")


def corpus_search(query, k=5):
    qe = embed(query)
    conn = db()
    rows = conn.execute("SELECT title, text, emb FROM chunks").fetchall()
    conn.close()
    if not rows:
        return []
    scored = [(cosine(qe, json.loads(emb)), title, text) for title, text, emb in rows]
    scored.sort(reverse=True)
    return scored[:k]


# ----------------------------------------------------------------------------- web (not local)
def arxiv_search(query, k=6):
    """Free, no API key. arXiv asks for a descriptive User-Agent and throttles
    unidentified clients (the cause of read timeouts). Returns (title, summary, url)."""
    url = ("https://export.arxiv.org/api/query"
           f"?search_query=all:{urllib.parse.quote(query)}"
           f"&start=0&max_results={k}")
    last = None
    for _ in range(2):  # one retry; arXiv can be transiently slow
        try:
            r = requests.get(url, headers={"User-Agent": "paperlab/1.0 (research aid)"},
                             timeout=20)
            r.raise_for_status()
            ns = {"a": "http://www.w3.org/2005/Atom"}
            out = []
            for e in ET.fromstring(r.content).findall("a:entry", ns):
                out.append((
                    (e.find("a:title", ns).text or "").strip().replace("\n", " "),
                    (e.find("a:summary", ns).text or "").strip().replace("\n", " ")[:400],
                    (e.find("a:id", ns).text or "").strip(),
                ))
            return out
        except requests.exceptions.RequestException as e:
            last = e
    raise last


def web_search(query, k=6, engines=None):
    """General web search via self-hosted SearXNG. Free, no key, no rate limit.
    Pass engines='arxiv' (etc.) to restrict to one engine. Returns
    (title, snippet, url). Raises on connection error."""
    params = {"q": query, "format": "json"}
    if engines:
        params["engines"] = engines
    r = requests.get(
        f"{SEARXNG}/search",
        params=params,
        headers={"User-Agent": "paperlab/1.0"},
        timeout=30,
    )
    r.raise_for_status()
    out = []
    for it in r.json().get("results", [])[:k]:
        out.append((it.get("title", ""), (it.get("content") or "")[:400], it.get("url", "")))
    return out


def exa_search(query, k=6):
    key = os.environ.get("EXA_API_KEY")
    if not key:
        return None
    r = requests.post(
        "https://api.exa.ai/search",
        headers={"x-api-key": key, "Content-Type": "application/json"},
        json={"query": query, "numResults": k, "contents": {"text": {"maxCharacters": 400}}},
        timeout=60,
    )
    r.raise_for_status()
    return [(d.get("title", ""), (d.get("text") or "")[:400], d.get("url", ""))
            for d in r.json().get("results", [])]


# ----------------------------------------------------------------------------- personas
PERSONAS = {
    "methodology": (
        "You are a skeptical measurement-methodology reviewer for an internet "
        "measurement venue (IMC) and other top venues like USENIX, S&P. You care only about measurement validity.",
        "Find every place where the measurement design could undermine a claim. "
        "Check: confounds, vantage-point / sampling bias, missing ground truth or "
        "baseline, sample size & representativeness, statistical validity, and any "
        "claim stated as CONFIRMED that the evidence only makes SUSPECTED. "
        "For EACH issue output:\n"
        "  - [SEVERITY: high|med|low] one-line problem\n"
        "  - EVIDENCE: a short quote from the paper it refers to\n"
        "  - FIX: what would resolve it\n"
        "If you cannot quote supporting text, do not raise the issue."
    ),
    "ethics": (
        "You are the ethics reviewer for an internet measurement venue. Active "
        "measurement and privacy work is judged hard on ethics.",
        "Check ONLY for: IRB / ethics-board review, responsible disclosure, harm to "
        "networks / systems / third parties, PII and data handling, and consent. "
        "For each concern output [SEVERITY], the EVIDENCE quote, and a FIX. "
        "If the paper already addresses a point well, say so briefly."
    ),
    "novelty": (
        "You are a related-work reviewer. You assess whether the contribution's "
        "delta over prior work is clearly established.",
        "Using ONLY the paper plus the PRIOR-WORK CONTEXT provided, judge: (1) what "
        "is the single concrete contribution, (2) which prior work is closest, "
        "(3) whether the delta is clearly stated. Flag any claim of novelty not "
        "supported against the provided prior work. Do NOT assert global novelty — "
        "you only see a slice of the literature."
    ),
    "scoping": (
        "You are a clarity-and-scoping reviewer.",
        "Answer: (1) State the paper's contribution in ONE sentence. (2) Is the scope "
        "coherent, or does it try to do too much? (3) What 1-2 things would you cut? "
        "Be concrete and quote where the scope drifts."
    ),
}


def review(path, which=None):
    paper = load_paper(path)
    names = [which] if which else list(PERSONAS)
    for name in names:
        if name not in PERSONAS:
            print(f"unknown persona: {name}", file=sys.stderr)
            continue
        system, task = PERSONAS[name]
        context = ""
        if name == "novelty":
            hits = corpus_search(paper[:2000], k=5)
            if hits:
                context = "\n\nPRIOR-WORK CONTEXT (from your corpus):\n" + "\n".join(
                    f"- {t}: {txt[:300]}" for _, t, txt in hits
                )
            else:
                context = ("\n\nPRIOR-WORK CONTEXT: (empty — add related papers with "
                           "`paperlab add`)")
        prompt = f"PAPER:\n{paper}{context}\n\nTASK:\n{task}"
        print(f"\n{'='*70}\n  {name.upper()} REVIEW\n{'='*70}")
        print(llm(prompt, system=system))


# ----------------------------------------------------------------------------- brainstorm REPL
def chat(path):
    paper = load_paper(path)
    print("Loaded paper. Brainstorm mode — ask anything, 'exit' to quit.\n")
    messages = [
        {"role": "system", "content":
         "You are a sharp research collaborator helping brainstorm and pressure-test "
         "an internet-measurement paper. Be concrete and skeptical; ground claims in "
         "the paper text. Distinguish confirmed from suspected findings."},
        {"role": "user", "content": f"Here is the paper I'm working on:\n\n{paper}"},
        {"role": "assistant", "content": "Got it — I've read it. What do you want to dig into?"},
    ]
    while True:
        try:
            q = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if q.lower() in {"exit", "quit"}:
            break
        if not q:
            continue
        messages.append({"role": "user", "content": q})
        r = requests.post(
            f"{OLLAMA}/api/chat",
            json={"model": MODEL, "messages": messages, "stream": False,
                  "options": {"num_ctx": NUM_CTX, "temperature": 0.4}},
            timeout=900,
        ).json()["message"]["content"]
        print(f"\n{r}\n")
        messages.append({"role": "assistant", "content": r})


# ----------------------------------------------------------------------------- fact-check
def extract_claims(paper, n=5):
    raw = llm(
        f"Extract the {n} most important EMPIRICAL or FACTUAL claims from this paper "
        "that could be verified against outside sources (numbers, comparisons, "
        "attributions, 'X is the first/largest/only'). Output each claim on its own "
        "line as a plain statement. No numbering, no preamble, no other text.\n\n"
        + paper[:8000],
        num_ctx=8192,
    )
    return [c.strip("-*• \t").strip() for c in raw.splitlines() if len(c.strip()) > 15][:n]


def factcheck(path, n=5):
    """Deterministic pipeline: extract claims -> web search each -> judge.
    Yields (claim, verdict_text, [(title, url), ...]). The model only does the
    two narrow tasks (extract, judge); Python drives the search."""
    paper = load_paper(path)
    for claim in extract_claims(paper, n):
        try:
            results = web_search(claim, k=5)
        except requests.exceptions.RequestException:
            yield (claim, "VERDICT: unclear\nWHY: SearXNG unreachable — no evidence gathered.", [])
            continue
        evidence = "\n".join(f"- {t}: {s} ({u})" for t, s, u in results) or "(no results)"
        verdict = llm(
            "Judge the CLAIM using only the EVIDENCE. Reply in EXACTLY this format:\n"
            "VERDICT: supported | contradicted | unclear\n"
            "WHY: one sentence, grounded in the evidence.\n\n"
            f"CLAIM: {claim}\n\nEVIDENCE:\n{evidence}",
            num_ctx=4096,
        )
        yield (claim, verdict, [(t, u) for t, _, u in results])


# ----------------------------------------------------------------------------- cli
def main():
    p = argparse.ArgumentParser(description="Local paper review & brainstorm for research work.")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("ingest", help="extract a PDF to markdown (caches it)")
    s.add_argument("pdf")

    s = sub.add_parser("review", help="run reviewer personas on a paper")
    s.add_argument("pdf")
    s.add_argument("--persona", choices=list(PERSONAS),
                   help="run just one (default: all)")

    s = sub.add_parser("add", help="add a paper to the related-work corpus")
    s.add_argument("pdf")
    s.add_argument("--title")

    s = sub.add_parser("search", help="semantic search across your corpus")
    s.add_argument("query")

    s = sub.add_parser("arxiv", help="free arXiv search (no key)")
    s.add_argument("query")

    s = sub.add_parser("web", help="general web search via SearXNG (free, no key)")
    s.add_argument("query")

    s = sub.add_parser("factcheck", help="extract & fact-check a paper's claims via web search")
    s.add_argument("pdf")

    s = sub.add_parser("chat", help="interactive brainstorm on a loaded paper")
    s.add_argument("pdf")

    a = p.parse_args()

    if a.cmd == "ingest":
        print(extract(a.pdf)[:2000] + "\n...[truncated preview]")
    elif a.cmd == "review":
        review(a.pdf, a.persona)
    elif a.cmd == "add":
        add_to_corpus(a.pdf, a.title)
    elif a.cmd == "search":
        for score, title, text in corpus_search(a.query):
            print(f"\n[{score:.3f}] {title}\n  {text[:300]}")
    elif a.cmd == "arxiv":
        for t, summ, url in arxiv_search(a.query):
            print(f"\n{t}\n  {url}\n  {summ}")
    elif a.cmd == "web":
        try:
            for t, summ, url in web_search(a.query):
                print(f"\n{t}\n  {url}\n  {summ}")
        except requests.exceptions.RequestException:
            sys.exit(f"Can't reach SearXNG at {SEARXNG}. Start it, or set SEARXNG_URL. "
                     "See README for the one-line Docker command.")
    elif a.cmd == "factcheck":
        for claim, verdict, sources in factcheck(a.pdf):
            print(f"\n• {claim}\n  {verdict}")
            for t, u in sources:
                print(f"    - {t} {u}")
    elif a.cmd == "chat":
        chat(a.pdf)


if __name__ == "__main__":
    main()
