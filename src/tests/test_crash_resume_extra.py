"""Extra crash/resume tests across all three student shapes."""

from __future__ import annotations

import pytest

from lab8.db import connect, init_store
from lab8.durability import InjectedCrash, committed_audit_count, get_refund
from lab8.shapes import SHAPES
from tests.conftest import CLAIMS, WORLD

STUDENT_SHAPES = ("workflow", "single", "orchestrator")
CRASH_POINTS = ("before_effect", "after_effect_before_checkpoint", "after_checkpoint")


def _fresh_store(tmp_path, name: str):
    path = tmp_path / name
    init_store(path, WORLD, CLAIMS).close()
    return path


@pytest.mark.parametrize("shape", STUDENT_SHAPES)
@pytest.mark.parametrize("crash_at", CRASH_POINTS)
def test_crash_and_resume_keeps_single_refund_and_audit(tmp_path, shape: str, crash_at: str) -> None:
    """Crashing at any durability point and resuming leaves 1 ledger row and 1 audit."""
    store = _fresh_store(tmp_path, f"{shape}-{crash_at}.sqlite")

    with pytest.raises(InjectedCrash):
        SHAPES[shape]("C-012", seed=11, store_path=str(store), crash_at=crash_at)

    conn = connect(store)
    try:
        before_resume = get_refund(conn, "C-012")
        if crash_at == "before_effect":
            assert before_resume is None
        else:
            assert before_resume is not None
    finally:
        conn.close()

    resumed = SHAPES[shape]("C-012", seed=11, store_path=str(store), crash_at=None)
    assert resumed.status == "resolved"

    conn = connect(store)
    try:
        row = get_refund(conn, "C-012")
        assert row is not None
        assert int(row["amount_vnd"]) > 0
        assert committed_audit_count(conn, "C-012") == 1
    finally:
        conn.close()
