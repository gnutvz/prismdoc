"""Generate a small retail product catalog spreadsheet for the offline demo."""

from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook

# Headers match the retail schema; values include whitespace noise for Normalize.
HEADERS = (
    "name",
    "sku",
    "price",
    "currency",
    "unit",
    "brand",
    "category",
)

SAMPLE_ROWS: list[tuple[str, str, str, str, str, str, str]] = [
    ("  Arabica Coffee Beans  ", "SKU-1001", "12.50", "USD", "kg", "  Acme  ", "Beverages"),
    ("Green Tea Leaves", " SKU-1002 ", "8.00", "USD", "  box  ", "LeafCo", "  Beverages  "),
    ("Olive Oil Extra Virgin", "SKU-1003", "15.99", "EUR", "bottle", "Mediterrana", "Pantry"),
    ("  Sea Salt Flakes ", "SKU-1004", "4.25", "USD", "jar", "Coastal", "Spices"),
    ("Dark Chocolate Bar", "SKU-1005", "  3.75  ", "USD", "bar", "  CacaoPlus ", "Snacks"),
]


def write_sample_catalog(path: str | Path) -> Path:
    """Write ``sample_catalog.xlsx`` (or ``path``) with ~5 product rows."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)

    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.title = "catalog"
    sheet.append(list(HEADERS))
    for row in SAMPLE_ROWS:
        sheet.append(list(row))
    workbook.save(out)
    return out


def main() -> None:
    """Write the demo sample next to this script."""
    target = Path(__file__).resolve().parent / "sample_catalog.xlsx"
    write_sample_catalog(target)
    print(f"Wrote {target}")


if __name__ == "__main__":
    main()
