"""
Layer 4: PDF Generation + Validation
 
What this does (and the old version did not):
- TRUE redaction: old text is removed from the PDF, not just painted over.
  Images (logo, QR, stamp) and table lines are kept. No white boxes, so grey
  header bars stay grey.
- Exact span boxes only: nothing near a neighbouring value is ever touched.
- Mixed scripts: Latin runs (GSTIN, B2C, item codes, INR...) use a Latin font,
  Indic runs use the Noto font for the target language. No more tofu boxes.
- Fit-or-skip: font size is shrunk to fit the free room on the line (stopping
  at the next neighbour or table line). If it cannot fit, the ORIGINAL span is
  left untouched. A span is never blanked.
- Missing font = hard error with install instructions. No silent fallback.
- Validation checks the visible output: protected values present, old English
  gone from rewritten spans, no rewritten span left empty.
"""
 
from __future__ import annotations
 
import html
import os
import re
from typing import Dict, List, Optional, Tuple
 
import fitz  # PyMuPDF
 
from protector import ProtectionResult
 
 
_HERE = os.path.dirname(os.path.abspath(__file__))
_FONT_DIRS = [
    os.path.join(_HERE, "fonts"),
    "/usr/share/fonts/truetype/noto",
    "/usr/share/fonts/opentype/noto",
    "/usr/share/fonts/noto",
    "/usr/share/fonts",
    os.path.expanduser("~/.fonts"),
    os.path.expanduser("~/.local/share/fonts"),
]
 
_SCRIPT_STEM = {
    "telugu": "NotoSansTelugu", "te": "NotoSansTelugu",
    "hindi": "NotoSansDevanagari", "hi": "NotoSansDevanagari",
    "marathi": "NotoSansDevanagari", "mr": "NotoSansDevanagari",
    "tamil": "NotoSansTamil", "ta": "NotoSansTamil",
    "kannada": "NotoSansKannada", "kn": "NotoSansKannada",
    "malayalam": "NotoSansMalayalam", "ml": "NotoSansMalayalam",
    "bengali": "NotoSansBengali", "bn": "NotoSansBengali",
    "gujarati": "NotoSansGujarati", "gu": "NotoSansGujarati",
    "punjabi": "NotoSansGurmukhi", "pa": "NotoSansGurmukhi",
}
 
# Report of the last generate_localized_pdf() call (read by the validator)
LAST_REPORT: Dict[str, object] = {}
 
 
# ---------------------------------------------------------------- fonts ----
 
def _find_font(stem: str, weight: str) -> Optional[str]:
    name = f"{stem}-{weight}.ttf"
    for base in _FONT_DIRS:
        if not os.path.isdir(base):
            continue
        direct = os.path.join(base, name)
        if os.path.isfile(direct):
            return direct
        for root, _, files in os.walk(base):
            if name in files:
                return os.path.join(root, name)
    return None
 
 
class _FontSet:
    """Indic + Latin fonts (regular and bold) for one target language."""
 
    def __init__(self, lang: str):
        stem = _SCRIPT_STEM.get(lang.lower().strip())
        if stem is None:
            raise ValueError(f"No font mapping for language: {lang}")
 
        self.ind_reg = _find_font(stem, "Regular")
        if self.ind_reg is None:
            raise FileNotFoundError(
                f"{stem}-Regular.ttf not found.\n"
                f"  Ubuntu/Debian: sudo apt install fonts-noto-core\n"
                f"  or copy {stem}-Regular.ttf into {os.path.join(_HERE, 'fonts')}"
            )
        self.ind_bold = _find_font(stem, "Bold") or self.ind_reg
        self.lat_reg = _find_font("NotoSans", "Regular")
        self.lat_bold = _find_font("NotoSans", "Bold") or self.lat_reg
        if self.lat_reg is None:
            raise FileNotFoundError(
                "NotoSans-Regular.ttf (Latin font) not found.\n"
                "  Ubuntu/Debian: sudo apt install fonts-noto-core\n"
                f"  or copy it into {os.path.join(_HERE, 'fonts')}"
            )
 
        self._cache: Dict[str, fitz.Font] = {}
        self.archive = fitz.Archive()
        for d in {os.path.dirname(p) for p in
                  (self.ind_reg, self.ind_bold, self.lat_reg, self.lat_bold)}:
            self.archive.add(d)
 
    def font(self, path: str) -> fitz.Font:
        if path not in self._cache:
            self._cache[path] = fitz.Font(fontfile=path)
        return self._cache[path]
 
    def path(self, kind: str, bold: bool) -> str:
        if kind == "I":
            return self.ind_bold if bold else self.ind_reg
        return self.lat_bold if bold else self.lat_reg
 
    def css_faces(self) -> str:
        def face(fam, weight, p):
            return (f"@font-face {{font-family:{fam};font-weight:{weight};"
                    f"src:url({os.path.basename(p)});}}")
        return "".join([
            face("IND", "normal", self.ind_reg), face("IND", "bold", self.ind_bold),
            face("LAT", "normal", self.lat_reg), face("LAT", "bold", self.lat_bold),
        ])
 
 
def _split_runs(text: str) -> List[Tuple[str, str]]:
    """Split text into ('L'|'I', run) pieces. L = Latin/digits/punct, I = Indic."""
    runs: List[Tuple[str, str]] = []
    cur, buf = None, []
    for ch in text:
        o = ord(ch)
        if ch.isspace():
            kind = cur or "L"
        elif o < 0x0250 or 0x2000 <= o <= 0x206F or 0x20A0 <= o <= 0x20CF:
            kind = "L"
        else:
            kind = "I"
        if kind != cur and buf:
            runs.append((cur, "".join(buf)))
            buf = []
        cur = kind
        buf.append(ch)
    if buf:
        runs.append((cur, "".join(buf)))
    return runs
 
 
def _measure(runs, fonts: _FontSet, size: float, bold: bool) -> float:
    return sum(fonts.font(fonts.path(k, bold)).text_length(t, fontsize=size)
               for k, t in runs)
 
 
# ------------------------------------------------------------- geometry ----
 
def _v_overlap(a0: float, a1: float, b0: float, b1: float) -> float:
    return min(a1, b1) - max(a0, b0)
 
 
def _vertical_walls(page: fitz.Page) -> List[Tuple[float, float, float]]:
    """Thin vertical lines/rects (table borders): (x, y0, y1)."""
    walls = []
    try:
        drawings = page.get_drawings()
    except Exception:
        return walls
    for d in drawings:
        for item in d.get("items", []):
            kind = item[0]
            if kind == "l":
                p1, p2 = item[1], item[2]
                if abs(p1.x - p2.x) < 0.6 and abs(p1.y - p2.y) > 3:
                    walls.append((p1.x, min(p1.y, p2.y), max(p1.y, p2.y)))
            elif kind == "re":
                r = item[1]
                if r.width < 2.0 and r.height > 3:
                    walls.append((r.x0, r.y0, r.y1))
    return walls
 
 
def _right_limit(span, others, walls, page_right: float) -> float:
    """Right edge of the free room on this span's line."""
    x0, y0, x1, y1 = span.bbox
    h = y1 - y0
    limit = page_right
    for o in others:
        if o is span:
            continue
        ox0, oy0, ox1, oy1 = o.bbox
        if ox0 >= x1 - 1.0 and _v_overlap(y0, y1, oy0, oy1) > 0.3 * min(h, oy1 - oy0):
            limit = min(limit, ox0 - 1.5)
    for wx, wy0, wy1 in walls:
        if wx >= x1 - 0.5 and _v_overlap(y0, y1, wy0, wy1) > 2.0:
            limit = min(limit, wx - 1.5)
    return limit
 
 
def _redaction_rect(span, survivors) -> Optional[fitz.Rect]:
    """
    Redaction removes every character whose box touches the rectangle, and
    line boxes of neighbouring lines overlap vertically. So redact only a band
    of this span that no surviving neighbour touches. None = no safe band.
    """
    x0, y0, x1, y1 = span.bbox
    bx0, bx1 = x0 + 0.2, x1 - 0.2
    blocked = []
    for o in survivors:
        if o is span:
            continue
        ox0, oy0, ox1, oy1 = o.bbox
        if ox0 < bx1 and ox1 > bx0 and oy0 < y1 and oy1 > y0:
            blocked.append((oy0 - 0.05, oy1 + 0.05))
    blocked.sort()
    free, cur = [], y0
    for a, b in blocked:
        if a > cur:
            free.append((cur, min(a, y1)))
        cur = max(cur, b)
    if cur < y1:
        free.append((cur, y1))
    free = [(a, b) for a, b in free if b - a >= 1.0]
    if not free:
        return None
    a, b = max(free, key=lambda t: t[1] - t[0])
    return fitz.Rect(bx0, a, bx1, b)
 
 
# ------------------------------------------------------------ rendering ----
 
def _is_bold(font_name: str) -> bool:
    n = font_name.lower()
    return "bold" in n or "black" in n or "heavy" in n
 
 
def _hex(color: int) -> str:
    return "#%06x" % (int(color) & 0xFFFFFF)
 
 
def _draw_text(page, fonts: _FontSet, runs, x0, y0, y1, avail, size, bold, color) -> bool:
    """Draw one line of mixed-script text. True on success."""
    lh = size * 1.3
    top = (y0 + y1) / 2.0 - lh / 2.0
    rect = fitz.Rect(x0, top, x0 + avail + 2.0, top + lh + 1.0)
 
    if hasattr(page, "insert_htmlbox"):
        body = "".join(
            f'<span class="{k}">{html.escape(t)}</span>' for k, t in runs
        )
        css = (
            fonts.css_faces()
            + f"body{{margin:0;padding:0;font-size:{size:.2f}pt;line-height:1.3;"
              f"white-space:nowrap;color:{_hex(color)};"
              f"font-weight:{'bold' if bold else 'normal'};}}"
            + ".I{font-family:IND}.L{font-family:LAT}"
        )
        try:
            spare, _scale = page.insert_htmlbox(rect, body, css=css, archive=fonts.archive)
            if spare >= 0:
                return True
        except Exception:
            pass  # fall through to TextWriter
 
    # Fallback: TextWriter (no complex-script shaping, but works on old PyMuPDF)
    try:
        tw = fitz.TextWriter(page.rect, color=(0, 0, 0))
        x = x0
        baseline = (y0 + y1) / 2.0 + size * 0.33
        for k, t in runs:
            f = fonts.font(fonts.path(k, bold))
            tw.append((x, baseline), t, font=f, fontsize=size)
            x += f.text_length(t, fontsize=size)
        tw.write_text(page, color=tuple(((int(color) >> s) & 255) / 255 for s in (16, 8, 0)))
        return True
    except Exception:
        return False
 
 
# ----------------------------------------------------------------- main ----
 
def generate_localized_pdf(
    original_pdf_path: str,
    protection_result: ProtectionResult,
    translated_map: Dict[str, str],
    output_path: str,
    target_lang: str = "hindi",
) -> str:
    fonts = _FontSet(target_lang)
    doc = fitz.open(original_pdf_path)
 
    rewritten: List[dict] = []
    skipped: List[dict] = []
    unchanged = 0
 
    for page_idx, page in enumerate(doc):
        page_pss = [ps for ps in protection_result.protected_spans
                    if ps.original_span.page_num == page_idx]
        all_spans = [ps.original_span for ps in page_pss]
        walls = _vertical_walls(page)
        page_right = page.rect.x1 - 8.0
 
        plans = []
        for ps in page_pss:
            span = ps.original_span
            new_text = translated_map.get(span.span_id, span.text)
 
            if new_text.strip() == span.text.strip() or all(p.is_protected for p in ps.parts):
                unchanged += 1
                continue
 
            x0, y0, x1, y1 = span.bbox
            bold = _is_bold(span.font_name)
            runs = _split_runs(new_text.strip())
            avail = _right_limit(span, all_spans, walls, page_right) - x0
 
            size0 = max(4.5, min(span.font_size, 14.0))
            size_min = max(4.5, size0 * 0.6)
            size = size0
            chosen = None
            while size >= size_min - 1e-6:
                if _measure(runs, fonts, size, bold) * 1.03 <= avail:
                    chosen = size
                    break
                size -= 0.25
 
            if chosen is None:
                skipped.append({"span_id": span.span_id, "text": span.text,
                                "reason": "does not fit"})
                continue
 
            plans.append((span, new_text.strip(), runs, chosen, avail, bold))
 
        # 1) true redaction, only of bands no surviving neighbour touches
        bands: Dict[str, fitz.Rect] = {}
        while True:
            planned = {pl[0].span_id for pl in plans}
            survivors = [sp for sp in all_spans if sp.span_id not in planned]
            bad = []
            for pl in plans:
                band = _redaction_rect(pl[0], survivors)
                if band is None:
                    bad.append(pl)
                else:
                    bands[pl[0].span_id] = band
            if not bad:
                break
            for pl in bad:
                plans.remove(pl)
                skipped.append({"span_id": pl[0].span_id, "text": pl[0].text,
                                "reason": "overlaps neighbouring text"})
        for span, *_ in plans:
            page.add_redact_annot(bands[span.span_id], fill=False)
        if plans:
            try:
                page.apply_redactions(
                    images=fitz.PDF_REDACT_IMAGE_NONE,
                    graphics=fitz.PDF_REDACT_LINE_ART_NONE,
                )
            except TypeError:
                page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE)
 
        # 2) write translated text
        for span, new_text, runs, size, avail, bold in plans:
            x0, y0, x1, y1 = span.bbox
            ok = _draw_text(page, fonts, runs, x0, y0, y1, avail, size, bold, span.color)
            if ok:
                rewritten.append({
                    "span_id": span.span_id, "page": page_idx,
                    "bbox": (x0, y0, x1 + max(0.0, avail - (x1 - x0)), y1),
                    "original": span.text, "new": new_text,
                })
            else:
                # Text is already redacted; this must be visible, not silent.
                skipped.append({"span_id": span.span_id, "text": span.text,
                                "reason": "draw failed AFTER redaction"})
 
    os.makedirs(os.path.dirname(os.path.abspath(output_path)) or ".", exist_ok=True)
    doc.save(output_path, garbage=4, deflate=True)
    doc.close()
 
    LAST_REPORT.clear()
    LAST_REPORT.update({"output": os.path.abspath(output_path),
                        "rewritten": rewritten, "skipped": skipped})
 
    print(f"      Spans rewritten: {len(rewritten)}")
    print(f"      Spans unchanged (values / identical): {unchanged}")
    if skipped:
        print(f"      [WARN] Spans left in ORIGINAL language: {len(skipped)}")
        for s in skipped[:15]:
            print(f"        - {s['span_id']} {s['text'][:40]!r} ({s['reason']})")
        if len(skipped) > 15:
            print(f"        ... and {len(skipped) - 15} more")
    return output_path
 
 
# ----------------------------------------------------------- validation ----
 
def validate_protected_values(
    original_pdf_path: str,
    localized_pdf_path: str,
    value_map: Dict[str, str],
) -> Tuple[bool, List[str]]:
    """
    Check the VISIBLE output:
      1. every protected value occurs at least as often as in the original
      2. old English is gone from every rewritten span
      3. no rewritten span came out empty
    Returns (success, list_of_problems).
    """
    problems: List[str] = []
 
    orig = fitz.open(original_pdf_path)
    orig_text = "\n".join(p.get_text("text") for p in orig)
    orig.close()
 
    loc = fitz.open(localized_pdf_path)
    loc_text = "\n".join(p.get_text("text") for p in loc)
 
    for v in sorted(set(value_map.values())):
        pat = re.compile(r"(?<![A-Za-z0-9.])" + re.escape(v) + r"(?![A-Za-z0-9])")
        if len(pat.findall(loc_text)) < len(pat.findall(orig_text)):
            problems.append(f"protected value lost/altered: {v}")
 
    report = LAST_REPORT if LAST_REPORT.get("output") == os.path.abspath(localized_pdf_path) else {}
    for rec in report.get("rewritten", []):
        page = loc[rec["page"]]
        x0, y0, x1, y1 = rec["bbox"]
        clip = fitz.Rect(x0, y0 + 0.5, x1, y1 - 0.5)
        txt = page.get_text("text", clip=clip).strip()
        if not txt:
            problems.append(f"{rec['span_id']}: rewritten span is EMPTY ({rec['original']!r})")
        elif rec["original"].strip() in txt:
            problems.append(f"{rec['span_id']}: old text still present ({rec['original']!r})")
 
    for s in report.get("skipped", []):
        problems.append(f"{s['span_id']}: not translated ({s['reason']}): {s['text'][:40]!r}")
 
    loc.close()
    return len(problems) == 0, problems
 
 
def print_validation_report(success: bool, missing: List[str]) -> None:
    print("=" * 70)
    print("VALIDATION REPORT")
    print("=" * 70)
    if success:
        print("✓ Protected values intact, old text removed, no blank spans.")
    else:
        print("✗ Problems found:")
        for v in missing[:30]:
            print(f"  - {v}")
        if len(missing) > 30:
            print(f"  ... and {len(missing) - 30} more")
    print("=" * 70)
 
