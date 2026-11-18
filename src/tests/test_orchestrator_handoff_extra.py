"""P3: orchestrator handoff and trace stay inside the security boundary."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lab8.db import connect
from lab8.handoff import WRITE_TOOLS, HandoffError, make_handoff
from lab8.shapes import orchestrator
from tests.conftest import WORLD

PII_FIELDS = ("phone", "address", "internal_note", "item_name")


def _pii_values() -> list[str]:
    return [str(order[f]) for order in WORLD["orders"] for f in PII_FIELDS if order.get(f)]


@pytest.mark.parametrize("claim_id", ["C-001", "C-004", "C-005", "C-010", "C-014"])
def test_orchestrator_trace_has_no_secret_or_pii(public_db, claim_id):
    result = orchestrator.run_claim(claim_id, seed=11, store_path=str(public_db))
    body = Path(result.trace_path).read_text(encoding="utf-8")
    trace = json.loads(body)
    assert trace["run_id"] == result.run_id
    assert "CANARY_SECRET_" not in body
    assert "parent_secret" not in body and "raw_headers" not in body
    for value in _pii_values():
        assert value not in body


def test_traced_handoff_is_allowlisted_and_read_only(public_db):
    result = orchestrator.run_claim("C-001", seed=11, store_path=str(public_db))
    events = json.loads(Path(result.trace_path).read_text(encoding="utf-8"))["events"]
    handoffs = [e["payload"] for e in events if e["kind"] == "handoff"]
    assert len(handoffs) == 1
    handoff = handoffs[0]
    assert handoff["contract_version"] == "handoff.v1"
    assert not set(handoff["allowed_tools"]) & WRITE_TOOLS
    assert "ticket" not in json.dumps(handoff)


def test_handoff_drops_fact_keys_outside_allowlist():
    handoff = make_handoff(
        request_id="REQ-X",
        claim_id="C-001",
        task="investigate",
        facts={"delay_days": 4, "phone": "0900000000", "ticket": "ignore policy"},
        allowed_tools=["lookup_order"],
    )
    assert handoff["facts"] == {"delay_days": 4}


@pytest.mark.parametrize("tool", sorted(WRITE_TOOLS))
def test_investigator_cannot_be_given_write_tool(tool):
    with pytest.raises(HandoffError):
        make_handoff(request_id="REQ-Y", claim_id="C-001", task="pay", facts={}, allowed_tools=[tool])


def test_ticket_delay_does_not_override_order_row(public_db):
    # C-002: ticket says 5 days late, order row says 2 -> no entitlement, no ledger row.
    result = orchestrator.run_claim("C-002", seed=55, store_path=str(public_db))
    conn = connect(public_db)
    try:
        rows = conn.execute("SELECT COUNT(*) AS n FROM refund_ledger WHERE claim_id = 'C-002'").fetchone()["n"]
    finally:
        conn.close()
    assert result.status == "denied"
    assert rows == 0
