"""Knowledge base per agent — loads PDFs, DOCX, TXT and retrieves relevant chunks via BM25.

Drop files into api-service/knowledge/{agent_id}/ and they are auto-indexed at startup.
Supported formats: .pdf, .docx, .doc, .txt, .md

Usage:
    from knowledge_base import get_agent_context
    context = get_agent_context("sueno", "calidad del sueño y melatonina")
"""
from __future__ import annotations

import re
import math
import hashlib
from collections import defaultdict
from pathlib import Path
from typing import Optional

KNOWLEDGE_DIR = Path(__file__).parent / "knowledge"
AGENT_IDS = ["animo", "sueno", "foco", "energia", "pantalla", "rachas"]
CHUNK_SIZE = 400       # words per chunk
CHUNK_OVERLAP = 80     # overlapping words between chunks
TOP_K = 4              # chunks to retrieve per query


# ─── Document loaders ─────────────────────────────────────────────────────────

def _load_txt(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def _load_pdf(path: Path) -> str:
    try:
        import pypdf
        reader = pypdf.PdfReader(str(path))
        return "\n".join(
            page.extract_text() or "" for page in reader.pages
        )
    except ImportError:
        return f"[PDF no leído — instala pypdf: {path.name}]"
    except Exception as e:
        return f"[Error leyendo PDF {path.name}: {e}]"


def _load_docx(path: Path) -> str:
    try:
        import docx
        doc = docx.Document(str(path))
        return "\n".join(p.text for p in doc.paragraphs if p.text.strip())
    except ImportError:
        return f"[DOCX no leído — instala python-docx: {path.name}]"
    except Exception as e:
        return f"[Error leyendo DOCX {path.name}: {e}]"


def _load_file(path: Path) -> str:
    ext = path.suffix.lower()
    if ext == ".pdf":
        return _load_pdf(path)
    if ext in (".docx", ".doc"):
        return _load_docx(path)
    if ext in (".txt", ".md"):
        return _load_txt(path)
    return ""


# ─── Chunking ─────────────────────────────────────────────────────────────────

def _chunk_text(text: str, source: str) -> list[dict]:
    words = text.split()
    chunks = []
    start = 0
    while start < len(words):
        end = min(start + CHUNK_SIZE, len(words))
        chunk_text = " ".join(words[start:end])
        if len(chunk_text.strip()) > 40:
            chunks.append({"text": chunk_text, "source": source})
        start += CHUNK_SIZE - CHUNK_OVERLAP
    return chunks


# ─── BM25 retriever ───────────────────────────────────────────────────────────

def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-záéíóúñü]+", text.lower())


class BM25Index:
    k1 = 1.5
    b = 0.75

    def __init__(self, chunks: list[dict]):
        self.chunks = chunks
        self.n = len(chunks)
        if self.n == 0:
            return
        self._tokenized = [_tokenize(c["text"]) for c in chunks]
        self._avgdl = sum(len(t) for t in self._tokenized) / self.n
        # document frequency
        self._df: dict[str, int] = defaultdict(int)
        for doc_tokens in self._tokenized:
            for term in set(doc_tokens):
                self._df[term] += 1

    def _idf(self, term: str) -> float:
        df = self._df.get(term, 0)
        return math.log((self.n - df + 0.5) / (df + 0.5) + 1)

    def query(self, text: str, k: int = TOP_K) -> list[dict]:
        if self.n == 0:
            return []
        q_terms = _tokenize(text)
        scores = []
        for i, doc_tokens in enumerate(self._tokenized):
            tf_map: dict[str, int] = defaultdict(int)
            for t in doc_tokens:
                tf_map[t] += 1
            dl = len(doc_tokens)
            score = 0.0
            for term in q_terms:
                tf = tf_map.get(term, 0)
                idf = self._idf(term)
                score += idf * (tf * (self.k1 + 1)) / (
                    tf + self.k1 * (1 - self.b + self.b * dl / self._avgdl)
                )
            scores.append((score, i))
        scores.sort(reverse=True)
        return [self.chunks[i] for score, i in scores[:k] if score > 0]


# ─── Per-agent knowledge base ─────────────────────────────────────────────────

class AgentKnowledgeBase:
    def __init__(self, agent_id: str):
        self.agent_id = agent_id
        self.doc_count = 0
        self.chunk_count = 0
        self._index: Optional[BM25Index] = None
        self._load()

    def _load(self):
        folder = KNOWLEDGE_DIR / self.agent_id
        if not folder.exists():
            self._index = BM25Index([])
            return

        all_chunks: list[dict] = []
        for path in sorted(folder.iterdir()):
            if path.suffix.lower() not in (".pdf", ".docx", ".doc", ".txt", ".md"):
                continue
            text = _load_file(path)
            if not text.strip():
                continue
            chunks = _chunk_text(text, path.name)
            all_chunks.extend(chunks)
            self.doc_count += 1

        self.chunk_count = len(all_chunks)
        self._index = BM25Index(all_chunks)
        if self.doc_count:
            print(f"[kb:{self.agent_id}] {self.doc_count} docs, {self.chunk_count} chunks indexed")

    def retrieve(self, query: str, k: int = TOP_K) -> str:
        if not self._index or self.chunk_count == 0:
            return ""
        results = self._index.query(query, k=k)
        if not results:
            return ""
        parts = [f"[{r['source']}]\n{r['text']}" for r in results]
        return "\n\n---\n\n".join(parts)

    def reload(self):
        """Hot-reload documents without restarting the server."""
        self.doc_count = 0
        self.chunk_count = 0
        self._load()

    def list_docs(self) -> list[dict]:
        folder = KNOWLEDGE_DIR / self.agent_id
        if not folder.exists():
            return []
        docs = []
        for path in sorted(folder.iterdir()):
            if path.suffix.lower() in (".pdf", ".docx", ".doc", ".txt", ".md"):
                docs.append({
                    "name": path.name,
                    "size_kb": round(path.stat().st_size / 1024, 1),
                    "type": path.suffix.lower().lstrip("."),
                })
        return docs


# ─── Global registry — loaded once at startup ────────────────────────────────

_registry: dict[str, AgentKnowledgeBase] = {}


def _init():
    for agent_id in AGENT_IDS:
        _registry[agent_id] = AgentKnowledgeBase(agent_id)


_init()


def get_agent_context(agent_id: str, query: str, k: int = TOP_K) -> str:
    """Return relevant knowledge chunks for an agent given a query."""
    kb = _registry.get(agent_id)
    if not kb:
        return ""
    return kb.retrieve(query, k=k)


def reload_agent(agent_id: str) -> dict:
    """Hot-reload a single agent's knowledge base."""
    if agent_id not in AGENT_IDS:
        return {"error": f"agente '{agent_id}' desconocido"}
    _registry[agent_id].reload()
    kb = _registry[agent_id]
    return {"agent": agent_id, "docs": kb.doc_count, "chunks": kb.chunk_count}


def get_kb_status() -> dict:
    """Return status for all agent knowledge bases."""
    return {
        agent_id: {
            "docs": kb.doc_count,
            "chunks": kb.chunk_count,
            "files": kb.list_docs(),
        }
        for agent_id, kb in _registry.items()
    }
