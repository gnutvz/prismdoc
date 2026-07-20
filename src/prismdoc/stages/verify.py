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
    """Verify cited field evidence sits near expected labels, not anti-labels."""

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


def register_plugins() -> None:
    """Register the default label verification stage in the plugin registry."""
    register("verify.label", LabelVerifyStage)


register_plugins()
