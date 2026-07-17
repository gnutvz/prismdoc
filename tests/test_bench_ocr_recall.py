"""Tests for T-023 SROIE OCR-recall benchmark harness."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from prismdoc.bench.dataset import load_manifest
from prismdoc.bench.ocr_recall import sample_recall, value_found
from prismdoc.bench.runner import run_ocr_recall
from prismdoc.models import Document
from prismdoc.stages.parse import Parser


class _CannedParser(Parser):
    """Fake parser: returns canned OCR text keyed by image path."""

    name = "canned"

    def __init__(self, by_path: dict[str, str]) -> None:
        self._by_path = by_path

    def parse(self, doc: Document) -> str:
        return self._by_path[doc.source.path]


def _write_png(path: Path) -> None:
    Image.new("RGB", (8, 8), color=(240, 240, 240)).save(path)


def test_value_found_exact_and_normalized() -> None:
    text = "Company:  BOOK   TA.K  SDN BHD\nDate: 25/12/2018"
    assert value_found("BOOK TA.K SDN BHD", text) is True
    assert value_found("book  ta.k   sdn bhd", text) is True
    assert value_found("25/12/2018", text) is True


def test_value_found_numeric_tolerant() -> None:
    text = "TOTAL DUE: RM 12.50"
    assert value_found("12.5", text) is True
    assert value_found("12.50", text) is True
    assert value_found("$12.50", text) is True


def test_value_found_absent() -> None:
    text = "Company: ACME\nTotal: 9.00"
    assert value_found("MISSING CORP", text) is False
    assert value_found("99.99", text) is False


def test_sample_recall_mixed_fields() -> None:
    text = "ACME STORE\nDate 01/01/2020\nTotal 12.50"
    result = sample_recall(
        text,
        {
            "company": "ACME STORE",
            "date": "01/01/2020",
            "address": "NO SUCH STREET",
            "total": "12.5",
        },
    )
    assert result["found"] == {
        "company": True,
        "date": True,
        "address": False,
        "total": True,
    }
    assert result["fraction"] == pytest.approx(0.75)


def test_load_manifest_resolves_relative_image_paths(tmp_path: Path) -> None:
    images = tmp_path / "images"
    images.mkdir()
    img = images / "r1.png"
    _write_png(img)

    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            [
                {
                    "image": "images/r1.png",
                    "fields": {"company": "ACME", "total": "1.00"},
                }
            ]
        ),
        encoding="utf-8",
    )

    samples = load_manifest(manifest)
    assert len(samples) == 1
    assert Path(samples[0].image_path) == img.resolve()
    assert samples[0].fields == {"company": "ACME", "total": "1.00"}


def test_run_ocr_recall_fake_parser_synthetic_manifest(tmp_path: Path) -> None:
    img_a = tmp_path / "a.png"
    img_b = tmp_path / "b.png"
    _write_png(img_a)
    _write_png(img_b)

    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            [
                {
                    "image": "a.png",
                    "fields": {
                        "company": "ALPHA CO",
                        "date": "01/02/2019",
                        "address": "1 ALPHA ROAD",
                        "total": "10.5",
                    },
                },
                {
                    "image": "b.png",
                    "fields": {
                        "company": "BETA CO",
                        "date": "03/04/2019",
                        "address": "2 BETA AVE",
                        "total": "20.00",
                    },
                },
            ]
        ),
        encoding="utf-8",
    )

    samples = load_manifest(manifest)
    # Sample A: company+date+total present (address missing); 3/4
    # Sample B: company+address present (date+total missing); 2/4
    canned = {
        samples[0].image_path: (
            "ALPHA CO\nDate 01/02/2019\nPaid TOTAL 10.50\n(no address line)"
        ),
        samples[1].image_path: (
            "Shop: BETA CO\n2 BETA AVE\n(other amounts 99.99, wrong date)"
        ),
    }

    report = run_ocr_recall(samples, _CannedParser(canned))

    assert report.n_samples == 2
    assert report.per_field["company"] == pytest.approx(1.0)
    assert report.per_field["date"] == pytest.approx(0.5)
    assert report.per_field["address"] == pytest.approx(0.5)
    assert report.per_field["total"] == pytest.approx(0.5)
    # mean of sample fractions: (0.75 + 0.5) / 2
    assert report.overall_recall == pytest.approx(0.625)
