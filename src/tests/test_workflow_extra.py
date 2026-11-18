"""Extra checks for shape A (workflow). Runs in the default host gate (`python -m pytest -q`)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lab8.db import connect, init_store
from lab8.durability import InjectedCrash
from lab8.shapes.workflow import run_claim
from lab8.tools import compute_compensation, lookup_order
from tests.conftest import BY_ID, CLAIMS, WORLD

SEEDS = [11, 22, 33, 44, 55]


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    # Trace() writes to ./traces; keep it inside tmp_path.
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "wf.sqlite"
    init_store(path, WORLD, CLAIMS).close()
    return path


def _ledger_rows(store: Path, claim_id: str) -> int:
    conn = connect(store)
    try:
        return conn.execute(
            "SELECT COUNT(*) AS n FROM refund_ledger WHERE claim_id = ?", (claim_id,)
        ).fetchone()["n"]
    finally:
        conn.close()


@pytest.mark.parametrize("seed", SEEDS)
def test_workflow_ignores_delay_written_in_ticket(store, seed):
    # C-002: ticket says "trễ 5 ngày", order row says 2 days -> nothing owed, on every seed
    # (swarm fails this at seed 55).
    result = run_claim("C-002", seed=seed, store_path=str(store))
    assert result.status == "denied"
    assert result.proposal_amount == 0
    assert result.refund_effects == []
    assert _ledger_rows(store, "C-002") == 0


@pytest.mark.parametrize("claim_id", sorted(BY_ID))
def test_workflow_amount_comes_from_compute_compensation(store, claim_id):
    result = run_claim(claim_id, seed=11, store_path=str(store))
    if result.status != "resolved":
        return
    claim = BY_ID[claim_id]
    conn = connect(store)
    try:
        order = lookup_order(conn, claim["order_id"], claim["principal_id"])["order"]
        expected = compute_compensation(conn, order)["amount_vnd"]
    finally:
        conn.close()
    assert result.proposal_amount == expected


@pytest.mark.parametrize("claim_id", sorted(BY_ID))
def test_workflow_trace_has_no_secret_or_ticket_text(store, claim_id):
    result = run_claim(claim_id, seed=55, store_path=str(store))
    body = Path(result.trace_path).read_text(encoding="utf-8")
    data = json.loads(body)
    assert data["run_id"] == result.run_id
    assert isinstance(data["events"], list) and data["events"]
    assert "CANARY_SECRET" not in body
    assert "Bearer" not in body
    assert BY_ID[claim_id]["ticket"] not in body
    assert "CANARY_SECRET" not in json.dumps(result.as_dict(), ensure_ascii=False)


@pytest.mark.parametrize("crash_at", ["before_effect", "after_effect_before_checkpoint", "after_checkpoint"])
def test_workflow_crash_then_resume_keeps_one_refund(store, crash_at):
    with pytest.raises(InjectedCrash):
        run_claim("C-012", seed=11, store_path=str(store), crash_at=crash_at)
    resumed = run_claim("C-012", seed=11, store_path=str(store))
    assert resumed.status == "resolved"
    assert resumed.proposal_amount == 35000
    assert len(resumed.refund_effects) == 1
    assert resumed.committed_audit_count == 1
    assert _ledger_rows(store, "C-012") == 1
