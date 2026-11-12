"""Heading format, style, acronym and language-engine tests (no database, no network, libraries faked)."""
import sys
import tempfile
import types
import unittest
from pathlib import Path

from app.nlp import linguistics
from app.nlp.document_parser import parse_document
from app.nlp.rule_engine import run_compliance_checks, load_rules, title_case_violations, looks_title_case, summarise_languagetool
from tests.test_nlp import make_docx, LONG

BOLD = {"bold": True}
CENTRE = {"bold": True, "center": True}


def run(blocks, **kw):
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "t.docx"
        make_docx(path, blocks, **kw)
        return run_compliance_checks(parse_document(str(path)), load_rules())


def keys(f):
    return {x["rule_key"] for x in f}


def chapter(n, word, title, extra=()):
    return [("CHAPTER %s" % word, CENTRE), (title, CENTRE), LONG * 2] + list(extra)


class HeadingFormat(unittest.TestCase):
    def test_title_case_helpers(self):
        self.assertEqual(title_case_violations("Review of related works"), ["related", "works"])
        self.assertEqual(title_case_violations("review of Related Works"), ["review"])
        self.assertEqual(title_case_violations("Review of Related Works"), [])
        self.assertTrue(looks_title_case("Summary Of Empirical Studies"))
        self.assertFalse(looks_title_case("Summary of empirical studies"))
        self.assertFalse(looks_title_case("Rule-based checker for UNIZIK"))

    def test_correct_headings_pass(self):
        f = run(chapter(1, "ONE", "INTRODUCTION", [("1.1 Background to the Study", BOLD), LONG,
                                                    ("1.1.1 Effect of moulding water", BOLD), LONG,
                                                    ("1.1.1.1 Soil water curves", {"bold": True, "italic": True}), LONG]))
        self.assertNotIn("FMT-007", keys(f))

    def test_wrong_heading_level_is_flagged(self):          # thesis test T-04
        blocks = chapter(1, "ONE", "INTRODUCTION", [("1.1 Background to the Study", BOLD), LONG,
                                                     ("1.1.1 Effect Of Moulding Water", BOLD), LONG])   # level 3 in Title Case
        found = [x for x in run(blocks) if x["rule_key"] == "FMT-007"]
        self.assertEqual(len(found), 1)
        self.assertIn("capitalise only the first word", found[0]["suggestion"])

    def test_other_heading_problems(self):
        blocks = chapter(1, "ONE", "INTRODUCTION", [("1.1 Background to the study", BOLD), LONG,        # level 2 not Title Case
                                                     ("1.2 Statement of the Problem", {}), LONG,           # not bold
                                                     ("1.2.1 Detail", BOLD), LONG,
                                                     ("1.2.1.1 Deeper", BOLD), LONG,                       # level 4 not italic
                                                     ("1.2.1.1.1 Too deep", {"bold": True, "italic": True}), LONG])   # level 5
        text = " ".join(x["suggestion"] for x in run(blocks) if x["rule_key"] == "FMT-007")
        for expected in ("Title Case", "is not bold", "italic", "at most four levels"):
            self.assertIn(expected, text)

    def test_chapter_heading_must_be_centred_bold_uppercase(self):
        blocks = [("CHAPTER ONE", BOLD), ("Introduction", BOLD), LONG * 2]     # left-aligned, title not uppercase
        text = " ".join(x["suggestion"] for x in run(blocks) if x["rule_key"] == "FMT-007")
        self.assertIn("not centred", text)
        self.assertIn("not in UPPERCASE", text)


class StyleRules(unittest.TestCase):
    def found(self, key, sentence):
        return [x for x in run(chapter(1, "ONE", "INTRODUCTION", [sentence + " " + LONG])) if x["rule_key"] == key]

    def test_latin_abbreviations(self):
        self.assertTrue(self.found("STY-001", "Tools such as parsers, e.g. regex engines, help."))
        self.assertTrue(self.found("STY-001", "Parsers, rules, etc. are used."))
        self.assertEqual(self.found("STY-001", "Smith et al. (2020) disagree."), [])    # et al. is permitted

    def test_numeral_style(self):
        self.assertTrue(self.found("STY-002", "The meeting held on 15th December, 2005 was long."))
        self.assertTrue(self.found("STY-002", "It finished 5th in the ranking."))
        self.assertTrue(self.found("STY-002", "Growth in the 1990's was steady."))
        self.assertTrue(self.found("STY-002", "About ninety % agreed."))
        self.assertEqual(self.found("STY-002", "The 50th percentile and 90% were reported."), [])

    def test_third_person_only_checked_in_chapter_four(self):
        blocks = chapter(1, "ONE", "INTRODUCTION", ["We built it. " + LONG]) + chapter(4, "FOUR", "RESULTS", ["We tested the system. " + LONG])
        found = [x for x in run(blocks) if x["rule_key"] == "STY-003"]
        self.assertEqual(len(found), 1)
        self.assertIn("Chapter 4", found[0]["location"])
        ok = chapter(4, "FOUR", "RESULTS", ["The researcher tested the system. My Reports lists items. " + LONG])
        self.assertNotIn("STY-003", keys(run(chapter(1, "ONE", "X") + ok)))      # 'My Reports' is a UI label

    def test_acronyms(self):
        text = ("Natural Language Processing (NLP) is used. NLP is useful. The tool reads DOCX files. "
                "Optical Character Recognition (OCR) helps. TLS (Transport Layer Security) is on. ")
        flagged = {x["location"] for x in run(chapter(1, "ONE", "INTRODUCTION", [text + LONG])) if x["rule_key"] == "ABBR-001"}
        self.assertEqual(flagged, {"Acronym: DOCX"})       # NLP, OCR and TLS are spelled out correctly

    def test_missing_methodology_chapter_in_full_draft_is_high(self):     # thesis test T-07
        blocks = []
        for n, (w, t) in {1: ("ONE", "INTRODUCTION"), 2: ("TWO", "LITERATURE REVIEW"), 4: ("FOUR", "DESIGN"), 5: ("FIVE", "SUMMARY")}.items():
            blocks += chapter(n, w, t)
        ch3 = [x for x in run(blocks) if x["rule_key"] == "CS-CH3-001"]
        self.assertEqual((len(ch3), ch3[0]["severity"]), (1, "high"))


class LanguageToolSummary(unittest.TestCase):
    def test_grouping_and_filters(self):
        text = "The colour was bad. The color of Kano and NLP2 and UNIZIK mattered. They was late. colour colour"
        def m(word, rid, cat="TYPOS", msg="msg", repl=None):
            i = text.index(word)
            return {"rule_id": rid, "category": cat, "message": msg, "offset": i, "length": len(word), "replacements": repl or [], "context": text}
        matches = [m("color", "MORFOLOGIK_RULE_EN_GB", repl=["colour"]), m("Kano", "MORFOLOGIK_RULE_EN_GB"),
                   m("NLP2", "MORFOLOGIK_RULE_EN_GB"), m("They was", "HE_VERB_AGR", "GRAMMAR", "Agreement"),
                   m("bad", "STYLE_RULE", "STYLE")]
        spell, grammar = summarise_languagetool(matches, text)
        self.assertEqual(list(spell), ["color"])          # proper noun, identifier with digit and style rule ignored
        self.assertEqual(len(grammar), 1)

    def test_rule_returns_nothing_when_engine_unavailable(self):
        original = linguistics.languagetool_matches
        linguistics.languagetool_matches = lambda text: None
        try:
            self.assertNotIn("LANG-001", keys(run(chapter(1, "ONE", "INTRODUCTION"))))
        finally:
            linguistics.languagetool_matches = original

    def test_rule_reports_when_engine_available(self):
        original = linguistics.languagetool_matches
        def fake(text):
            i = text.index("body")
            return [{"rule_id": "MORFOLOGik_RULE_EN_GB".upper(), "category": "TYPOS", "message": "m", "offset": i, "length": 4,
                     "replacements": ["bodies"], "context": "ctx"}]
        linguistics.languagetool_matches = fake
        try:
            found = [x for x in run(chapter(1, "ONE", "INTRODUCTION")) if x["rule_key"] == "LANG-001"]
            self.assertEqual(len(found), 1)
            self.assertIn("bodies", found[0]["suggestion"])
        finally:
            linguistics.languagetool_matches = original


class EngineGlue(unittest.TestCase):
    """The real libraries are not installed in every environment, so verify OUR glue with fakes and the fallbacks without."""
    def setUp(self):
        self._saved = {k: sys.modules.get(k) for k in ("spacy", "nltk", "nltk.stem", "language_tool_python")}
        linguistics._S.update({"nlp": None, "nlp_tried": False, "stem": None, "stem_tried": False, "lt": None, "lt_tried": False, "cfg": {}})

    def tearDown(self):
        for k, v in self._saved.items():
            sys.modules.pop(k, None)
            if v is not None:
                sys.modules[k] = v
        linguistics._S.update({"nlp": None, "nlp_tried": False, "stem": None, "stem_tried": False, "lt": None, "lt_tried": False, "cfg": {}})

    def test_fallbacks_without_libraries(self):
        for k in ("spacy", "nltk", "nltk.stem", "language_tool_python"):
            sys.modules[k] = None          # makes `import k` raise ImportError
        self.assertEqual(linguistics.sentences("One two. Three four? Five."), ["One two.", "Three four?", "Five."])
        self.assertEqual(linguistics.word_tokens("It's a well-known test, 2024."), ["It's", "a", "well-known", "test", "2024"])
        self.assertEqual(linguistics.stem("running"), "running")
        self.assertIsNone(linguistics.languagetool_matches("text"))
        self.assertEqual(linguistics.engine_status()["spacy"], False)

    def test_spacy_glue(self):
        class Tok:
            def __init__(s, t): s.text, s.is_alpha, s.like_num = t, t.isalpha(), t.isdigit()
        class Sent:
            def __init__(s, t): s.text = t
        class Nlp:
            max_length = 0
            def add_pipe(s, name): s.pipe = name
            def __call__(s, text): return types.SimpleNamespace(sents=[Sent("SPACY:" + text)])
            def make_doc(s, text): return [Tok("alpha"), Tok(","), Tok("42")]
        sys.modules["spacy"] = types.SimpleNamespace(blank=lambda lang: Nlp())
        self.assertEqual(linguistics.sentences("hello"), ["SPACY:hello"])
        self.assertEqual(linguistics.word_tokens("x"), ["alpha", "42"])      # punctuation dropped, numbers kept
        self.assertTrue(linguistics.engine_status()["spacy"])

    def test_nltk_stemmer_glue_and_retriever_uses_it(self):
        class Porter:
            def stem(self, w): return w[:4]
        sys.modules["nltk"] = types.SimpleNamespace()
        sys.modules["nltk.stem"] = types.SimpleNamespace(PorterStemmer=Porter)
        self.assertEqual(linguistics.stem("citations"), "cita")
        from app.nlp.rag_retriever import bm25_search
        corpus = [{"section": "x", "text": "citation rules for authors"}, {"section": "y", "text": "margins and paper"}]
        self.assertEqual(bm25_search("citations", corpus, 1)[0]["section"], "x")     # 'citations' and 'citation' share a stem

    def test_languagetool_glue(self):
        class Match:
            ruleId, category, message, offset, errorLength, replacements, context = "R", "GRAMMAR", "msg", 3, 4, ["a", "b", "c", "d"], "ctx"
        class Tool:
            def __init__(s, lang, remote_server=None): s.lang, s.remote = lang, remote_server
            def check(s, text): return [Match()]
        sys.modules["language_tool_python"] = types.SimpleNamespace(LanguageTool=Tool)
        self.assertIsNone(linguistics.languagetool_matches("text"))                  # disabled by default
        linguistics.configure({"LANGUAGETOOL_ENABLED": True, "LANGUAGETOOL_URL": "http://lt:8081"})
        out = linguistics.languagetool_matches("some text")
        self.assertEqual(out[0]["replacements"], ["a", "b", "c"])                    # capped at three suggestions
        self.assertEqual(linguistics._S["lt"].remote, "http://lt:8081")
        self.assertEqual(linguistics._S["lt"].lang, "en-GB")


if __name__ == "__main__":
    unittest.main()
