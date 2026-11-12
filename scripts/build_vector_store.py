"""Build the vector index over data/rules/manual_sections.jsonl using Gemini embeddings.

Usage (needs network and GEMINI_API_KEY/EMBEDDING_MODEL in .env):
    pip install langchain-community langchain-google-genai faiss-cpu     # preferred (thesis stack)
    pip install faiss-cpu numpy google-genai                             # fallback without LangChain
    python scripts/build_vector_store.py
Re-run whenever manual_sections.jsonl changes. If no index exists the app uses BM25.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import create_app  # noqa: E402
from app.nlp.rag_retriever import load_corpus, build_langchain_index, build_faiss_index  # noqa: E402

app = create_app()
corpus = load_corpus()
if not corpus:
    raise SystemExit("No corpus found at data/rules/manual_sections.jsonl")
settings = {k: app.config.get(k) for k in ("GEMINI_API_KEY", "EMBEDDING_MODEL", "LLM_TIMEOUT_SECONDS")}
if not settings["GEMINI_API_KEY"] or not settings["EMBEDDING_MODEL"]:
    raise SystemExit("Set GEMINI_API_KEY and EMBEDDING_MODEL in .env first.")
try:
    n = build_langchain_index(corpus, settings)
    print("Built LangChain FAISS index with %d passages (data/vector_store/langchain)." % n)
except ImportError:
    n = build_faiss_index(corpus, settings)
    print("LangChain not installed: built plain FAISS index with %d passages (data/vector_store)." % n)
