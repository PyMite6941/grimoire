# GRIMOIRE — the cyberdeck's offline search engine

Your tome of searchable knowledge. Point it at a pile of documents and it
folds them into a single compressed file you can search **fully offline** —
on the deck, in the field, no internet. Built so a large corpus stays **under
1 GB** while every document is still randomly accessible from Python.

```
./run.sh ingest ~/wiki            # fold a folder of docs into the tome
./run.sh search "solar charge controller"
./run.sh stats                    # size + compression + budget
```

## How the storage works (compressed, but Python-accessible, ≤ 1 GB)

Everything lives in one SQLite file (`grimoire.db`):

- **Document bodies** are compressed **per-document** and stored as BLOBs, so
  any single doc can be pulled and decompressed on its own — no need to unpack
  the whole corpus. Compression is **zstandard level 19 with a shared
  dictionary trained on your corpus** (great on many small docs); if the
  `zstandard` package isn't installed it transparently falls back to **zlib**
  (Python stdlib), so it always runs.
- **Search** uses SQLite's built-in **FTS5** full-text index in *contentless*
  mode — it stores only the inverted index, not a second copy of the text, so
  searchable text isn't duplicated. Ranking is BM25.
- **The 1 GB budget is enforced:** `ingest --max-gb 1.0` (the default) stops
  adding documents once the store reaches the cap, and `stats` shows how much
  of the gigabyte you've used.

Because it's plain SQLite + a documented codec, anything in Python can read it
— not just this tool.

## CLI

| Command | What it does |
|---|---|
| `grimoire.py ingest <path> [--max-gb 1.0]` | Walk a file/folder, extract text (incl. HTML/XML stripping), compress + index. Re-runnable; skips dupes; respects the budget. |
| `grimoire.py search "<query>" [-n 10] [--raw]` | Full-text search; prints ranked title + path + snippet. `--raw` passes FTS5 syntax (`"phrases"`, `term*`, `a OR b`). |
| `grimoire.py get <id>` | Print one document (decompressed). |
| `grimoire.py stats` | Document count, compression ratio, disk use vs the 1 GB budget. |

`--db PATH` selects an alternate tome (default: `grimoire.db` beside the script).
Indexed file types: text, Markdown, HTML/XML, code, JSON/CSV, logs, config.

## Python API

```python
from grimoire import Grimoire

g = Grimoire()                       # opens grimoire.db beside this file
for hit in g.search("raspberry pi", n=5):
    print(hit["score"], hit["title"])
    print(g.get(hit["id"])[:300])    # decompressed on demand
```

`Grimoire(db).search(query, n, raw)` → list of `{id, title, path, score}`;
`Grimoire(db).get(id)` → the full decompressed text.

## Notes

- For the best ratio: `pip install -r requirements.txt` (adds zstandard).
- Good corpora for an offline deck: a Wikipedia text dump, man pages, your
  notes, RFCs, datasheets, the chess/reference docs you keep.
- The store is a single file — back it up or carry it on a USB SSD by copying
  `grimoire.db`.
