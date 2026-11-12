"""Effective-formatting inspection for DOCX files.

Word stores most formatting in styles rather than on runs, so reading only
`run.font.name` misses it. This module resolves each run/paragraph through:
direct formatting -> character style -> paragraph style chain -> document defaults
(-> theme fonts). Measurements are weighted by character count over *body* text only
(headings, short lines, front matter and the reference list are excluded).
"""
import re
from collections import Counter
from lxml import etree

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"


def _q(tag):
    return "{%s}%s" % (W, tag)


def _style_chain(style):
    seen = set()
    while style is not None and id(style) not in seen:
        seen.add(id(style))
        yield style
        style = style.base_style


def _defaults(doc):
    root = doc.styles.element
    dd = root.find(_q("docDefaults"))
    rpr = ppr = None
    if dd is not None:
        r = dd.find(_q("rPrDefault"))
        p = dd.find(_q("pPrDefault"))
        rpr = r.find(_q("rPr")) if r is not None else None
        ppr = p.find(_q("pPr")) if p is not None else None
    return rpr, ppr


def _theme_fonts(doc):
    try:
        from docx.opc.constants import RELATIONSHIP_TYPE as RT
        part = doc.part.part_related_by(RT.THEME)
        root = etree.fromstring(part.blob)
        ns = {"a": A}
        minor = root.find(".//a:minorFont/a:latin", ns).get("typeface")
        major = root.find(".//a:majorFont/a:latin", ns).get("typeface")
        return {"minor": minor, "major": major}
    except Exception:
        return {}


def _font_from(chain, theme):
    for rpr in chain:
        rf = rpr.find(_q("rFonts")) if rpr is not None else None
        if rf is None:
            continue
        name = rf.get(_q("ascii")) or rf.get(_q("hAnsi"))
        if name:
            return name
        th = rf.get(_q("asciiTheme")) or rf.get(_q("hAnsiTheme"))
        if th:
            return theme.get("minor" if th.startswith("minor") else "major")
    return None


def _size_from(chain):
    for rpr in chain:
        sz = rpr.find(_q("sz")) if rpr is not None else None
        if sz is not None and sz.get(_q("val")):
            return int(sz.get(_q("val"))) / 2.0
    return None


def _spacing_from(chain):
    for ppr in chain:
        sp = ppr.find(_q("spacing")) if ppr is not None else None
        if sp is not None and sp.get(_q("line")):
            line = int(sp.get(_q("line")))
            rule = sp.get(_q("lineRule")) or "auto"
            if rule == "auto":
                return "%.2f" % (line / 240.0)
            return "%s:%.0fpt" % (rule, line / 20.0)
    return None


def _is_heading_like(paragraph, text):
    style = (paragraph.style.name if paragraph.style else "").lower()
    if style.startswith(("heading", "title", "toc", "caption", "header", "footer")):
        return True
    runs = [r for r in paragraph.runs if r.text.strip()]
    return bool(runs) and all(r.bold for r in runs) and len(text) < 110 and not text.endswith(".")


def _page_field_info(hf):
    """Find the paragraph holding a PAGE field; report its alignment and whether tabs position it."""
    from docx.text.paragraph import Paragraph
    # iterate every <w:p>, including those inside content controls (<w:sdt>) that hf.paragraphs skips
    for el in hf._element.iter(_q("p")):
        para = Paragraph(el, hf)
        has = any(re.search(r"\bPAGE\b", (n.text or "").upper()) for n in el.iter(_q("instrText"))) or \
            any(re.search(r"\bPAGE\b", (n.get(_q("instr")) or "").upper()) for n in el.iter(_q("fldSimple")))
        if not has:
            continue
        align = para.alignment
        if align is None and para.style is not None:
            for st in _style_chain(para.style):
                if st.paragraph_format.alignment is not None:
                    align = st.paragraph_format.alignment
                    break
        name = str(getattr(align, "name", align) or "left").lower()
        return {"align": name, "tabbed": el.find(".//" + _q("tab")) is not None}
    return None


def inspect_sections(doc):
    sections, prev = [], {"where": None, "align": None, "tabbed": False}
    for i, s in enumerate(doc.sections):
        pg = s._sectPr.find(_q("pgNumType"))
        fmt = (pg.get(_q("fmt")) if pg is not None else None) or "decimal"
        found = None
        for where, hf in (("footer", s.footer), ("header", s.header)):
            if hf.is_linked_to_previous:
                continue
            info = _page_field_info(hf)
            if info and found is None:
                found = dict(info, where=where)
        both_linked = s.footer.is_linked_to_previous and s.header.is_linked_to_previous
        field = prev if (found is None and both_linked) else (found or {"where": None, "align": None, "tabbed": False})
        prev = field
        sections.append({
            "index": i,
            "margins_inches": {"top": round(s.top_margin.inches, 2), "bottom": round(s.bottom_margin.inches, 2),
                               "left": round(s.left_margin.inches, 2), "right": round(s.right_margin.inches, 2)},
            "paper_size_inches": {"width": round(s.page_width.inches, 2), "height": round(s.page_height.inches, 2)},
            "page_number_format": fmt, "has_page_field": field["where"] is not None,
            "page_field_where": field["where"], "page_field_align": field["align"], "page_field_tabbed": field["tabbed"],
        })
    return sections


def measure_body_formatting(doc, body_range):
    """body_range = (first_paragraph_index, last_paragraph_index_exclusive) or None for all."""
    d_rpr, d_ppr = _defaults(doc)
    theme = _theme_fonts(doc)
    fonts, sizes, spacing = Counter(), Counter(), Counter()
    unresolved = 0
    for idx, p in enumerate(doc.paragraphs):
        if body_range and not (body_range[0] <= idx < body_range[1]):
            continue
        text = p.text.strip()
        if len(text) < 40 or _is_heading_like(p, text):
            continue
        p_chain = list(_style_chain(p.style)) if p.style is not None else []
        p_rprs = [s.element.rPr for s in p_chain]
        p_pprs = [p._p.pPr] + [s.element.pPr for s in p_chain] + [d_ppr]
        sp = _spacing_from(p_pprs) or "1.00"
        spacing[sp] += len(text)
        for run in p.runs:
            n = len(run.text.strip())
            if not n:
                continue
            try:
                c_rprs = [s.element.rPr for s in _style_chain(run.style)]
            except Exception:
                c_rprs = []
            chain = [run._r.rPr] + c_rprs + p_rprs + [d_rpr]
            name = _font_from(chain, theme)
            if name is None:
                unresolved += n
            else:
                fonts[name] += n
            sizes["%.1f" % (_size_from(chain) or 10.0)] += n
    return {"fonts": dict(fonts), "sizes": dict(sizes), "spacing": dict(spacing),
            "unresolved_font_chars": unresolved,
            "body_chars": sum(fonts.values()) + unresolved}


def paragraph_flags(paragraph):
    """Effective (bold, italic, alignment) of a paragraph: direct run formatting, else its style chain."""
    chain = list(_style_chain(paragraph.style)) if paragraph.style is not None else []
    runs = [r for r in paragraph.runs if r.text.strip()]

    def eff(attr):
        if not runs:
            return False
        for r in runs:
            v = getattr(r, attr)
            if v is None:
                v = next((getattr(st.font, attr) for st in chain if getattr(st.font, attr) is not None), None)
            if not v:
                return False
        return True

    align = paragraph.alignment
    if align is None:
        align = next((st.paragraph_format.alignment for st in chain if st.paragraph_format.alignment is not None), None)
    return eff("bold"), eff("italic"), str(getattr(align, "name", align) or "left").lower()
