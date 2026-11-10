from __future__ import annotations

import pytest
from kt import watch


@pytest.fixture
async def kh(harness):
    watch(harness)
    return harness
