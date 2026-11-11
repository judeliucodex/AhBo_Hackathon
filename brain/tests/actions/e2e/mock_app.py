"""Tiny FastAPI app for the e2e topology ONLY (not collected by pytest): the mock-centre API router plus
the static `mock-centre/` page, served on the api container of the `ahbo-t-actions` network so the browser
container can fetch `http://api:8000/mock-centre/`. Run directly: `python tests/actions/e2e/mock_app.py`.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))          # /repo/brain

from fastapi import FastAPI  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

from ahbo.actions import mock_centre  # noqa: E402
from ahbo.core.clock import Clock, set_clock  # noqa: E402
from ahbo.core.db import open_db  # noqa: E402

set_clock(Clock())
db = open_db("/tmp/ahbo-e2e-mock.sqlite")
mock_centre.bind(db)

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
app.include_router(mock_centre.router)
REPO = Path(__file__).resolve().parents[4]
app.mount("/mock-centre", StaticFiles(directory=str(REPO / "mock-centre"), html=True), name="mock")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="warning")
