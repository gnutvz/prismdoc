"""Tests for SROIE OCR-recall benchmark harness (exact + token-overlap)."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from PIL import Image

from prismdoc.bench.dataset import BenchSample, load_manifest
from prismdoc.bench.ocr_recall import sample_recall, token_recall, value_found
from prismdoc.bench.runner import BenchReport, FieldRecall, run_ocr_recall
from prismdoc.bench.sroie import _print_report
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
    # Number-token match only — digit soup must not false-positive.
    assert value_found("12.5", "invoice 1250 subtotal 99.00") is False


def test_value_found_absent() -> None:
    text = "Company: ACME\nTotal: 9.00"
    assert value_found("MISSING CORP", text) is False
    assert value_found("99.99", text) is False


def test_token_recall_multi_token_fraction() -> None:
    text = "jalan sagu taman daya sdn bhd receipt"
    assert token_recall("JALAN SAGU TAMAN DAYA", text) == pytest.approx(1.0)
    assert token_recall("JALAN SAGU MISSING STREET", text) == pytest.approx(0.5)
    assert token_recall("UNKNOWN MISSING CORP", text) == pytest.approx(0.0)


def test_token_recall_short_or_single_significant_is_none() -> None:
    text = "jalan sagu taman daya sdn bhd receipt"
    # Fewer than 2 significant tokens (len > 2): not measurable.
    assert token_recall("9.00", text) is None
    assert token_recall("12.5", text) is None
    assert token_recall("25/12/18", text) is None
    assert token_recall("01/01/2020", text) is None  # only "2020"
    assert token_recall("ab cd", text) is None
    assert token_recall("", text) is None


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
    assert result["per_field"]["company"]["exact"] is True
    assert result["per_field"]["company"]["token"] == pytest.approx(1.0)
    assert result["per_field"]["date"]["exact"] is True
    assert result["per_field"]["date"]["token"] is None
    assert result["per_field"]["address"]["exact"] is False
    assert result["per_field"]["address"]["token"] == pytest.approx(0.0)
    assert result["per_field"]["total"]["exact"] is True
    assert result["per_field"]["total"]["token"] is None
    assert result["mean_exact"] == pytest.approx(0.75)
    # mean_token skips None: (1.0 + 0.0) / 2
    assert result["mean_token"] == pytest.approx(0.5)


def test_sample_recall_all_token_none() -> None:
    result = sample_recall("Total 9.00", {"total": "9.00", "date": "25/12/18"})
    assert result["per_field"]["total"]["token"] is None
    assert result["per_field"]["date"]["token"] is None
    assert result["mean_token"] is None


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
                        "company": "ALPHA TRADING COMPANY",
                        "date": "01/02/2019",
                        "address": "1 ALPHA ROAD",
                        "total": "10.5",
                    },
                },
                {
                    "image": "b.png",
                    "fields": {
                        "company": "BETA TRADING COMPANY",
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
    # Sample A: company+date+total exact (address missing); mean_exact 0.75
    #   token: company 1.0, date None, address 0.5 (alpha only), total None
    # Sample B: company+address exact; mean_exact 0.5
    #   token: company 1.0, date None, address 1.0, total None
    canned = {
        samples[0].image_path: (
            "ALPHA TRADING COMPANY\nDate 01/02/2019\n"
            "Paid TOTAL 10.50\n(no address line)"
        ),
        samples[1].image_path: (
            "Shop: BETA TRADING COMPANY\n2 BETA AVE\n"
            "(other amounts 99.99, wrong date)"
        ),
    }

    report = run_ocr_recall(samples, _CannedParser(canned))

    assert report.n_samples == 2
    assert report.per_field["company"].exact_recall == pytest.approx(1.0)
    assert report.per_field["company"].token_recall == pytest.approx(1.0)
    assert report.per_field["date"].exact_recall == pytest.approx(0.5)
    assert report.per_field["date"].token_recall is None
    assert report.per_field["address"].exact_recall == pytest.approx(0.5)
    assert report.per_field["address"].token_recall == pytest.approx(0.75)
    assert report.per_field["total"].exact_recall == pytest.approx(0.5)
    assert report.per_field["total"].token_recall is None
    assert report.overall_exact == pytest.approx(0.625)
    # overall_token: mean of measurable field aggregates (company 1.0, address 0.75)
    assert report.overall_token == pytest.approx(0.875)


def test_run_ocr_recall_field_all_token_none(tmp_path: Path) -> None:
    img = tmp_path / "t.png"
    _write_png(img)
    sample = BenchSample(
        image_path=str(img.resolve()),
        fields={"total": "9.00", "date": "25/12/18"},
    )
    report = run_ocr_recall(
        [sample],
        _CannedParser({sample.image_path: "Total 9.00 Date 25/12/18"}),
    )
    assert report.per_field["total"].token_recall is None
    assert report.per_field["date"].token_recall is None
    assert report.overall_token is None


def test_print_report_renders_em_dash_for_none_token() -> None:
    report = BenchReport(
        n_samples=1,
        per_field={
            "company": FieldRecall(exact_recall=1.0, token_recall=0.84),
            "date": FieldRecall(exact_recall=0.95, token_recall=None),
            "total": FieldRecall(exact_recall=0.95, token_recall=None),
        },
        overall_exact=0.9,
        overall_token=0.84,
    )
    buf = io.StringIO()
    _print_report(report, file=buf)
    out = buf.getvalue()
    assert "overall_token: 0.8400" in out
    assert "date" in out and "—" in out
    lines = out.splitlines()
    date_line = next(line for line in lines if line.startswith("date"))
    total_line = next(line for line in lines if line.startswith("total"))
    assert "—" in date_line
    assert "—" in total_line
    company_line = next(line for line in lines if line.startswith("company"))
    assert "0.8400" in company_line
