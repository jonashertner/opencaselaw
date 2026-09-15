"""OCR fallback for image-only PDFs (2026-09-15).

Federal authorities publish some decisions as scans without a text layer. On 2026-09-15
8 ElCom Verfügungen, 8 PostCom Verfügungen of 2013-2016 and 2 ESchK tariff decisions were
downloaded every night and dropped as "no text extracted". The production host has
Tesseract 5 with deu/fra/ita and pytesseract; pages are rendered with PyMuPDF, so no
pdf2image or poppler dependency is needed here.

Every failure (missing library, unreadable PDF, Tesseract error) returns "", so callers
keep their existing no-text handling. Partial OCR output is discarded rather than stored.
"""
from __future__ import annotations

import io
import logging

logger = logging.getLogger(__name__)

OCR_LANGS = "deu+fra+ita"
OCR_DPI = 300
OCR_MAX_PAGES = 80
MIN_TEXT_CHARS = 50


def ocr_pdf_bytes(data: bytes, *, lang: str = OCR_LANGS, dpi: int = OCR_DPI, max_pages: int = OCR_MAX_PAGES) -> str:
    """Text of a scanned PDF via Tesseract, or "" when OCR is unavailable or fails."""
    try:
        import fitz  # PyMuPDF
        import pytesseract
        from PIL import Image
    except ImportError as e:
        logger.info(f"OCR unavailable: {e}")
        return ""
    try:
        doc = fitz.open(stream=data, filetype="pdf")
    except Exception as e:
        logger.warning(f"OCR: PDF not readable: {e}")
        return ""
    pages: list[str] = []
    try:
        for index, page in enumerate(doc):
            if index >= max_pages:
                logger.warning(f"OCR: stopped after {max_pages} of {doc.page_count} pages")
                break
            pixmap = page.get_pixmap(dpi=dpi)
            image = Image.open(io.BytesIO(pixmap.tobytes("png")))
            pages.append(pytesseract.image_to_string(image, lang=lang))
    except Exception as e:
        logger.warning(f"OCR failed after {len(pages)} pages: {e}")
        return ""
    finally:
        doc.close()
    return "\n\n".join(pages).strip()
