import argparse
import html.parser
import json
import os
import re
import sqlite3
import sys
import urllib.request
import zlib

DB_DEFAULT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "grimoire.db")
GB = 1024 ** 3
TEXT_EXT = {".txt", ".md", ".markdown", ".rst", ".html", ".htm", ".json",
            ".csv", ".tsv", ".log", ".py", ".js", ".ts", ".c", ".h", ".cpp",
            ".java", ".go", ".rs", ".sh", ".yaml", ".yml", ".ini", ".cfg",
            ".toml", ".tex", ".org", ".xml"}

HAVE_ZSTD = False

class Codec:
    def __init__(self, method="zlib", zdict=None):
        self.method = method
        self.zdict = zdict
        if method == "zstd":
            global HAVE_ZSTD
            try:
                import zstandard as _zstd
                HAVE_ZSTD = True
            except Exception:
                HAVE_ZSTD = False
                raise ValueError("zstandard not available — use method='zlib'")
            d = _zstd.ZstdCompressionDict(zdict) if zdict else None
            self._c = _zstd.ZstdCompressor(level=19, dict_data=d)
            self._d = _zstd.ZstdDecompressor(dict_data=d)

    def compress(self, data: bytes) -> bytes:
        if self.method == "zstd":
            return self._c.compress(data)
        return zlib.compress(data, 9)

    def decompress(self, blob: bytes) -> bytes:
        if self.method == "zstd":
            return self._d.decompress(blob)
        return zlib.decompress(blob)


def train_dict(samples, dict_size=110 * 1024):
    if not HAVE_ZSTD or len(samples) < 8:
        return None
    try:
        return _zstd.train_dictionary(dict_size, samples).as_bytes()
    except Exception:
        return None

class _Stripper(html.parser.HTMLParser):
    def __init__(self):
        super().__init__()
        self._buf = []

    def handle_data(self, d):
        self._buf.append(d)

    def text(self):
        return " ".join("".join(self._buf).split())

def strip_html(s):
    p = _Stripper()
    try:
        p.feed(s)
        return p.text()
    except Exception:
        return s

def read_text(path):
    try:
        raw = open(path, "rb").read()
    except Exception:
        return None
    for enc in ("utf-8-sig", "latin-1"):
        try:
            txt = raw.decode(enc)
            break
        except Exception:
            txt = None
    if txt is None:
        return None
    if path.lower().endswith((".html", ".htm", ".xml")):
        txt = strip_html(txt)
    return txt

def derive_title(text, path):
    for line in text.splitlines():
        line = line.strip().lstrip("#").strip()
        if line:
            return line[:120]
    return os.path.basename(path)

def iter_zim(path):
    try:
        from libzim.reader import Archive
    except Exception:
        sys.stderr.write("  skipping %s: libzim not installed (pip install libzim)\n" % path)
        return
    try:
        arc = Archive(path)
    except Exception as e:
        sys.stderr.write("  cannot open %s: %s\n" % (path, e))
        return
    for i in range(getattr(arc, "entry_count", 0)):
        try:
            entry = arc._get_entry_by_id(i)
            item = entry.get_item()
            mt = item.mimetype or ""
            if "html" not in mt and "text/plain" not in mt:
                continue
            txt = bytes(item.content).decode("utf-8", "replace")
            if "html" in mt:
                txt = strip_html(txt)
            if txt.strip():
                yield (entry.title or entry.path, txt, "%s#%s" % (path, entry.path))
        except Exception:
            continue

def iter_documents(paths):
    files, zims = [], []
    for p in paths:
        if os.path.isfile(p):
            (zims if p.lower().endswith(".zim") else files).append(p)
        elif os.path.isdir(p):
            for root, _, names in os.walk(p):
                for n in names:
                    fp = os.path.join(root, n)
                    ext = os.path.splitext(n)[1].lower()
                    if ext == ".zim":
                        zims.append(fp)
                    elif ext in TEXT_EXT:
                        files.append(fp)
    for fp in sorted(files):
        t = read_text(fp)
        if t and t.strip():
            yield (derive_title(t, fp), t, fp)
    for zp in sorted(zims):
        for doc in iter_zim(zp):
            yield doc

def connect(db):
    cx = sqlite3.connect(db)
    cx.execute("PRAGMA journal_mode=WAL")
    return cx

def init_db(cx):
    cx.executescript("""
        CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value BLOB);
        CREATE TABLE IF NOT EXISTS docs(
            id INTEGER PRIMARY KEY, path TEXT UNIQUE, title TEXT,
            orig INTEGER, body BLOB);
        CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5(text, content='');
        CREATE TABLE IF NOT EXISTS embeds(
            doc_id INTEGER PRIMARY KEY, vector BLOB,
            FOREIGN KEY(doc_id) REFERENCES docs(id));
    """)

def load_codec(cx):
    row = cx.execute("SELECT value FROM meta WHERE key='method'").fetchone()
    if not row:
        return Codec("zstd" if HAVE_ZSTD else "zlib", None)
    method = bytes(row[0]).decode()
    zr = cx.execute("SELECT value FROM meta WHERE key='zdict'").fetchone()
    return Codec(method, bytes(zr[0]) if zr else None)

def db_size(db):
    return sum(os.path.getsize(db + s) for s in ("", "-wal", "-shm")
               if os.path.exists(db + s))

def fts_query(q):
    terms = re.findall(r"\w+", q)
    return " ".join('"%s"' % t for t in terms) if terms else '""'

def snippet(text, query, width=240):
    terms = re.findall(r"\w+", query.lower())
    low = text.lower()
    pos = -1
    for t in terms:
        i = low.find(t)
        if i != -1 and (pos == -1 or i < pos):
            pos = i
    if pos == -1:
        pos = 0
    start = max(0, pos - width // 3)
    s = " ".join(text[start:start + width].split())
    return ("..." if start else "") + s + "..."

def cosine_sim(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    return dot / (na * nb + 1e-10)

def embed_text(text, model="nomic-embed-text", base_url="http://localhost:11434"):
    try:
        payload = json.dumps({"model": model, "input": text[:8192]}).encode()
        req = urllib.request.Request(
            f"{base_url}/api/embed",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read())
            return data["embeddings"][0]
    except Exception as e:
        raise RuntimeError(f"embedding failed: {e}")

def generate(prompt, model="qwen2.5-coder:3b", base_url="http://localhost:11434"):
    try:
        payload = json.dumps({
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {"num_ctx": 4096, "num_thread": 4}
        }).encode()
        req = urllib.request.Request(
            f"{base_url}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=120) as r:
            data = json.loads(r.read())
            return data["response"]
    except Exception as e:
        raise RuntimeError(f"generation failed: {e}")


class Grimoire:
    def __init__(self, db=DB_DEFAULT):
        self.db = db
        self.cx = connect(db)
        self.codec = load_codec(self.cx)

    def search(self, query, n=10, raw=False):
        q = query if raw else fts_query(query)
        rows = self.cx.execute(
            "SELECT d.id, d.title, d.path, bm25(fts) AS rank "
            "FROM fts JOIN docs d ON d.id = fts.rowid "
            "WHERE fts MATCH ? ORDER BY rank LIMIT ?", (q, n)).fetchall()
        return [{"id": r[0], "title": r[1], "path": r[2], "score": -r[3]}
                for r in rows]

    def get(self, doc_id):
        r = self.cx.execute("SELECT body FROM docs WHERE id=?", (doc_id,)).fetchone()
        return self.codec.decompress(bytes(r[0])).decode("utf-8", "replace") if r else None

    def stats(self):
        if not os.path.exists(self.db):
            return {"doc_count": 0, "orig_mb": 0, "comp_mb": 0,
                    "store_mb": 0, "budget_mb": 1000, "ratio": 0, "codec": "n/a"}
        n = self.cx.execute("SELECT COUNT(*) FROM docs").fetchone()[0]
        orig = self.cx.execute("SELECT COALESCE(SUM(orig),0) FROM docs").fetchone()[0]
        comp = self.cx.execute("SELECT COALESCE(SUM(LENGTH(body)),0) FROM docs").fetchone()[0]
        size = db_size(self.db)
        embeds_n = self.cx.execute("SELECT COUNT(*) FROM embeds").fetchone()[0]
        return {
            "doc_count": n,
            "orig_mb": orig / 1e6,
            "comp_mb": comp / 1e6,
            "store_mb": size / 1e6,
            "budget_mb": 1000,
            "ratio": (orig / comp) if comp else 0,
            "codec": self.codec.method,
            "embed_count": embeds_n,
        }

    def embed_corpus(self, model="nomic-embed-text", base_url="http://localhost:11434"):
        rows = self.cx.execute(
            "SELECT d.id, d.title, d.body FROM docs d "
            "LEFT JOIN embeds e ON d.id = e.doc_id "
            "WHERE e.doc_id IS NULL").fetchall()
        if not rows:
            return 0
        for doc_id, title, body in rows:
            text = self.codec.decompress(bytes(body)).decode("utf-8", "replace")
            chunk = (title + "\n\n" + text[:4096]).encode("utf-8")
            try:
                vec = embed_text(chunk.decode("utf-8", "replace"), model=model, base_url=base_url)
                blob = json.dumps(vec).encode()
                self.cx.execute(
                    "INSERT OR REPLACE INTO embeds(doc_id, vector) VALUES(?,?)",
                    (doc_id, blob))
            except Exception as e:
                sys.stderr.write(f"  embed failed for doc {doc_id}: {e}\n")
                continue
        self.cx.commit()
        return len(rows)

    def _find_similar(self, query_vec, n=5):
        rows = self.cx.execute(
            "SELECT e.doc_id, e.vector FROM embeds e LIMIT 10000").fetchall()
        scored = []
        for doc_id, blob in rows:
            vec = json.loads(bytes(blob).decode())
            sim = cosine_sim(query_vec, vec)
            scored.append((sim, doc_id))
        scored.sort(key=lambda x: -x[0])
        return scored[:n]

    def query(self, text, model="nomic-embed-text", gen_model="qwen2.5-coder:3b",
              base_url="http://localhost:11434", n_docs=5):
        query_vec = embed_text(text, model=model, base_url=base_url)
        similar = self._find_similar(query_vec, n=n_docs)
        if not similar:
            return {"answer": "No relevant documents found in the tome.", "sources": []}
        context_parts = []
        sources = []
        for sim, doc_id in similar:
            doc = self.cx.execute(
                "SELECT title, path, body FROM docs WHERE id=?",
                (doc_id,)).fetchone()
            if doc:
                body = self.codec.decompress(bytes(doc[2])).decode("utf-8", "replace")
                context_parts.append(f"--- {doc[0]} ---\n{body[:2000]}")
                sources.append({"id": doc_id, "title": doc[0], "path": doc[1], "score": sim})
        context = "\n\n".join(context_parts)
        prompt = (
            "You are a knowledgeable assistant answering questions based on the provided documents.\n\n"
            f"Context documents:\n{context}\n\n"
            f"Question: {text}\n\n"
            "Answer concisely based on the context above. If the context doesn't contain "
            "the answer, say so."
        )
        answer = generate(prompt, model=gen_model, base_url=base_url)
        return {"answer": answer, "sources": sources}


def cmd_ingest(a):
    cx = connect(a.db)
    init_db(cx)

    row = cx.execute("SELECT value FROM meta WHERE key='method'").fetchone()
    if row:
        codec = load_codec(cx)
    else:
        samples = []
        for _, text, _ in iter_documents([a.path]):
            samples.append(text.encode("utf-8"))
            if len(samples) >= 500:
                break
        if not samples:
            print("no ingestable documents found under %s" % a.path)
            return
        zdict = train_dict(samples)
        method = "zstd" if HAVE_ZSTD else "zlib"
        cx.execute("INSERT OR REPLACE INTO meta VALUES('method',?)", (method.encode(),))
        if zdict:
            cx.execute("INSERT OR REPLACE INTO meta VALUES('zdict',?)", (zdict,))
        cx.commit()
        codec = Codec(method, zdict)
        print("codec: %s%s" % (method, " + trained dictionary" if zdict else ""))

    cap = int(a.max_gb * GB)
    have = {r[0] for r in cx.execute("SELECT path FROM docs")}
    added = 0
    for i, (title, text, key) in enumerate(iter_documents([a.path])):
        if key in have:
            continue
        if i % 50 == 0 and db_size(a.db) >= cap:
            print("reached %.2f GB budget — stopping early" % a.max_gb)
            break
        data = text.encode("utf-8")
        blob = codec.compress(data)
        cur = cx.execute(
            "INSERT OR IGNORE INTO docs(path,title,orig,body) VALUES(?,?,?,?)",
            (key, title[:200], len(data), blob))
        if cur.rowcount:
            cx.execute("INSERT INTO fts(rowid,text) VALUES(?,?)", (cur.lastrowid, text))
            added += 1
        if added and added % 200 == 0:
            cx.commit()
            print("  ... %d docs, %.0f MB" % (added, db_size(a.db) / 1e6))
    cx.commit()
    print("ingested %d docs. store: %.1f MB" % (added, db_size(a.db) / 1e6))

def cmd_search(a):
    if not os.path.exists(a.db):
        print("no store yet — run: grimoire.py ingest <path>")
        return
    g = Grimoire(a.db)
    try:
        hits = g.search(a.query, n=a.n, raw=a.raw)
    except sqlite3.OperationalError as e:
        print("query error: %s (try --raw for FTS5 syntax)" % e)
        return
    if not hits:
        print("no matches.")
        return
    for h in hits:
        body = g.get(h["id"])
        print("\n[%d] %s   (score %.2f)" % (h["id"], h["title"], h["score"]))
        print("    %s" % h["path"])
        print("    %s" % snippet(body, a.query))

def cmd_get(a):
    g = Grimoire(a.db)
    body = g.get(a.id)
    if body is None:
        print("no document with id %d" % a.id)
    else:
        sys.stdout.write(body if body.endswith("\n") else body + "\n")

def cmd_stats(a):
    g = Grimoire(a.db)
    s = g.stats()
    print("GRIMOIRE  %s" % a.db)
    print("  documents      : %d" % s["doc_count"])
    print("  original text  : %.1f MB" % s["orig_mb"])
    print("  compressed body: %.1f MB   (%.1fx smaller)" % (s["comp_mb"], s["ratio"]))
    print("  store on disk  : %.1f MB" % s["store_mb"])
    print("  1 GB budget    : %.0f%% used  (%.0f MB free)" % (
        100 * s["store_mb"] / 1000, 1000 - s["store_mb"]))
    print("  codec          : %s" % s["codec"])
    print("  embedded docs  : %d" % s["embed_count"])

def cmd_embed(a):
    g = Grimoire(a.db)
    print("embedding unembedded documents with '%s'..." % a.model)
    n = g.embed_corpus(model=a.model, base_url=a.base_url)
    print("embedded %d docs" % n)

def cmd_query(a):
    g = Grimoire(a.db)
    print("embedding query...")
    try:
        result = g.query(a.text, model=a.embed_model, gen_model=a.gen_model,
                         base_url=a.base_url, n_docs=a.n_docs)
    except RuntimeError as e:
        print("query error: %s" % e)
        return
    print("\n" + "=" * 60)
    print(result["answer"])
    print("=" * 60)
    if result["sources"]:
        print("\nSources:")
        for s in result["sources"]:
            print("  [%d] %s  (similarity: %.3f)" % (s["id"], s["title"], s["score"]))
            print("       %s" % s["path"])

def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    p = argparse.ArgumentParser(prog="grimoire", description="cyberdeck offline search engine + RAG")
    p.add_argument("--db", default=DB_DEFAULT, help="store path")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("ingest", help="add files/folders to the tome")
    s.add_argument("path")
    s.add_argument("--max-gb", type=float, default=1.0)
    s.set_defaults(fn=cmd_ingest)

    s = sub.add_parser("search", help="full-text search")
    s.add_argument("query")
    s.add_argument("-n", type=int, default=10)
    s.add_argument("--raw", action="store_true")
    s.set_defaults(fn=cmd_search)

    s = sub.add_parser("get", help="print a document by id")
    s.add_argument("id", type=int)
    s.set_defaults(fn=cmd_get)

    s = sub.add_parser("stats", help="store size + compression report")
    s.set_defaults(fn=cmd_stats)

    s = sub.add_parser("embed", help="compute embeddings for unembedded docs")
    s.add_argument("--model", default="nomic-embed-text", help="Ollama embedding model")
    s.add_argument("--base-url", default="http://localhost:11434", help="Ollama base URL")
    s.set_defaults(fn=cmd_embed)

    s = sub.add_parser("query", help="ask a question using RAG over the tome")
    s.add_argument("text")
    s.add_argument("--embed-model", default="nomic-embed-text", help="Ollama embedding model")
    s.add_argument("--gen-model", default="qwen2.5-coder:3b", help="Ollama generation model")
    s.add_argument("--base-url", default="http://localhost:11434", help="Ollama base URL")
    s.add_argument("-n", "--n-docs", type=int, default=5, help="number of docs to retrieve")
    s.set_defaults(fn=cmd_query)

    a = p.parse_args(argv)
    a.fn(a)

if __name__ == "__main__":
    main()
