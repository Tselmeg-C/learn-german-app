"""Dump the OpenAPI schema to stdout.

The web client generates its types from this (`npm run gen:api`), so a change to a
response model breaks the web typecheck in CI rather than surfacing as a runtime bug.

    python -m lgapp.openapi > ../web/openapi.json
"""

import json
import sys

from lgapp.main import create_app


def main() -> None:
    json.dump(create_app().openapi(), sys.stdout, indent=2)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
