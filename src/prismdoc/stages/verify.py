"""Label/region-aware field verification stage (semantic verification, slice 1).

Given the source span the extractor cited per field (``Record.field_evidence``),
verify that span sits next to an expected label and not next to an anti-label
that signals the wrong region (e.g. a ``total`` value on a ``net`` line).
"""

from __future__ import annotations

import re

from prismdoc.models import Document
from prismdoc.registry import register
from prismdoc.stages.base import Context, Stage

STATUS_NO_EVIDENCE = "no_evidence"
STATUS_UNLOCATED = "unlocated"
STATUS_LABEL_MISMATCH = "label_mismatch"
STATUS_VERIFIED = "verified"
STATUS_NO_LABEL = "no_label"

ALL_STATUSES = (
    STATUS_NO_EVIDENCE,
    STATUS_UNLOCATED,
    STATUS_LABEL_MISMATCH,
    STATUS_VERIFIED,
    STATUS_NO_LABEL,
)

DEFAULT_LABELS: dict[str, dict[str, list[str]]] = {
    "total": {
        "expect": [
            "total",
            "grand total",
            "amount due",
            "balance due",
            "amount payable",
            "gross",
            "total gross",
        ],
        "reject": [
            "subtotal",
            "sub-total",
            "net",
            "total net",
            "tax",
            "vat",
            "gst",
            "change",
            "cash",
            "discount",
            "shipping",
        ],
    },
    "subtotal": {
        "expect": ["subtotal", "sub-total", "net", "total net"],
        "reject": ["grand total", "amount due", "gross", "total gross"],
    },
    "tax": {
        "expect": ["tax", "vat", "gst"],
        "reject": ["subtotal", "grand total", "gross", "net"],
    },
}


def _normalize(text: str) -> str:
    """Casefold and collapse spaces/tabs; keep newlines intact."""
    return re.sub(r"[ \t]+", " ", str(text)).casefold()


def _document_text(doc: Document) -> str:
    return str(doc.artifacts.get("parsed_markdown") or doc.full_text)


def _line_start(text: str, idx: int) -> int:
    """Index just after the last newline before ``idx`` (or 0)."""
    nl = text.rfind("\n", 0, idx)
    return 0 if nl < 0 else nl + 1


def _locate_evidence(text: str, evidence: str) -> int | None:
    """Return start index of the first normalized evidence match, or ``None``."""
    needle = _normalize(evidence)
    if not needle:
        return None
    haystack = _normalize(text)
    idx = haystack.find(needle)
    return None if idx < 0 else idx


def _label_pattern(label: str) -> re.Pattern[str]:
    """Word-boundary regex for a (possibly multi-word) label."""
    return re.compile(rf"\b{re.escape(_normalize(label))}\b")


def _label_in_window(window: str, label: str) -> bool:
    return _label_pattern(label).search(window) is not None


def _any_label_matches(window: str, labels: list[str]) -> bool:
    return any(_label_in_window(window, label) for label in labels)


def verify_field(
    text: str,
    evidence: str,
    *,
    expect: list[str] | None = None,
    reject: list[str] | None = None,
    window: int = 60,
) -> str:
    """Compute verification status for one field's cited evidence."""
    if not evidence:
        return STATUS_NO_EVIDENCE

    idx = _locate_evidence(text, evidence)
    if idx is None:
        return STATUS_UNLOCATED

    normalized = _normalize(text)
    normalized_evidence = _normalize(evidence)
    line_start = _line_start(normalized, idx)
    win_start = max(idx - window, line_start)
    win_end = idx + len(normalized_evidence)
    win = normalized[win_start:win_end]

    reject_labels = reject or []
    expect_labels = expect or []

    if reject_labels and _any_label_matches(win, reject_labels):
        return STATUS_LABEL_MISMATCH
    if expect_labels and _any_label_matches(win, expect_labels):
        return STATUS_VERIFIED
    return STATUS_NO_LABEL


class LabelVerifyStage(Stage):
    """Verify cited field evidence sits near expected labels, not anti-labels.

    Run this on **layout-preserving** parse output (e.g. Docling markdown/tables), NOT on flattened OCR:
    the label window is bounded per line, so a flattened document (no newlines) makes the window bleed into
    adjacent labels and false-alarms (measured 100% -> 0% when the parse keeps layout; see
    docs/VERIFICATION.md). Note: this checks the value's *line/region* has the right label; distinguishing
    columns within one row (net vs. gross in a summary table) needs cell-level parsing (a later slice).
    """

    name = "verify"

    def __init__(
        self,
        field_labels: dict[str, dict[str, list[str]]] | None = None,
        window: int = 60,
    ) -> None:
        self.field_labels = field_labels if field_labels is not None else DEFAULT_LABELS
        self.window = window

    def run(self, doc: Document, ctx: Context) -> Document:
        text = _document_text(doc)
        counts: dict[str, int] = {status: 0 for status in ALL_STATUSES}

        for record in doc.records:
            for field in self.field_labels:
                if field not in record.fields:
                    continue
                evidence = record.field_evidence.get(field, "")
                labels = self.field_labels[field]
                status = verify_field(
                    text,
                    evidence,
                    expect=labels.get("expect"),
                    reject=labels.get("reject"),
                    window=self.window,
                )
                record.field_verification[field] = status
                counts[status] += 1

        doc.artifacts["verification"] = counts
        return doc


# ---------------------------------------------------------------------------
# Table column verification (semantic verification, slice 3)
# ---------------------------------------------------------------------------

STATUS_NO_TABLE = "no_table"
STATUS_VALUE_NOT_IN_TABLE = "value_not_in_table"
STATUS_COLUMN_VERIFIED = "column_verified"
STATUS_COLUMN_MISMATCH = "column_mismatch"
STATUS_COLUMN_NO_LABEL = "column_no_label"

COLUMN_STATUSES = (
    STATUS_NO_TABLE,
    STATUS_VALUE_NOT_IN_TABLE,
    STATUS_COLUMN_VERIFIED,
    STATUS_COLUMN_MISMATCH,
    STATUS_COLUMN_NO_LABEL,
)

DEFAULT_COLUMN_LABELS: dict[str, dict[str, list[str]]] = {
    "total": {
        "expect_col": [
            "gross",
            "gross worth",
            "total",
            "grand total",
            "amount due",
            "balance due",
        ],
        "reject_col": [
            "net",
            "net worth",
            "net price",
            "subtotal",
            "vat",
            "tax",
            "gst",
            "discount",
            "qty",
            "unit price",
        ],
    },
}

# A parsed markdown table: (header cells, data rows).
Table = tuple[list[str], list[list[str]]]

_SEP_LINE = re.compile(r"^[|\s:\-]+$")
_CURRENCY_OR_PCT = re.compile(r"[$€£¥%]")
_TRAILING_COMMA_DECIMAL = re.compile(r"^([+-]?\d[\d.,]*),(\d{1,2})$")
_NUMBER_TOLERANCE = 0.01


def _split_table_cells(line: str) -> list[str]:
    """Split a markdown table line into stripped cell strings."""
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [cell.strip() for cell in s.split("|")]


def parse_markdown_tables(text: str) -> list[Table]:
    """Parse pipe-delimited markdown tables from ``text``.

    A table is a run of consecutive lines whose stripped form starts with ``"|"``.
    The first line is the header; separator lines (pipes/dashes/colons/space only)
    are skipped; every other line is a data row.
    """
    tables: list[Table] = []
    lines = str(text).splitlines()
    i = 0
    while i < len(lines):
        if not lines[i].strip().startswith("|"):
            i += 1
            continue
        run: list[str] = []
        while i < len(lines) and lines[i].strip().startswith("|"):
            run.append(lines[i])
            i += 1
        header = _split_table_cells(run[0])
        rows: list[list[str]] = []
        for line in run[1:]:
            stripped = line.strip()
            if _SEP_LINE.match(stripped):
                continue
            rows.append(_split_table_cells(line))
        tables.append((header, rows))
    return tables


def _parse_number(text: str) -> float | None:
    """Parse a numeric value from cell/field text, or ``None`` if non-numeric.

    Strips currency symbols, ``%``, and spaces. A single trailing ``,dd`` is
    treated as the decimal separator (EU style); other commas/dots are thousands
    separators.
    """
    s = _CURRENCY_OR_PCT.sub("", str(text)).replace(" ", "").strip()
    if not s:
        return None
    m = _TRAILING_COMMA_DECIMAL.match(s)
    if m:
        int_part = m.group(1).replace(".", "").replace(",", "")
        try:
            return float(f"{int_part}.{m.group(2)}")
        except ValueError:
            return None
    # US / plain: commas are thousands separators; keep dots as decimal.
    cleaned = s.replace(",", "")
    try:
        return float(cleaned)
    except ValueError:
        return None


def _to_number(value: object) -> float | None:
    """Coerce a field value or cell text to float, or ``None`` if non-numeric."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return _parse_number(str(value))


def numbers_match(a: object, b: object, *, tol: float = _NUMBER_TOLERANCE) -> bool:
    """True when both sides parse as numbers and ``|a - b| <= tol``."""
    na = _to_number(a)
    nb = _to_number(b)
    if na is None or nb is None:
        return False
    return abs(na - nb) <= tol


def _matching_column_headers(tables: list[Table], value: object) -> list[str]:
    """Return headers of every cell whose number matches ``value``."""
    headers: list[str] = []
    for header, rows in tables:
        for row in rows:
            for i, cell in enumerate(row):
                if not numbers_match(value, cell):
                    continue
                headers.append(header[i] if i < len(header) else "")
    return headers


def _classify_column_headers(
    headers: list[str],
    *,
    expect_col: list[str] | None = None,
    reject_col: list[str] | None = None,
) -> str:
    """Classify matched headers; expect wins over reject."""
    expect = expect_col or []
    reject = reject_col or []
    for h in headers:
        if expect and _any_label_matches(_normalize(h), expect):
            return STATUS_COLUMN_VERIFIED
    for h in headers:
        if reject and _any_label_matches(_normalize(h), reject):
            return STATUS_COLUMN_MISMATCH
    return STATUS_COLUMN_NO_LABEL


def verify_column(
    tables: list[Table],
    value: object,
    *,
    expect_col: list[str] | None = None,
    reject_col: list[str] | None = None,
) -> str:
    """Compute column-verification status for one field value against tables."""
    if not tables:
        return STATUS_NO_TABLE
    headers = _matching_column_headers(tables, value)
    if not headers:
        return STATUS_VALUE_NOT_IN_TABLE
    return _classify_column_headers(
        headers, expect_col=expect_col, reject_col=reject_col
    )


class TableColumnVerifyStage(Stage):
    """Verify extracted field values sit under expected table column headers.

    Complements :class:`LabelVerifyStage`: label verification checks the value's
    *line*, while this stage checks the value's *column* in a markdown table
    (e.g. gross vs net on the same invoice row).
    """

    name = "verify"

    def __init__(
        self,
        column_labels: dict[str, dict[str, list[str]]] | None = None,
    ) -> None:
        self.column_labels = (
            column_labels if column_labels is not None else DEFAULT_COLUMN_LABELS
        )

    def run(self, doc: Document, ctx: Context) -> Document:
        text = _document_text(doc)
        tables = parse_markdown_tables(text)
        counts: dict[str, int] = {status: 0 for status in COLUMN_STATUSES}

        for record in doc.records:
            for field in self.column_labels:
                if field not in record.fields:
                    continue
                labels = self.column_labels[field]
                status = verify_column(
                    tables,
                    record.fields[field],
                    expect_col=labels.get("expect_col"),
                    reject_col=labels.get("reject_col"),
                )
                record.field_column_verification[field] = status
                counts[status] += 1

        doc.artifacts["column_verification"] = counts
        return doc


def register_plugins() -> None:
    """Register label and column verification stages in the plugin registry."""
    register("verify.label", LabelVerifyStage)
    register("verify.column", TableColumnVerifyStage)


register_plugins()
