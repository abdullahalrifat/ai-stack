"""Safe, dependency-light extraction of user supplied documents."""

from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass
from pathlib import Path

from docx import Document as DocxDocument
from openpyxl import load_workbook
from pypdf import PdfReader


SUPPORTED_DOCUMENT_EXTENSIONS = {".txt", ".md", ".csv", ".json", ".pdf", ".docx", ".xlsx"}


@dataclass(frozen=True)
class ExtractedDocument:
    text: str
    metadata: dict[str, object]


def extraction_quality(text: str) -> dict[str, str | int | float | bool]:
    """Return cheap, format-independent extraction diagnostics."""
    stripped = text.strip()
    characters = len(stripped)
    printable = sum(character.isprintable() or character in "\n\t" for character in stripped)
    replacement = stripped.count("\ufffd")
    lines = [line for line in stripped.splitlines() if line.strip()]
    table_rows = sum(
        1 for line in lines if line.count("|") >= 1 or len(re.split(r"\s{2,}", line.strip())) >= 3
    )
    printable_ratio = printable / max(characters, 1)
    replacement_ratio = replacement / max(characters, 1)
    score = max(
        0.0,
        min(
            1.0,
            printable_ratio
            - replacement_ratio * 4
            - (0.35 if characters < 20 else 0),
        ),
    )
    return {
        "quality_score": round(score, 3),
        "character_count": characters,
        "line_count": len(lines),
        "table_row_count": table_rows,
        "needs_ocr": characters < 20 or score < 0.55,
        "extraction_status": "low_quality" if characters < 20 or score < 0.55 else "usable",
    }


def _section_metadata(text: str) -> dict[str, str | int]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    headings = [
        line[:160]
        for line in lines
        if len(line) <= 160
        and (
            line.endswith(":")
            or (len(line.split()) <= 12 and line.upper() == line and any(c.isalpha() for c in line))
        )
    ][:8]
    lowered = " ".join(headings or lines[:3]).lower()
    if re.search(r"\b(history|historical|appendix|example|prior|previous|archive)\b", lowered):
        section_kind = "supplementary"
    elif re.search(r"\b(total|summary|overview)\b", lowered):
        section_kind = "summary"
    else:
        section_kind = "primary"
    return {
        "section_kind": section_kind,
        "headings": " || ".join(headings),
        "row_count": len(lines),
    }


def _with_diagnostics(text: str, metadata: dict[str, object]) -> ExtractedDocument:
    return ExtractedDocument(
        text,
        {
            **metadata,
            **_section_metadata(text),
            **extraction_quality(text),
        },
    )


def _best_pdf_text(page) -> tuple[str, str]:
    """Try pypdf's plain and layout extractors and retain the better result."""
    candidates: list[tuple[str, str]] = []
    try:
        candidates.append((page.extract_text() or "", "plain"))
    except Exception:
        pass
    try:
        candidates.append((page.extract_text(extraction_mode="layout") or "", "layout"))
    except (TypeError, ValueError, NotImplementedError):
        pass
    if not candidates:
        return "", "failed"
    return max(
        candidates,
        key=lambda item: (
            float(extraction_quality(item[0])["quality_score"]),
            int(extraction_quality(item[0])["table_row_count"]),
            len(item[0]),
        ),
    )


def _ocr_pdf_page(content: bytes, page_index: int) -> str:
    """OCR one page when installed; failure remains an explicit diagnostic."""
    try:
        import pypdfium2 as pdfium
        import pytesseract

        pdf = pdfium.PdfDocument(content)
        page = pdf[page_index]
        image = page.render(scale=2).to_pil()
        return pytesseract.image_to_string(image) or ""
    except Exception:
        return ""


def extract_document(filename: str, content: bytes) -> list[ExtractedDocument]:
    """Extract independently-citable text sections from an uploaded file."""
    suffix = Path(filename).suffix.lower()
    base = {"source": filename, "source_type": suffix.lstrip(".") or "text"}
    if suffix not in SUPPORTED_DOCUMENT_EXTENSIONS:
        raise ValueError(f"Unsupported document type: {suffix or 'unknown'}")

    if suffix == ".pdf":
        reader = PdfReader(io.BytesIO(content))
        sections = []
        for index, page in enumerate(reader.pages):
            text, strategy = _best_pdf_text(page)
            if extraction_quality(text)["needs_ocr"]:
                ocr_text = _ocr_pdf_page(content, index)
                if float(extraction_quality(ocr_text)["quality_score"]) > float(
                    extraction_quality(text)["quality_score"]
                ):
                    text, strategy = ocr_text, "ocr"
            if text.strip():
                sections.append(
                    _with_diagnostics(
                        text,
                        {
                            **base,
                            "location": f"page {index + 1}",
                            "page_number": index + 1,
                            "extraction_strategy": strategy,
                        },
                    )
                )
            else:
                sections.append(
                    _with_diagnostics(
                        "",
                        {
                            **base,
                            "location": f"page {index + 1}",
                            "page_number": index + 1,
                            "extraction_strategy": strategy,
                        },
                    )
                )
        return sections
    if suffix == ".docx":
        document = DocxDocument(io.BytesIO(content))
        blocks = [paragraph.text for paragraph in document.paragraphs if paragraph.text.strip()]
        for table in document.tables:
            blocks.extend(
                " | ".join(cell.text.strip() for cell in row.cells)
                for row in table.rows
                if any(cell.text.strip() for cell in row.cells)
            )
        text = "\n".join(blocks)
        return [_with_diagnostics(text, {**base, "location": "document"})] if text.strip() else []
    if suffix == ".xlsx":
        workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        sections: list[ExtractedDocument] = []
        for worksheet in workbook.worksheets:
            rows = []
            for row in worksheet.iter_rows(values_only=True):
                values = ["" if value is None else str(value) for value in row]
                if any(values):
                    rows.append(" | ".join(values))
            if rows:
                sections.append(
                    _with_diagnostics(
                        "\n".join(rows),
                        {
                            **base,
                            "location": f"sheet {worksheet.title}",
                            "sheet_name": worksheet.title,
                        },
                    )
                )
        return sections

    text = content.decode("utf-8", errors="ignore")
    if suffix == ".csv":
        # Parse and normalize CSV so quoted cells do not become accidental
        # instruction-like markup in the retrieved context.
        text = "\n".join(" | ".join(row) for row in csv.reader(io.StringIO(text)))
    elif suffix == ".json":
        try:
            text = json.dumps(json.loads(text), indent=2, ensure_ascii=False)
        except json.JSONDecodeError:
            pass
    return [_with_diagnostics(text, {**base, "location": "document"})] if text.strip() else []
