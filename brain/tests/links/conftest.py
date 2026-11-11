"""Shared fixtures for the links tests (§9.1–9.3, §26.3).

- `system`: the full fake-mode system (`ahbo.evals.harness.EvalSystem`) — real Kernel, real links wiring
  (dispatchers write the fake outbox), in-memory DB, FakeClock.
- `client`: the real FastAPI app behind Starlette's TestClient, with test secrets, for the webhook/auth routes.
"""
from __future__ import annotations

from typing import Iterator

import pytest
from fastapi.testclient import TestClient

from ahbo.api.app import create_app
from ahbo.core.clock import FakeClock
from ahbo.evals.harness import EvalSystem

from link_helpers import make_settings  # noqa: F401  (re-exported for convenience)


@pytest.fixture
async def make_system():
    made: list[EvalSystem] = []

    async def make(**kw) -> EvalSystem:
        s = await EvalSystem.create(**kw)
        made.append(s)
        return s

    yield make
    for s in made:
        await s.close()


@pytest.fixture
async def system(make_system) -> EvalSystem:
    return await make_system()


@pytest.fixture
def settings(tmp_path):
    return make_settings(tmp_path)


@pytest.fixture
def client(settings) -> Iterator[TestClient]:
    app = create_app(settings, db_path=":memory:", clock=FakeClock("2026-11-20T15:00:00+08:00"), time_scale=0.05,
                     backups=False)
    with TestClient(app, base_url="https://test.invalid") as c:
        yield c
