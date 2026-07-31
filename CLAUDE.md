# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

GRIMOIRE — the cyberdeck's offline search engine (one app inside a Raspberry Pi
cyberdeck project; the deck lives two levels up). It folds plain-text documents
and `.zim` archives into a single compressed SQLite "tome" and does full-text
search with no internet. Hard design constraint: keep the store **under 1 GB**
while every document stays **randomly accessible from Python**.

## Run

```bash
# the engine (this is the real, working entry point — a self-contained CLI)
python backend/grimoire.py ingest <path>     # build/extend the tome from files and/or .zim archives
python backend/grimoire.py search "query"    # full-text search, BM25-ranked, prints snippets
python backend/grimoire.py get <id>          # print one document (decompressed)
python backend/grimoire.py stats             # doc count, compression ratio, 1 GB budget usage

python run.py                                # interactive launcher (questionary) → frontend/* (WIP)
```

Flags: `--db PATH` (default `backend/grimoire.db`), `--max-gb N` (ingest size
cap, default 1.0), `search -n N`, `search --raw` (pass FTS5 query syntax through
verbatim: `"phrase"`, `term*`, `a OR b`).

Deps: `pip install -r requirements.txt`. Apps in this cyberdeck share one venv at
`../.venv` (create/refresh with `../setup-venv.sh`). **zstandard is optional** —
without it the engine falls back to zlib. **libzim is required only to ingest
`.zim`** files. There is no test suite or linter configured.

## Architecture

Three layers, engine-centric:

- `backend/grimoire.py` — the engine, and the only thing that really matters.
  Self-contained: usable as a library (`from grimoire import Grimoire;
  Grimoire(db).search(q)` / `.get(id)`) and as the CLI above (`main()` argparse).
- `frontend/` — UI layer (rich + questionary). `cli.py` does `from grimoire
  import *`; `cool-app.py` is a stub. Early WIP — wiring/sys.path is not finished.
- `run.py` — thin launcher that `subprocess`-runs a frontend script.

When refactoring the engine, the frontend imports it with `from grimoire import
*`, so keep the public names (`Grimoire`, the `cmd_*` functions, and the module
helpers) stable.

## Storage model (everything in one SQLite file)

- `docs(id, path UNIQUE, title, orig, body)` — `body` is the document text
  compressed **per document** (zstandard level 19 with a dictionary trained on
  the corpus, else zlib). Per-doc compression is what makes any single document
  decompressible on its own, without unpacking the whole corpus.
- `fts` — an FTS5 **contentless** index (`content=''`): stores only the inverted
  index, never a second copy of the text. Search joins `fts.rowid = docs.id` and
  orders by `bm25(fts)`.
- `meta` — the codec name and the trained zstd dictionary bytes, so a reader can
  rebuild the exact decompressor. `load_codec()` reads this.

Changing the FTS schema or leaving contentless mode means rebuilding the store —
there is no migration path.

## Ingestion (`iter_documents` → `cmd_ingest`)

- Text/markup/code files (`TEXT_EXT`) → one document each; HTML/XML is
  tag-stripped via `strip_html`.
- `.zim` archives (openZIM/Kiwix — offline Wikipedia, DevDocs, Gutenberg) → one
  document per HTML/text article via `iter_zim` (uses `libzim`; the article text
  is extracted and re-stored like any other doc). `iter_zim` relies on
  `Archive._get_entry_by_id`, which is libzim-version-sensitive — guard changes.
- The `--max-gb` cap is checked inside the ingest loop and stops early; dedup is
  by the document `path`/key (`INSERT OR IGNORE` on the UNIQUE column).

## Gotchas

- `backend/data/` holds the `.zim` corpora and the built `grimoire.db` — **~5 GB
  of data, gitignored**. Never commit it; rebuild the tome with `ingest`.
- CLI output reconfigures stdout to UTF-8 with `errors="replace"` so it can't
  crash on a Windows cp1252 console; `read_text` decodes `utf-8-sig` to drop BOMs.
- The 1 GB budget is the defining constraint — `ingest` enforces it and `stats`
  reports headroom; preserve that behavior.
