"""Typed errors raised by prismdoc stages and loaders."""

from __future__ import annotations


class UnreadableDocumentError(Exception):
    """Raised when a document cannot be loaded.

    Typical causes: encrypted/password-protected, corrupt, or unsupported
    internal structure.
    """


class InputTooLargeError(Exception):
    """Raised when a prompt exceeds the configured max input token limit."""
