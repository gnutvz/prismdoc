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


@pytest.fixture(scope="module")
def pdf_with_figure() -> Path:
    """A committed PDF, not one generated in the test.

    Every other PDF test here builds its fixture with PyMuPDF, which is fine for
    a dev environment but useless in this file: a test proving PDF works without
    PyMuPDF cannot use PyMuPDF to produce the PDF. Checking in a 5KB file is the
    only way the claim means anything.
    """
    fixture = Path(__file__).parent / "fixtures" / "page_with_figure.pdf"
    assert fixture.exists(), f"missing test fixture: {fixture}"
    return fixture


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
    """PDF must work on a permissive install, not merely fail politely.

    An earlier version of this fix only made the AGPL import lazy, so a core-only
    install imported cleanly and then raised the moment it saw a PDF. That is not
    a working install of a document-extraction tool — it just moves the failure.
    """

    @pytest.mark.parametrize("module", PERMISSIVE_MODULES)
    def test_module_imports_without_pymupdf(self, without_pymupdf, module):
        """A top-level `import fitz` anywhere here makes the extra mandatory again."""
        importlib.import_module(module)

    def test_the_default_engine_needs_no_extra(self, without_pymupdf):
        pdf = importlib.import_module("prismdoc.pdf")
        assert pdf.default_engine().name == "pdfplumber"

    def test_pdf_pages_load_without_pymupdf(self, without_pymupdf, pdf_with_figure):
        ingest = importlib.import_module("prismdoc.stages.ingest")
        models = importlib.import_module("prismdoc.models")

        pages = ingest.PdfLoader().load(models.Source(path=str(pdf_with_figure)))

        assert len(pages) == 1
        assert "Catalog page" in pages[0].text
        assert pages[0].blocks and pages[0].blocks[0].bbox is not None

    def test_figures_extract_without_pymupdf(self, without_pymupdf, pdf_with_figure):
        figures = importlib.import_module("prismdoc.stages.figures")

        found, placeholders = figures._extract_pdf_figures(str(pdf_with_figure))

        assert len(found) == 1
        assert placeholders == {0: ["fig_0_0"]}
        assert found[0].image_b64

    def test_the_agpl_engine_still_names_its_extra(self, without_pymupdf):
        """Choosing it explicitly should explain why it is not already there."""
        pdf = importlib.import_module("prismdoc.pdf")

        with pytest.raises(ImportError, match=r"prismdoc\[pymupdf\]"):
            pdf.PyMuPDFEngine().load_pages("anything.pdf")
        assert "AGPL" in pdf._PYMUPDF_EXTRA_HINT
