"""
Layer 1: PDF Text Extraction
Extracts all text spans with positions, font info using PyMuPDF.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional
import fitz  # PyMuPDF


@dataclass
class TextSpan:
    text: str
    bbox: tuple[float, float, float, float]  # x0, y0, x1, y1
    page_num: int
    font_size: float
    font_name: str
    color: int
    span_id: str


@dataclass
class PageData:
    page_num: int
    width: float
    height: float
    spans: List[TextSpan] = field(default_factory=list)


@dataclass
class DocumentData:
    source_path: str
    page_count: int
    pages: List[PageData] = field(default_factory=list)


def extract_document(pdf_path: str) -> DocumentData:
    """
    Extract all text spans with layout information from a PDF.
    """
    doc = fitz.open(pdf_path)
    document = DocumentData(source_path=pdf_path, page_count=len(doc))

    for page_idx, page in enumerate(doc):
        page_data = PageData(
            page_num=page_idx,
            width=page.rect.width,
            height=page.rect.height,
        )

        # Use "dict" mode for rich span information
        text_dict = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)

        span_counter = 0
        for block in text_dict.get("blocks", []):
            if block.get("type") != 0:  # only text blocks
                continue

            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    text = span.get("text", "").strip()
                    if not text:
                        continue

                    bbox = tuple(span.get("bbox", (0, 0, 0, 0)))
                    font_size = float(span.get("size", 10.0))
                    font_name = span.get("font", "unknown")
                    color = int(span.get("color", 0))

                    span_id = f"p{page_idx}_s{span_counter}"
                    span_counter += 1

                    page_data.spans.append(
                        TextSpan(
                            text=text,
                            bbox=bbox,
                            page_num=page_idx,
                            font_size=font_size,
                            font_name=font_name,
                            color=color,
                            span_id=span_id,
                        )
                    )

        document.pages.append(page_data)

    doc.close()
    return document


def print_extraction_summary(document: DocumentData) -> None:
    """Helper to inspect extraction results."""
    print(f"Document: {document.source_path}")
    print(f"Pages: {document.page_count}")
    total_spans = sum(len(p.spans) for p in document.pages)
    print(f"Total text spans: {total_spans}")
    print("-" * 60)
    for page in document.pages:
        print(f"\n--- Page {page.page_num + 1} ({len(page.spans)} spans) ---")
        for span in page.spans[:40]:  # limit for readability
            print(f"  [{span.span_id}] ({span.font_size:.1f}pt) {span.text[:80]}")
        if len(page.spans) > 40:
            print(f"  ... and {len(page.spans) - 40} more spans")
