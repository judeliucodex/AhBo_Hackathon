"""Memory test fixtures: an in-memory DB with the demo seed and a FakeClock (no Kernel)."""
from __future__ import annotations

import pytest

from ahbo.core.clock import FakeClock, set_clock
from ahbo.core.db import open_db
from ahbo.core.settings import load_settings
from ahbo.ops.seed import load_demo


@pytest.fixture
def clock():
    c = FakeClock("2026-11-20T10:00:00+08:00")
    set_clock(c)
    return c


@pytest.fixture
def db(clock):
    d = open_db(":memory:")
    load_demo(d, clock, load_settings())
    yield d
    d.close()


@pytest.fixture
def empty_db(clock):
    d = open_db(":memory:")
    yield d
    d.close()
