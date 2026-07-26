"""Safe, dependency-light extraction of user supplied documents."""

from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass
from pathlib import Path

from docx import Document as DocxDocument
from openpyxl import load_workbook
from pypdf import PdfReader


SUPPORTED_DOCUMENT_EXTENSIONS = {".txt", ".md", ".csv", ".json", ".pdf", ".docx", ".xlsx"}


@dataclass(frozen=True)
class ExtractedDocument:
    text: str
    metadata: dict[str, str | int]


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
            text = page.extract_text() or ""
            if text.strip():
                sections.append(ExtractedDocument(text, {**base, "location": f"page {index + 1}"}))
        return sections
    if suffix == ".docx":
        document = DocxDocument(io.BytesIO(content))
        text = "\n".join(paragraph.text for paragraph in document.paragraphs if paragraph.text.strip())
        return [ExtractedDocument(text, {**base, "location": "document"})] if text.strip() else []
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
                    ExtractedDocument("\n".join(rows), {**base, "location": f"sheet {worksheet.title}"})
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
    return [ExtractedDocument(text, {**base, "location": "document"})] if text.strip() else []
