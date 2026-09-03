"""Write the live OpenAPI schema to docs/openapi.json.

Run: python -m scripts.export_openapi

The frontend generates its client from this file, so a stale copy is worse than
no copy: it lets the tablet be built against a contract the server no longer
honours. CI regenerates and diffs it, which turns silent drift into a failed
build instead of a bug found in the field.
"""

import json
from pathlib import Path

from app.main import app

OUTPUT = Path(__file__).resolve().parent.parent / "docs" / "openapi.json"


def main() -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    schema = json.dumps(app.openapi(), indent=2, ensure_ascii=False)
    OUTPUT.write_text(schema + "\n", encoding="utf-8")
    print(f"wrote {OUTPUT}")


if __name__ == "__main__":
    main()
