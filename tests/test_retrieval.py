"""Retriever backend selection, with LangChain faked (not installed in every environment)."""
import sys
import tempfile
import types
import unittest
from pathlib import Path

from app.nlp import rag_retriever as rr

SETTINGS = {"GEMINI_API_KEY": "k", "EMBEDDING_MODEL": "m"}


class Doc:
    def __init__(self, text, metadata): self.page_content, self.metadata = text, metadata


def install_fakes(search_result=None, raise_on_load=False):
    calls = {}

    class FAISS:
        @classmethod
        def load_local(cls, path, emb, allow_dangerous_deserialization=False):
            calls["load"] = (path, allow_dangerous_deserialization)
            if raise_on_load:
                raise RuntimeError("index corrupt")
            return cls()

        def similarity_search_with_score(self, query, k=3):
            calls["query"] = (query, k)
            return search_result or []

        @classmethod
        def from_texts(cls, texts, emb, metadatas=None):
            calls["built"] = (texts, metadatas)
            return cls()

        def save_local(self, path):
            calls["saved"] = path

    sys.modules["langchain_community"] = types.ModuleType("langchain_community")
    sys.modules["langchain_community.vectorstores"] = types.SimpleNamespace(FAISS=FAISS)
    sys.modules["langchain_google_genai"] = types.SimpleNamespace(
        GoogleGenerativeAIEmbeddings=lambda model, google_api_key: ("emb", model, google_api_key))
    return calls


class Backends(unittest.TestCase):
    def setUp(self):
        self._saved = {k: sys.modules.get(k) for k in ("langchain_community", "langchain_community.vectorstores", "langchain_google_genai")}
        self._lc = rr.LC_DIR
        self.tmp = tempfile.TemporaryDirectory()
        rr.LC_DIR = Path(self.tmp.name)
        (rr.LC_DIR / "index.faiss").write_bytes(b"x")

    def tearDown(self):
        rr.LC_DIR = self._lc
        self.tmp.cleanup()
        for k, v in self._saved.items():
            sys.modules.pop(k, None)
            if v is not None:
                sys.modules[k] = v

    def test_langchain_backend_used_when_available(self):
        calls = install_fakes([(Doc("Use 'and' not &.", {"section": "Citation", "source": "Manual p.16"}), 0.1234)])
        out = rr.retrieve("two authors", 3, SETTINGS)
        self.assertEqual((out[0]["backend"], out[0]["section"], out[0]["retrieval_score"]), ("langchain-faiss", "Citation", 0.123))
        self.assertEqual(out[0]["text"], "Use 'and' not &.")
        self.assertEqual(calls["query"], ("two authors", 3))

    def test_failure_falls_back_to_bm25(self):
        install_fakes(raise_on_load=True)
        out = rr.retrieve("citation author year comma", 2, SETTINGS)
        self.assertTrue(out and out[0]["backend"] == "bm25")

    def test_no_api_key_means_bm25_without_touching_langchain(self):
        calls = install_fakes([(Doc("x", {}), 0.0)])
        out = rr.retrieve("citation author year comma", 2, {})
        self.assertNotIn("query", calls)
        self.assertTrue(out and out[0]["backend"] == "bm25")

    def test_build_passes_text_and_metadata(self):
        calls = install_fakes()
        n = rr.build_langchain_index([{"section": "A", "text": "alpha", "source": "S1"}, {"section": "B", "text": "beta", "source": "S2"}],
                                     SETTINGS, out_dir=self.tmp.name)
        self.assertEqual(n, 2)
        self.assertEqual(calls["built"][0], ["alpha", "beta"])
        self.assertEqual(calls["built"][1][1], {"section": "B", "source": "S2"})
        self.assertEqual(calls["saved"], self.tmp.name)

    def test_real_corpus_loads_and_ranks(self):
        out = rr.retrieve("two authors ampersand and", 3, {})
        self.assertIn("two to five authors", out[0]["section"])


if __name__ == "__main__":
    unittest.main()
