"""Fixtures for the invariant tests: a full fake-mode system per test (see inv_helpers)."""
from __future__ import annotations

import pytest

from ahbo.evals.harness import EvalSystem


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
