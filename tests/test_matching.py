"""Tests for shared value-in-text matching (confidence + OCR-recall)."""

from __future__ import annotations

from prismdoc.matching import normalize_alphanumeric, normalize_text, value_in_text


def test_normalize_text_lowercase_collapse_strip() -> None:
    assert normalize_text("  Hello   WORLD  ") == "hello world"


def test_normalize_alphanumeric_strips_punctuation_and_space() -> None:
    assert (
        normalize_alphanumeric("BOOK TA .K (TAMAN DAYA) SDN BHD")
        == "booktaktamandayasdnbhd"
    )
    assert normalize_alphanumeric("BOOK TAK(TAMAN DAYA)SDN BHD") == (
        "booktaktamandayasdnbhd"
    )


def test_value_in_text_numeric_token_match_not_digit_soup() -> None:
    assert value_in_text("12.5", "total 12.50 paid") is True
    # Bug: digit-soup would see 125 inside 1250; number tokens must not.
    assert value_in_text("12.5", "invoice 1250 subtotal 99.00") is False


def test_value_in_text_numeric_exact_token() -> None:
    assert value_in_text("105.00", "... total 105.00") is True


def test_value_in_text_string_normalized_substring() -> None:
    assert value_in_text("BOOK TA.K SDN BHD", "Company:  book   ta.k  sdn bhd") is True
    assert value_in_text("MISSING CORP", "Company: ACME") is False


def test_value_in_text_importable_from_prismdoc_matching() -> None:
    from prismdoc.matching import value_in_text as imported

    assert imported is value_in_text
