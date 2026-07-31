#!/usr/bin/env python3
"""grimoire import_data — pull public-domain texts into the corpus.

Fetches plain-text works from Project Gutenberg (and equivalents) into a local
corpus folder, then optionally folds them straight into the tome via
``grimoire.py ingest``. Uses only the standard library — no extra deps, works on
the deck offline once the texts are downloaded.

Sources:
  * gutenberg   — Project Gutenberg, via the free gutendex.com catalog API
  * wikisource  — English Wikisource page text, via the MediaWiki API
  * standardebooks (listed; see notes)

Examples:
  python import_data.py search "sherlock holmes"        # find Gutenberg books
  python import_data.py get 1661 --ingest               # download PG #1661 + ingest
  python import_data.py popular --count 25 --ingest      # top 25 PG books + ingest
  python import_data.py wikisource "The Art of War" --ingest
  python import_data.py sources                          # list supported sources

Corpus folder defaults to backend/data/corpus/ (gitignored). Re-running skips
files already downloaded, so it's safe to grow the library incrementally.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CORPUS = os.path.join(HERE, "backend", "data", "corpus")
GRIMOIRE = os.path.join(HERE, "backend", "grimoire.py")
UA = {"User-Agent": "grimoire-import/1.0 (cyberdeck offline library)"}

SOURCES = {
    "gutenberg": "Project Gutenberg — 70k+ public-domain books (gutendex.com API)",
    "wikisource": "English Wikisource — transcribed public-domain texts (MediaWiki API)",
    "standardebooks": "Standard Ebooks — polished public-domain editions (see notes)",
}


def _get(url: str, timeout: int = 30) -> bytes:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _slug(text: str, maxlen: int = 80) -> str:
    s = re.sub(r"[^\w\s-]", "", text).strip().lower()
    s = re.sub(r"[\s_-]+", "-", s)
    return s[:maxlen] or "untitled"


def _save(corpus: str, name: str, text: str) -> str | None:
    os.makedirs(corpus, exist_ok=True)
    path = os.path.join(corpus, name)
    if os.path.exists(path):
        print(f"  skip (already have) {name}")
        return None
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"  saved {name}  ({len(text)//1024} KB)")
    return path


# --- Project Gutenberg (via gutendex) --------------------------------------

def gutendex(params: dict) -> dict:
    url = "https://gutendex.com/books?" + urllib.parse.urlencode(params)
    return json.loads(_get(url).decode("utf-8"))


def _pg_text_url(book: dict) -> str | None:
    fmts = book.get("formats", {})
    # Prefer UTF-8 plain text.
    for mime, url in fmts.items():
        if mime.startswith("text/plain") and "utf-8" in mime.lower():
            return url
    for mime, url in fmts.items():
        if mime.startswith("text/plain"):
            return url
    return None


def gutenberg_search(query: str, limit: int = 15):
    data = gutendex({"search": query})
    results = data.get("results", [])[:limit]
    if not results:
        print("no matches.")
        return
    print(f"{'ID':>7}  {'TITLE':50} AUTHOR")
    for b in results:
        authors = ", ".join(a.get("name", "") for a in b.get("authors", [])) or "—"
        title = (b.get("title") or "")[:50]
        print(f"{b.get('id'):>7}  {title:50} {authors[:40]}")
    print("\nDownload one with:  python import_data.py get <ID> --ingest")


def gutenberg_get(book_id: int, corpus: str) -> str | None:
    data = gutendex({"ids": str(book_id)})
    results = data.get("results", [])
    if not results:
        print(f"  Gutenberg #{book_id}: not found.")
        return None
    book = results[0]
    url = _pg_text_url(book)
    if not url:
        print(f"  Gutenberg #{book_id}: no plain-text format available.")
        return None
    title = book.get("title") or f"pg{book_id}"
    print(f"  fetching #{book_id}: {title}")
    try:
        text = _get(url).decode("utf-8", "replace")
    except Exception as e:
        print(f"  download failed: {e}")
        return None
    return _save(corpus, f"pg{book_id}-{_slug(title)}.txt", text)


def gutenberg_popular(count: int, corpus: str) -> list[str]:
    saved = []
    page = 1
    while len(saved) < count:
        data = gutendex({"sort": "popular", "page": str(page)})
        results = data.get("results", [])
        if not results:
            break
        for b in results:
            if len(saved) >= count:
                break
            url = _pg_text_url(b)
            if not url:
                continue
            title = b.get("title") or f"pg{b.get('id')}"
            print(f"  [{len(saved)+1}/{count}] #{b.get('id')}: {title[:60]}")
            try:
                text = _get(url).decode("utf-8", "replace")
            except Exception as e:
                print(f"    download failed: {e}")
                continue
            p = _save(corpus, f"pg{b.get('id')}-{_slug(title)}.txt", text)
            if p:
                saved.append(p)
        page += 1
    return saved


# --- Wikisource ------------------------------------------------------------

def wikisource_get(title: str, corpus: str) -> str | None:
    api = ("https://en.wikisource.org/w/api.php?action=query&prop=extracts"
           "&explaintext=1&format=json&titles=" + urllib.parse.quote(title))
    try:
        data = json.loads(_get(api).decode("utf-8"))
    except Exception as e:
        print(f"  Wikisource fetch failed: {e}")
        return None
    pages = data.get("query", {}).get("pages", {})
    for _, page in pages.items():
        text = page.get("extract", "")
        if text.strip():
            return _save(corpus, f"ws-{_slug(title)}.txt", text)
    print(f"  Wikisource: no text for '{title}'.")
    return None


# --- ingest ----------------------------------------------------------------

def ingest(corpus: str, max_gb: float):
    if not os.path.exists(GRIMOIRE):
        print(f"  (grimoire.py not found at {GRIMOIRE}; skipping ingest)")
        return
    print(f"\nIngesting {corpus} into the tome ...")
    subprocess.run([sys.executable, GRIMOIRE, "ingest", corpus, "--max-gb", str(max_gb)])


# --- CLI -------------------------------------------------------------------

def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    # Shared options usable either before OR after the subcommand.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--corpus", default=DEFAULT_CORPUS, help="download folder")
    common.add_argument("--ingest", action="store_true", help="fold downloads into the tome after fetching")
    common.add_argument("--max-gb", type=float, default=1.0, help="ingest size budget")

    p = argparse.ArgumentParser(prog="import_data", parents=[common],
                                description="Import public-domain texts into grimoire.")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("sources", help="list supported sources", parents=[common])
    s = sub.add_parser("search", help="search Project Gutenberg", parents=[common]); s.add_argument("query", nargs="+")
    s = sub.add_parser("get", help="download a Gutenberg book by id", parents=[common]); s.add_argument("id", type=int)
    s = sub.add_parser("popular", help="download the most popular Gutenberg books", parents=[common]); s.add_argument("--count", type=int, default=20)
    s = sub.add_parser("wikisource", help="download a Wikisource page", parents=[common]); s.add_argument("title", nargs="+")

    a = p.parse_args(argv)

    if a.cmd == "sources":
        for k, v in SOURCES.items():
            print(f"  {k:16} {v}")
        print("\nnote: Standard Ebooks distributes EPUB/AZW3; for plain text, use its "
              "GitHub source repos or convert an .epub, then `grimoire.py ingest`.")
        return

    if a.cmd == "search":
        gutenberg_search(" ".join(a.query))
        return

    got = []
    if a.cmd == "get":
        p1 = gutenberg_get(a.id, a.corpus)
        if p1:
            got.append(p1)
    elif a.cmd == "popular":
        got = gutenberg_popular(a.count, a.corpus)
    elif a.cmd == "wikisource":
        p1 = wikisource_get(" ".join(a.title), a.corpus)
        if p1:
            got.append(p1)

    print(f"\n{len(got)} new file(s) in {a.corpus}")
    if a.ingest and (got or a.cmd == "popular"):
        ingest(a.corpus, a.max_gb)


if __name__ == "__main__":
    main()
