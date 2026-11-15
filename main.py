#!/usr/bin/env python3
"""
PDF Localizer – Fully Local, Strong Protection, Single User

Usage:
    python main.py <input.pdf> <target_language> [--output <out.pdf>]

Example:
    python main.py "Laptop bill document.pdf" telugu
    python main.py invoice.pdf hindi --output localized_hindi.pdf
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from extractor import extract_document, print_extraction_summary
from protector import protect_document, print_protection_report
from translator import LocalTranslator, translate_protected_document, LANG_CODE_MAP
from generator import (
    generate_localized_pdf,
    validate_protected_values,
    print_validation_report,
)


def run_pipeline(
    input_pdf: str,
    target_lang: str,
    output_pdf: str | None = None,
    skip_translation: bool = False,
) -> str:
    """
    Full pipeline:
    1. Extract
    2. Protect
    3. Translate
    4. Generate + Validate
    """
    input_pdf = os.path.abspath(input_pdf)
    if not os.path.isfile(input_pdf):
        raise FileNotFoundError(f"Input PDF not found: {input_pdf}")

    if output_pdf is None:
        stem = Path(input_pdf).stem
        output_pdf = str(Path(input_pdf).with_name(f"{stem}_localized_{target_lang}.pdf"))

    print("\n" + "=" * 70)
    print("PDF LOCALIZER – Starting Pipeline")
    print("=" * 70)
    print(f"Input  : {input_pdf}")
    print(f"Language: {target_lang}")
    print(f"Output : {output_pdf}")
    print()

    # ------------------------------------------------------------------
    # Step 1: Extraction
    # ------------------------------------------------------------------
    print("[1/4] Extracting text and layout ...")
    t0 = time.time()
    document = extract_document(input_pdf)
    print(f"      Done in {time.time() - t0:.2f}s – {sum(len(p.spans) for p in document.pages)} spans found")

    # ------------------------------------------------------------------
    # Step 2: Protection
    # ------------------------------------------------------------------
    print("[2/4] Protecting dynamic values ...")
    t0 = time.time()
    protection = protect_document(document)
    print(f"      Done in {time.time() - t0:.2f}s")
    print_protection_report(protection)

    # ------------------------------------------------------------------
    # Step 3: Translation
    # ------------------------------------------------------------------
    if skip_translation:
        print("[3/4] Skipping translation (test mode)")
        translated_map = {
            ps.original_span.span_id: ps.original_span.text
            for ps in protection.protected_spans
        }
    else:
        print("[3/4] Translating static text ...")
        t0 = time.time()
        translator = LocalTranslator(device="cpu")
        translator.load()
        translated_map = translate_protected_document(
            protection, target_lang, translator
        )
        print(f"      Done in {time.time() - t0:.2f}s")

    # ------------------------------------------------------------------
    # Step 4: Generation + Validation
    # ------------------------------------------------------------------
    print("[4/4] Generating localized PDF ...")
    t0 = time.time()
    generate_localized_pdf(
        original_pdf_path=input_pdf,
        protection_result=protection,
        translated_map=translated_map,
        output_path=output_pdf,
        target_lang=target_lang,
    )
    print(f"      PDF written in {time.time() - t0:.2f}s → {output_pdf}")

    print("\nValidating protected values ...")
    success, missing = validate_protected_values(
        original_pdf_path=input_pdf,
        localized_pdf_path=output_pdf,
        value_map=protection.value_map,
    )
    print_validation_report(success, missing)

    if success:
        print("\n✓ Pipeline completed successfully.")
    else:
        print("\n⚠ Pipeline completed with validation warnings.")

    return output_pdf


def main():
    parser = argparse.ArgumentParser(
        description="Fully local PDF Localizer for Indian regional languages"
    )
    parser.add_argument("input_pdf", help="Path to the source PDF")
    parser.add_argument(
        "language",
        help=f"Target language. Supported: {', '.join(sorted(set(LANG_CODE_MAP.keys())))}",
    )
    parser.add_argument(
        "--output", "-o",
        help="Output PDF path (optional)",
    )
    parser.add_argument(
        "--skip-translation",
        action="store_true",
        help="Skip actual translation (useful for testing protection + generation)",
    )
    parser.add_argument(
        "--show-extract",
        action="store_true",
        help="Print detailed extraction summary",
    )

    args = parser.parse_args()

    # Validate language early
    if args.language.lower() not in LANG_CODE_MAP:
        print(f"Error: Unsupported language '{args.language}'")
        print(f"Supported: {', '.join(sorted(set(LANG_CODE_MAP.keys())))}")
        sys.exit(1)

    try:
        run_pipeline(
            input_pdf=args.input_pdf,
            target_lang=args.language,
            output_pdf=args.output,
            skip_translation=args.skip_translation,
        )
    except Exception as e:
        print(f"\n[ERROR] {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
