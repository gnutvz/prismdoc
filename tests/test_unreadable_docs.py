"""Tests for T-009 graceful handling of encrypted / corrupt documents."""

from __future__ import annotations

from pathlib import Path

import fitz
import pytest

from prismdoc import (
    Context,
    Document,
    IngestStage,
    Pipeline,
    Source,
    UnreadableDocumentError,
)
from prismdoc.stages.ingest import ImageLoader, PdfLoader, XlsxLoader


def _make_encrypted_pdf(path: Path) -> None:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "secret")
    doc.save(
        path,
        encryption=fitz.PDF_ENCRYPT_AES_256,
        owner_pw="x",
        user_pw="x",
    )
    doc.close()


def test_unreadable_document_error_exportable() -> None:
    assert issubclass(UnreadableDocumentError, Exception)


def test_pdf_loader_encrypted_raises(tmp_path: Path) -> None:
    pdf_path = tmp_path / "encrypted.pdf"
    _make_encrypted_pdf(pdf_path)

    with pytest.raises(UnreadableDocumentError, match="encrypted"):
        PdfLoader().load(Source(path=str(pdf_path), mime="application/pdf"))


def test_pdf_loader_owner_encrypted_loads(tmp_path: Path) -> None:
    """Owner-only encryption (empty user pw): needs_pass=False — must load."""
    pdf_path = tmp_path / "owner_encrypted.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "invoice text")
    doc.save(
        pdf_path,
        encryption=fitz.PDF_ENCRYPT_AES_256,
        owner_pw="owner",
        user_pw="",
    )
    doc.close()

    with fitz.open(pdf_path) as check:
        # File is encrypted (owner pw / permissions) but opens without a password.
        assert check.metadata.get("encryption")
        assert not check.needs_pass

    pages = PdfLoader().load(Source(path=str(pdf_path), mime="application/pdf"))
    assert len(pages) >= 1


def test_pdf_loader_corrupt_raises(tmp_path: Path) -> None:
    pdf_path = tmp_path / "corrupt.pdf"
    pdf_path.write_bytes(b"this is not a pdf at all")

    with pytest.raises(UnreadableDocumentError, match="Cannot read"):
        PdfLoader().load(Source(path=str(pdf_path), mime="application/pdf"))


def test_xlsx_loader_corrupt_raises(tmp_path: Path) -> None:
    xlsx_path = tmp_path / "corrupt.xlsx"
    xlsx_path.write_bytes(b"not an xlsx file")

    with pytest.raises(UnreadableDocumentError, match="Cannot read"):
        XlsxLoader().load(
            Source(
                path=str(xlsx_path),
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        )


def test_image_loader_corrupt_raises(tmp_path: Path) -> None:
    img_path = tmp_path / "corrupt.png"
    img_path.write_bytes(b"not an image")

    with pytest.raises(UnreadableDocumentError, match="Cannot read"):
        ImageLoader().load(Source(path=str(img_path), mime="image/png"))


def test_pipeline_encrypted_pdf_records_trace(tmp_path: Path) -> None:
    pdf_path = tmp_path / "pipeline_encrypted.pdf"
    _make_encrypted_pdf(pdf_path)

    doc = Document(source=Source(path=str(pdf_path), mime="application/pdf"))
    with pytest.raises(RuntimeError, match="Stage ingest failed"):
        Pipeline([IngestStage()]).run(doc, Context())

    assert len(doc.trace) == 1
    assert doc.trace[0].stage == "ingest"
    assert doc.trace[0].ok is False
    assert doc.trace[0].error
