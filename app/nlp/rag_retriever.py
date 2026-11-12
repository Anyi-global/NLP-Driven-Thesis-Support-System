"""Retrieval over approved Manual of Style excerpts (data/rules/manual_sections.jsonl).

Backends, tried in order:
  1. LangChain FAISS vector store + Gemini embeddings (the stack named in the thesis).
  2. Plain FAISS index + Gemini embeddings (no pickle), when LangChain is not installed.
     Both are built by scripts/build_vector_store.py and need an API key.
  3. BM25 (stemmed when NLTK is installed). No dependencies; works offline and is the fallback.
"""
import json
import math
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CORPUS_PATH = ROOT / "data" / "rules" / "manual_sections.jsonl"
STORE_DIR = ROOT / "data" / "vector_store"
STOP = set("a an and are as at be by for from has have in is it its of on or that the this to was were will with shall should".split())
_cache = {}


def _tokens(text):
    from .linguistics import stem  # NLTK Porter stemmer when installed, identity otherwise
    return [stem(t) for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in STOP]


def load_corpus(path=CORPUS_PATH):
    path = Path(path)
    if not path.exists():
        return []
    key = (str(path), path.stat().st_mtime)
    if key in _cache:
        return _cache[key]
    items = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            items.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    _cache.clear()
    _cache[key] = items
    return items


def bm25_search(query, corpus, top_k=3, k1=1.5, b=0.75):
    docs = [_tokens(i.get("section", "") + " " + i.get("text", "")) for i in corpus]
    if not docs:
        return []
    avg = sum(len(d) for d in docs) / len(docs) or 1
    df = Counter(t for d in docs for t in set(d))
    n = len(docs)
    q = _tokens(query)
    scored = []
    for item, d in zip(corpus, docs):
        tf = Counter(d)
        score = 0.0
        for t in q:
            if t in tf:
                idf = math.log(1 + (n - df[t] + .5) / (df[t] + .5))
                score += idf * tf[t] * (k1 + 1) / (tf[t] + k1 * (1 - b + b * len(d) / avg))
        if score > 0:
            scored.append((score, item))
    scored.sort(key=lambda x: -x[0])
    return [dict(i, retrieval_score=round(s, 3), backend="bm25") for s, i in scored[:top_k]]


LC_DIR = STORE_DIR / "langchain"


def _lc_embeddings(settings):
    from langchain_google_genai import GoogleGenerativeAIEmbeddings
    return GoogleGenerativeAIEmbeddings(model=settings["EMBEDDING_MODEL"], google_api_key=settings["GEMINI_API_KEY"])


def build_langchain_index(corpus, settings, out_dir=LC_DIR):
    """LangChain FAISS vector store (the stack named in the thesis). Raises ImportError if LangChain is absent."""
    from langchain_community.vectorstores import FAISS
    store = FAISS.from_texts([c.get("text", "") for c in corpus], _lc_embeddings(settings),
                             metadatas=[{"section": c.get("section", ""), "source": c.get("source", "")} for c in corpus])
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    store.save_local(str(out_dir))
    return len(corpus)


def _langchain_search(query, top_k, settings):
    if not (LC_DIR / "index.faiss").exists():
        return None
    from langchain_community.vectorstores import FAISS
    # index.pkl is a pickle: only ever load a store that this application's own build script wrote.
    store = FAISS.load_local(str(LC_DIR), _lc_embeddings(settings), allow_dangerous_deserialization=True)
    return [dict(doc.metadata, text=doc.page_content, retrieval_score=round(float(score), 3), backend="langchain-faiss")
            for doc, score in store.similarity_search_with_score(query, k=top_k)]


def build_faiss_index(corpus, settings, out_dir=STORE_DIR):
    """Plain FAISS + JSON metadata (no pickle). Used when LangChain is not installed."""
    import faiss
    import numpy as np
    from .gemini import embed_texts
    vectors = []
    for i in range(0, len(corpus), 50):
        vectors += embed_texts([c.get("section", "") + ". " + c.get("text", "") for c in corpus[i:i + 50]], settings)
    arr = np.array(vectors, dtype="float32")
    faiss.normalize_L2(arr)
    index = faiss.IndexFlatIP(arr.shape[1])
    index.add(arr)
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    faiss.write_index(index, str(Path(out_dir) / "index.faiss"))
    (Path(out_dir) / "meta.json").write_text(json.dumps(corpus, ensure_ascii=False), encoding="utf-8")
    return len(corpus)


def _faiss_search(query, top_k, settings):
    index_file, meta_file = STORE_DIR / "index.faiss", STORE_DIR / "meta.json"
    if not (index_file.exists() and meta_file.exists()):
        return None
    import faiss
    import numpy as np
    from .gemini import embed_texts
    index = faiss.read_index(str(index_file))
    meta = json.loads(meta_file.read_text(encoding="utf-8"))
    vec = np.array(embed_texts([query], settings, query=True), dtype="float32")
    faiss.normalize_L2(vec)
    scores, ids = index.search(vec, min(top_k, index.ntotal))
    return [dict(meta[i], retrieval_score=round(float(sc), 3), backend="faiss") for sc, i in zip(scores[0], ids[0]) if i >= 0]


def _dense_search(query, top_k, settings):
    """LangChain-FAISS, then plain FAISS. Any failure (no network, bad index, retired model) -> None -> BM25."""
    if not settings.get("GEMINI_API_KEY"):
        return None
    for fn in (_langchain_search, _faiss_search):
        try:
            result = fn(query, top_k, settings)
        except Exception:
            result = None
        if result:
            return result
    return None


def retrieve(query, top_k=3, settings=None):
    top_k = max(1, min(int(top_k), 10))
    dense = _dense_search(query, top_k, settings or {})
    if dense:
        return dense
    return bm25_search(query, load_corpus(), top_k)
