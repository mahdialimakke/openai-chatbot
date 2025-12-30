import re
import time
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup


_token_re = re.compile(r"[a-z0-9]+", re.IGNORECASE)


def normalize_text(s: str) -> str:
    s = s.lower()
    s = s.replace("\u00a0", " ")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def tokenize(s: str) -> list[str]:
    s = normalize_text(s)
    return _token_re.findall(s)


def extract_text_from_html(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
        tag.decompose()
    text = soup.get_text(separator="\n")
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return "\n".join(lines)


def fetch_url_text(url: str, max_chars: int = 120_000) -> str:
    headers = {"User-Agent": "Mozilla/5.0"}
    r = requests.get(url, headers=headers, timeout=30)
    r.raise_for_status()
    ct = (r.headers.get("content-type") or "").lower()
    if "pdf" in ct or url.lower().endswith(".pdf"):
        import fitz
        doc = fitz.open(stream=r.content, filetype="pdf")
        text = "\n".join(page.get_text() for page in doc)
        doc.close()
    else:
        text = extract_text_from_html(r.text)
    return text[:max_chars]


def chunk_tokens(tokens: list[str], chunk_size: int = 450, overlap: int = 80) -> list[str]:
    out = []
    i = 0
    step = max(1, chunk_size - overlap)
    n = len(tokens)
    while i < n:
        chunk = tokens[i:i + chunk_size]
        if not chunk:
            break
        out.append(" ".join(chunk))
        i += step
    return out


@dataclass
class Hit:
    url: str
    title: str
    chunk: str
    score: float


class MevzuatKB:
    def __init__(self, base_dir: Path):
        self.base_dir = Path(base_dir)
        self.db_path = self.base_dir / "mevzuat_kb.sqlite"
        self.seed_url = "https://mevzuat.emu.edu.tr/content-en.htm"
        self.allowed_host = "mevzuat.emu.edu.tr"
        self._init_db()

    def _init_db(self):
        con = sqlite3.connect(self.db_path)
        cur = con.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS docs (
                url TEXT PRIMARY KEY,
                title TEXT,
                fetched_at INTEGER,
                content TEXT
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT,
                title TEXT,
                chunk TEXT,
                chunk_norm TEXT
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_chunks_url ON chunks(url)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_chunks_norm ON chunks(chunk_norm)")
        con.commit()
        con.close()

    def _is_allowed(self, url: str) -> bool:
        p = urlparse(url)
        return p.scheme in ("http", "https") and p.netloc == self.allowed_host

    def _extract_links(self, html: str, page_url: str) -> list[str]:
        soup = BeautifulSoup(html, "html.parser")
        links = set()
        for a in soup.select("a[href]"):
            href = (a.get("href") or "").strip()
            if not href:
                continue
            full = urljoin(page_url, href)
            if not self._is_allowed(full):
                continue
            low = full.lower()
            if low.endswith((".htm", ".html", ".pdf")):
                links.add(full)
        return sorted(links)

    def ensure_index(self, refresh_days: int = 14, max_pages: int = 600):
        now = int(time.time())
        refresh_seconds = refresh_days * 24 * 3600

        headers = {"User-Agent": "Mozilla/5.0"}
        seed = requests.get(self.seed_url, headers=headers, timeout=30)
        seed.raise_for_status()
        discovered = [self.seed_url] + self._extract_links(seed.text, self.seed_url)
        discovered = discovered[:max_pages]

        con = sqlite3.connect(self.db_path)
        cur = con.cursor()

        for url in discovered:
            cur.execute("SELECT fetched_at FROM docs WHERE url=?", (url,))
            row = cur.fetchone()
            if row and (now - int(row[0])) < refresh_seconds:
                continue
            try:
                text = fetch_url_text(url)
            except Exception:
                continue

            title = url.split("/")[-1]
            cur.execute("""
                INSERT INTO docs(url, title, fetched_at, content)
                VALUES(?,?,?,?)
                ON CONFLICT(url) DO UPDATE SET
                    title=excluded.title,
                    fetched_at=excluded.fetched_at,
                    content=excluded.content
            """, (url, title, now, text))

            cur.execute("DELETE FROM chunks WHERE url=?", (url,))
            toks = tokenize(text)
            chunks = chunk_tokens(toks)

            rows = []
            for c in chunks:
                c_norm = " ".join(tokenize(c))
                rows.append((url, title, c, c_norm))

            cur.executemany(
                "INSERT INTO chunks(url, title, chunk, chunk_norm) VALUES(?,?,?,?)",
                rows
            )

        con.commit()
        con.close()

    def search(self, query: str, k: int = 6) -> list[Hit]:
        q_terms = tokenize(query)
        if not q_terms:
            return []
        q_set = set(q_terms)

        con = sqlite3.connect(self.db_path)
        cur = con.cursor()
        cur.execute("SELECT url, title, chunk, chunk_norm FROM chunks")
        rows = cur.fetchall()
        con.close()

        hits: list[Hit] = []
        for url, title, chunk, chunk_norm in rows:
            if not chunk_norm:
                continue
            c_terms = chunk_norm.split()
            if not c_terms:
                continue
            c_set = set(c_terms)
            overlap = len(q_set & c_set)
            if overlap <= 0:
                continue
            denom = (len(q_set) ** 0.5) * (len(c_set) ** 0.5)
            score = float(overlap) / denom if denom else float(overlap)
            hits.append(Hit(url=url, title=title, chunk=chunk, score=score))

        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:k]

    def build_context(self, query: str, k: int = 6) -> str:
        hits = self.search(query, k=k)
        if not hits:
            return ""
        parts = ["EMU MEVZUAT EXCERPTS:"]
        for i, h in enumerate(hits, 1):
            parts.append(f"\n[{i}] {h.url}\n{h.chunk}\n")
        return "\n".join(parts)
