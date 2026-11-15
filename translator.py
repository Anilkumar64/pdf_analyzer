"""
Layer 3: Local Translation using NLLB-200 (ungated, no login needed)

Translates only the safe (non-protected) text.
Fully local after the one-time model download.
"""

from __future__ import annotations

from typing import Dict, List, Optional
from protector import ProtectionResult


# NLLB-200 language codes
LANG_CODE_MAP = {
    "hindi": "hin_Deva",
    "hi": "hin_Deva",
    "telugu": "tel_Telu",
    "te": "tel_Telu",
    "marathi": "mar_Deva",
    "mr": "mar_Deva",
    "tamil": "tam_Taml",
    "ta": "tam_Taml",
    "kannada": "kan_Knda",
    "kn": "kan_Knda",
    "malayalam": "mal_Mlym",
    "ml": "mal_Mlym",
    "bengali": "ben_Beng",
    "bn": "ben_Beng",
    "gujarati": "guj_Gujr",
    "gu": "guj_Gujr",
    "punjabi": "pan_Guru",
    "pa": "pan_Guru",
    "english": "eng_Latn",
    "en": "eng_Latn",
}


class LocalTranslator:
    """NLLB-200 wrapper. Loads once. Fails loudly if the model can't load."""

    def __init__(self, model_name: str = "facebook/nllb-200-distilled-600M", device: str = "cpu"):
        self.model_name = model_name
        self.device = device
        self.model = None
        self.tokenizer = None

    def load(self) -> None:
        if self.model is not None:
            return
        from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

        print(f"Loading translation model: {self.model_name} ...")
        self.tokenizer = AutoTokenizer.from_pretrained(self.model_name, src_lang="eng_Latn")
        self.model = (
            AutoModelForSeq2SeqLM.from_pretrained(self.model_name)
            .to(self.device)
            .eval()
        )
        print("Translation model loaded successfully.")

    def translate(self, texts: List[str], target_lang: str) -> List[str]:
        if not texts:
            return []

        code = LANG_CODE_MAP.get(target_lang.lower().strip())
        if not code:
            raise ValueError(
                f"Unsupported language: {target_lang}. Supported: {list(LANG_CODE_MAP.keys())}"
            )

        import torch

        self.load()
        bos_id = self.tokenizer.convert_tokens_to_ids(code)

        results: List[str] = []
        batch_size = 8
        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            inputs = self.tokenizer(
                batch,
                return_tensors="pt",
                padding=True,
                truncation=True,
                max_length=256,
            ).to(self.device)

            with torch.no_grad():
                generated = self.model.generate(
                    **inputs,
                    forced_bos_token_id=bos_id,
                    num_beams=4,
                    max_length=256,
                )

            results.extend(
                self.tokenizer.batch_decode(generated, skip_special_tokens=True)
            )

        return results


def translate_protected_document(
    protection_result: ProtectionResult,
    target_lang: str,
    translator: Optional[LocalTranslator] = None,
) -> Dict[str, str]:
    """
    Translate only the static parts of protected spans.
    Returns span_id -> final text (original values restored).
    If a placeholder gets lost or mangled by the model, the span keeps its
    original English text so a dynamic value is never lost.
    """
    if translator is None:
        translator = LocalTranslator()
        translator.load()

    spans = protection_result.protected_spans
    value_map = protection_result.value_map

    to_translate: List[str] = []
    index_to_pos: Dict[int, int] = {}  # protected_spans index -> position in to_translate

    for idx, ps in enumerate(spans):
        if all(p.is_protected for p in ps.parts):
            continue
        index_to_pos[idx] = len(to_translate)
        to_translate.append(ps.final_text_for_translation)

    print(f"Translating {len(to_translate)} text segments to {target_lang} ...")

    translated_texts = translator.translate(to_translate, target_lang) if to_translate else []

    translated_map: Dict[str, str] = {}
    fallback_count = 0

    for idx, ps in enumerate(spans):
        span_id = ps.original_span.span_id

        if idx not in index_to_pos:
            translated_map[span_id] = ps.original_span.text
            continue

        translated = translated_texts[index_to_pos[idx]]

        # Guard: every placeholder must survive translation exactly once
        expected = [p.placeholder for p in ps.parts if p.is_protected]
        if not all(translated.count(ph) == 1 for ph in expected):
            translated_map[span_id] = ps.original_span.text
            fallback_count += 1
            continue

        # Restore only the placeholders that belong to this span
        for ph in expected:
            translated = translated.replace(ph, value_map[ph])
        translated_map[span_id] = translated

    if fallback_count:
        print(f"[WARN] {fallback_count} spans kept in English (placeholder mangled by model)")

    return translated_map