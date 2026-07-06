"""Per-PDF OCR orchestration: at most one call per OCR backend per load session."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from logic.validation import clean_iban, is_plausible_iban
from parser.pdf_parser import (
    _scan_sepa_ibans_in_text,
    extract_text_force_raster_ocr,
    extract_text_from_images,
)


def has_reliable_text_layer_iban(data: dict[str, Any], pdf_text_layer: str) -> bool:
    """True when IBAN is confirmed/tentative and literally present in the PDF text layer."""
    ir = data.get("iban_result") if isinstance(data.get("iban_result"), dict) else {}
    status = str(ir.get("status") or "").strip()
    if status not in ("confirmed", "tentative"):
        return False
    iban = clean_iban(str(ir.get("value") or data.get("iban") or "").strip())
    if not iban or not is_plausible_iban(iban):
        return False
    text_ibans = {clean_iban(x) for x in _scan_sepa_ibans_in_text(pdf_text_layer or "")}
    return iban in text_ibans


def needs_supplement_ocr(data: dict[str, Any] | None, pdf_text_layer: str) -> bool:
    """Conservative: run supplement OCR when payment-critical fields are missing from text-only parse."""
    if not (pdf_text_layer or "").strip():
        return True
    if data is None:
        return True
    ar = data.get("amount_result") if isinstance(data.get("amount_result"), dict) else {}
    amount_status = str(ar.get("status") or "").strip()
    cand_count = len(ar.get("candidates") or [])
    if amount_status == "failed" or cand_count == 0:
        return True
    if not has_reliable_text_layer_iban(data, pdf_text_layer):
        return True
    return False


def merge_supplement_chunks(*chunks: str) -> str:
    """Dedupe-merge OCR chunks (same contract as extract_ocr_supplement_text)."""
    parts: list[str] = []
    seen: set[str] = set()
    for chunk in chunks:
        c = str(chunk or "").strip()
        if not c or c in seen:
            continue
        seen.add(c)
        parts.append(c)
    return "\n\n".join(parts)


@dataclass
class PdfOcrSession:
    """Lazy memoized OCR for a single PDF path during one load_invoice_from_pdf_path call."""

    path: str
    _image_text: str | None = field(default=None, repr=False)
    _raster_1: str | None = field(default=None, repr=False)
    _raster_2: str | None = field(default=None, repr=False)
    _image_loaded: bool = field(default=False, repr=False)
    _raster_1_loaded: bool = field(default=False, repr=False)
    _raster_2_loaded: bool = field(default=False, repr=False)

    @property
    def touched(self) -> bool:
        return self._image_loaded or self._raster_1_loaded or self._raster_2_loaded

    def image_text(self) -> str:
        if not self._image_loaded:
            self._image_text = extract_text_from_images(self.path) or ""
            self._image_loaded = True
        return self._image_text or ""

    def raster_text(self, *, max_pages: int = 1) -> str:
        page_limit = max(1, int(max_pages))
        if page_limit <= 1:
            if not self._raster_1_loaded:
                self._raster_1 = extract_text_force_raster_ocr(self.path, max_pages=1) or ""
                self._raster_1_loaded = True
            return self._raster_1 or ""
        if not self._raster_2_loaded:
            self._raster_2 = extract_text_force_raster_ocr(self.path, max_pages=2) or ""
            self._raster_2_loaded = True
        return self._raster_2 or ""

    def supplement_text(self) -> str:
        return merge_supplement_chunks(self.image_text(), self.raster_text(max_pages=1))

    def ibans_from_images(self) -> list[str]:
        """Same logic as parser.extract_ibans_from_images, using cached OCR text."""
        seen: set[str] = set()
        ordered: list[str] = []

        def _collect(text: str) -> None:
            for iban in _scan_sepa_ibans_in_text(text or ""):
                if iban not in seen:
                    seen.add(iban)
                    ordered.append(iban)

        try:
            _collect(self.image_text())
            if not ordered:
                _collect(self.raster_text(max_pages=2))
        except Exception:
            return ordered
        return ordered
