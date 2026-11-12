import re
from collections import Counter
from pathlib import Path
from docx import Document
import pdfplumber

from .structure import analyse_structure, NUMBER_PREFIX_RE
from .citations import extract_citations, split_reference_entries
from .docx_inspector import inspect_sections, measure_body_formatting, paragraph_flags

HEADING_RE = re.compile(r"^(CHAPTER\s+[IVX0-9A-Z]+|\d+(?:\.\d+)*\s+\S)", re.I)
# Metric-compatible substitutes that PDF exporters use when Times New Roman is absent.
FONT_ALIASES = {"liberation serif": "Times New Roman", "tinos": "Times New Roman", "nimbus roman": "Times New Roman",
                "times": "Times New Roman"}


def parse_document(path):
    ext = Path(path).suffix.lower()
    if ext == ".docx":
        return _parse_docx(path)
    if ext == ".pdf":
        return _parse_pdf(path)
    raise ValueError("Only DOCX and PDF files are supported.")


def _assemble(file_type, lines, line_meta, formatting, joined_refs):
    """Common post-processing once we have ordered text lines."""
    st = analyse_structure(lines)
    chapters = []
    for c in st["chapters"]:
        chapters.append({**c, "text": "\n".join(lines[c["start"]:c["end"]])})
    refs_range = st["references"]
    references = split_reference_entries(lines[refs_range[0] + 1:refs_range[1]], joined_refs) if refs_range else []
    body_end = refs_range[0] if refs_range else len(lines)
    body_start = st["front_end"] if st["has_chapters"] else 0
    citations = extract_citations("\n".join(lines[body_start:body_end]))
    numbers = [c["number"] for c in chapters]
    return {"file_type": file_type, "text": "\n".join(lines), "lines": lines, "line_meta": line_meta,
            "structure": st, "chapters": chapters, "front_matter": lines[:st["front_end"]] if st["has_chapters"] else [],
            "references": references, "has_reference_heading": refs_range is not None,
            "citation_items": citations, "citations": [c["raw"] for c in citations],
            "partial_draft": not numbers or max(numbers) < 5, "formatting": formatting}


def _parse_docx(path):
    doc = Document(path)
    paragraphs, headings = [], []
    for idx, p in enumerate(doc.paragraphs):
        text = p.text.strip()
        if not text:
            continue
        style = p.style.name if p.style else "Normal"
        ppr = p._p.pPr
        numbered = ppr is not None and ppr.numPr is not None
        meta = {"index": idx, "text": text, "style": style, "numbered": numbered}
        if len(text) < 140:   # formatting flags are only needed for heading-length lines
            meta["bold"], meta["italic"], meta["align"] = paragraph_flags(p)
        paragraphs.append(meta)
        runs = [r for r in p.runs if r.text.strip()]
        bold_short = bool(runs) and all(r.bold for r in runs) and len(text) < 110 and not text.endswith(".")
        if style.lower().startswith("heading") or HEADING_RE.match(text) or (numbered and bold_short):
            headings.append({"text": text, "style": style, "paragraph_index": idx, "auto_numbered": numbered and not NUMBER_PREFIX_RE.match(text)})
    lines = [p["text"] for p in paragraphs]
    parsed = _assemble("docx", lines, paragraphs, {}, joined_refs=False)
    st = parsed["structure"]
    body_range = None
    if st["has_chapters"]:
        first = paragraphs[st["chapters"][0]["start"]]["index"]
        end = paragraphs[st["references"][0]]["index"] if st["references"] else len(doc.paragraphs)
        body_range = (first, end)
    sections = inspect_sections(doc)
    parsed["formatting"] = {"body": measure_body_formatting(doc, body_range), "sections": sections,
                            "section_count": len(sections), "measured_body_only": body_range is not None}
    parsed.update({"paragraphs": paragraphs, "headings": headings})
    return parsed


def pdf_font_family(fontname):
    name = (fontname or "").split("+")[-1]
    name = re.split(r"[-,]", name)[0]
    name = re.sub(r"(PSMT|PS|MT)$", "", name)
    name = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", name).strip()
    return FONT_ALIASES.get(name.lower(), name)


def _parse_pdf(path):
    lines, line_pages, page_chars = [], [], []
    page_sizes = []
    with pdfplumber.open(path) as pdf:
        for i, page in enumerate(pdf.pages):
            for line in (page.extract_text() or "").splitlines():
                if line.strip():
                    lines.append(line.strip())
                    line_pages.append(i)
            page_chars.append([(c.get("fontname"), c.get("size"), c.get("text", "")) for c in page.chars])
            page_sizes.append((round(page.width / 72, 2), round(page.height / 72, 2)))
    meta = [{"index": i, "page": line_pages[i]} for i in range(len(lines))]
    parsed = _assemble("pdf", lines, meta, {}, joined_refs=True)
    st = parsed["structure"]
    first_page, last_page = 0, len(page_chars)
    if st["has_chapters"]:
        first_page = line_pages[st["chapters"][0]["start"]]
        if st["references"]:
            last_page = line_pages[st["references"][0]]
    fonts, sizes = Counter(), Counter()
    for chars in page_chars[first_page:last_page + 1]:
        for fontname, size, ch in chars:
            if ch.strip() and size:
                fonts[pdf_font_family(fontname)] += 1
                sizes["%.1f" % (round(size * 2) / 2)] += 1
    w, h = page_sizes[0] if page_sizes else (0, 0)
    parsed["formatting"] = {
        "body": {"fonts": dict(fonts), "sizes": dict(sizes), "spacing": {}, "unresolved_font_chars": 0,
                 "body_chars": sum(fonts.values())},
        "sections": [{"index": 0, "margins_inches": None, "paper_size_inches": {"width": w, "height": h},
                      "page_number_format": None, "has_page_field": None}],
        "section_count": 1, "measured_body_only": st["has_chapters"],
        "note": "PDF: fonts, sizes and paper size are measured; margins, line spacing and page-number style are not."}
    parsed["headings"] = [{"text": l, "style": "pdf-detected", "paragraph_index": i}
                          for i, l in enumerate(lines) if HEADING_RE.match(l) and len(l) < 140]
    parsed["pages"] = len(page_sizes)
    return parsed
