"""The licence boundary, enforced instead of documented.

prismdoc is MIT, but a consumer installs the whole dependency tree — so an AGPL
package in `[project].dependencies` makes `pip install prismdoc` an AGPL event no
matter what LICENSE says. PyMuPDF is dual-licensed by Artifex (AGPL-3.0, or paid
commercial), which is why it lives in an opt-in extra.

The failure this guards against is not someone typing `pymupdf` back into the
core list on purpose. It is the ordinary version: a top-level `import fitz` added
to a module for one convenient call, which silently makes the extra mandatory
again while the metadata still claims otherwise.
"""

from __future__ import annotations

import builtins
import importlib
import sys
import tomllib
from pathlib import Path

import pytest

PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"

# Packages that cannot appear in the core dependency list. Value is the extra
# that is allowed to carry them instead.
COPYLEFT = {
    "pymupdf": "pymupdf",
    "pymupdf4llm": "pymupdf4llm",
}

# Modules that must import on a core-only install. Anything reachable from
# `import prismdoc` belongs here.
PERMISSIVE_MODULES = [
    "prismdoc",
    "prismdoc.stages.ingest",
    "prismdoc.stages.figures",
    "prismdoc.stages.parse",
]


@pytest.fixture(scope="module")
def metadata() -> dict:
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))


class TestCoreStaysPermissive:
    @pytest.mark.parametrize(("package", "extra"), COPYLEFT.items())
    def test_copyleft_package_is_not_a_core_dependency(self, metadata, package, extra):
        core = [d.split(">")[0].split("=")[0].split("[")[0].strip().lower() for d in metadata["project"]["dependencies"]]
        assert package not in core, (
            f"{package} is AGPL-3.0 and is in [project].dependencies, so a plain "
            f"`pip install prismdoc` pulls copyleft into the tree. Move it to the "
            f"'{extra}' extra."
        )

    @pytest.mark.parametrize(("package", "extra"), COPYLEFT.items())
    def test_the_extra_that_carries_it_still_exists(self, metadata, package, extra):
        """Removing it from core is only correct if opting in remains possible."""
        extras = metadata["project"]["optional-dependencies"]
        assert extra in extras, f"no '{extra}' extra to install {package} from"
        assert any(package in dep.lower() for dep in extras[extra])


@pytest.fixture
def without_pymupdf(monkeypatch):
    """Simulate a core-only install by making `import fitz` fail."""
    real_import = builtins.__import__

    def blocked(name, *args, **kwargs):
        if name.split(".")[0] in ("fitz", "pymupdf"):
            raise ImportError(f"No module named {name!r}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked)
    for name in list(sys.modules):
        if name.split(".")[0] in ("fitz", "pymupdf") or name.startswith("prismdoc"):
            monkeypatch.delitem(sys.modules, name, raising=False)
    yield


class TestCoreOnlyInstallWorks:
    @pytest.mark.parametrize("module", PERMISSIVE_MODULES)
    def test_module_imports_without_pymupdf(self, without_pymupdf, module):
        """A top-level `import fitz` anywhere here makes the extra mandatory again."""
        importlib.import_module(module)

    def test_pdf_loading_names_the_extra(self, without_pymupdf):
        ingest = importlib.import_module("prismdoc.stages.ingest")
        models = importlib.import_module("prismdoc.models")

        with pytest.raises(ImportError, match=r"prismdoc\[pymupdf\]"):
            ingest.PdfLoader().load(models.Source(path="anything.pdf"))

    def test_figure_extraction_names_the_extra(self, without_pymupdf):
        figures = importlib.import_module("prismdoc.stages.figures")

        with pytest.raises(ImportError, match=r"prismdoc\[pymupdf\]"):
            figures._extract_pdf_figures("anything.pdf")

    def test_the_error_says_why_it_is_optional(self, without_pymupdf):
        """An operator hitting this needs to know it is a licence choice, not a bug."""
        ingest = importlib.import_module("prismdoc.stages.ingest")
        assert "AGPL" in ingest._PYMUPDF_EXTRA_HINT
