"""Shape B — one ReAct agent. Student work.

Use `lab8.runtime.run_react` with max_steps=8. `decide` may pick a read tool.
It must not call `issue_refund`. After the loop, the host validates the quote
and is the only writer.

A decide() that trusts ticket text over the order row will fail hidden claims
where the customer overstates the delay or cites a superseded clause.

Per-order cap: an order may already have been refunded by an earlier claim.
The read tool `lookup_refund_history` shows how much. The host still enforces
the cap, so pass the full quote to `durability.issue_refund`, not a net amount.

Caps: stop at 8 steps. Count model calls in the Budget so cost stays comparable.

Trace contract v1 (docs/contracts.md): return a trace_path to a JSON file written in this run,
{"run_id": <same run_id>, "events": [...]}, on every return path including early
ones. `lab8.trace.Trace` does this; your own tracer is fine if it matches.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from lab8 import durability
from lab8.db import connect
from lab8.mock_llm import complete
from lab8.runtime import run_react
from lab8.schemas import Budget, RunResult, make_run_id
from lab8.shapes.common import finish
from lab8.trace import Trace

SHAPE = "single"


def run_claim(
    claim_id: str,
    *,
    seed: int,
    store_path: str,
    crash_at: str | None = None,
) -> RunResult:
    """Entry point for Shape B (single ReAct agent)."""
    conn = connect(store_path)
    try:
        return _run(conn, claim_id, seed=seed, crash_at=crash_at)
    finally:
        conn.close()


def _run(
    conn: sqlite3.Connection,
    claim_id: str,
    *,
    seed: int,
    crash_at: str | None,
) -> RunResult:
    """Execute single ReAct loop and coordinate with host durability layer."""
    budget = Budget()
    trace = Trace(make_run_id(SHAPE, claim_id, seed))

    def done(status: str, proposal: int, reason: str | None) -> RunResult:
        """Helper to append final decision to trace and finalize RunResult via host finish()."""
        trace.add("decision", {"status": status, "proposal_amount": int(proposal), "reason": reason})
        return finish(
            conn,
            shape=SHAPE,
            claim_id=claim_id,
            seed=seed,
            status=status,
            proposal_amount=proposal,
            budget=budget,
            trace=trace,
            error_reason=reason,
        )

    # State container for ReAct loop transitions
    agent_state: dict[str, Any] = {
        "claim": None,
        "order": None,
        "quote": None,
    }

    def decide(seen: dict[str, Any]) -> dict[str, Any]:
        """Model decision logic for next tool invocation or final determination.
        
        Only read-only tools are selected: get_claim_view, lookup_order, compute_compensation.
        Ticket claims are ignored; truth is established exclusively via order row.
        """
        last_tool = seen.get("last_tool")
        last_result = seen.get("last_result")

        # Step 1: Initial invocation -> fetch claim details
        if last_tool is None:
            return {
                "tool": "get_claim_view",
                "args": {"claim_id": claim_id},
            }

        # Step 2: Handle claim view result -> validate and lookup order
        if last_tool == "get_claim_view":
            agent_state["claim"] = last_result
            found = bool(last_result.get("found"))
            has_order_id = bool(last_result.get("order_id"))
            trace.add("step", {"name": "get_claim_view", "found": found, "has_order_id": has_order_id})

            if not found:
                return {"final": {"status": "failed", "reason": "unknown_claim", "proposal": 0}}
            if not has_order_id:
                return {"final": {"status": "needs_info", "reason": "missing_order_id", "proposal": 0}}

            return {
                "tool": "lookup_order",
                "args": {
                    "order_id": last_result["order_id"],
                    "principal_id": last_result["principal_id"],
                },
            }

        # Step 3: Handle order lookup result -> validate order & compute entitlement
        if last_tool == "lookup_order":
            visible = bool(last_result.get("visible"))
            reason = last_result.get("reason")
            trace.add("step", {"name": "lookup_order", "visible": visible, "reason": reason})

            if not visible:
                return {"final": {"status": "escalated", "reason": reason, "proposal": 0}}

            order = dict(last_result["order"])
            agent_state["order"] = order

            # Compute compensation strictly from verified DB order row (ignores ticket lies)
            return {
                "tool": "compute_compensation",
                "args": {"order": order},
            }

        # Step 4: Handle computation quote -> decide if eligible for host refund gate
        if last_tool == "compute_compensation":
            agent_state["quote"] = last_result
            proposal = int(last_result.get("amount_vnd", 0))
            reason = last_result.get("reason")
            clause_ids = list(last_result.get("clause_ids") or [])
            trace.add("quote", {"amount_vnd": proposal, "reason": reason, "clause_ids": clause_ids})

            if proposal <= 0:
                return {"final": {"status": "denied", "reason": reason, "proposal": 0}}

            return {
                "final": {
                    "status": "ready_to_refund",
                    "proposal": proposal,
                    "reason": None,
                }
            }

        return {"final": {"status": "failed", "reason": "unexpected_step", "proposal": 0}}

    # Execute bounded ReAct loop with max 8 steps
    react_result = run_react(
        conn,
        budget,
        observation={"claim_id": claim_id},
        decide=decide,
        max_steps=8,
        worker=True,
    )

    if react_result.get("stopped") == "step_cap":
        return done("failed", 0, "step_cap")

    final = react_result.get("final") or {}
    status = final.get("status")
    proposal = int(final.get("proposal", 0))
    reason = final.get("reason")

    # Early returns handled directly without calling issue_refund
    if status in ("failed", "needs_info", "escalated", "denied"):
        complete(budget, seed=seed, purpose="draft", prompt=status)
        return done(status, proposal, reason)

    # For valid positive proposals, invoke host durability gate
    if status == "ready_to_refund":
        try:
            outcome = durability.issue_refund(conn, claim_id, proposal, crash_at=crash_at)
        except durability.InjectedCrash:
            trace.add("crash", {"at": crash_at})
            trace.write()
            raise

        trace.add(
            "issue_refund",
            {
                "ok": outcome["ok"],
                "reason": outcome["reason"],
                "conflict": outcome["conflict"],
            },
        )

        # Model drafts client response text (model call accounted into budget)
        complete(budget, seed=seed, purpose="draft", prompt=f"proposal={proposal}")

        if not outcome["ok"]:
            return done("failed" if outcome["conflict"] else "denied", proposal, outcome["reason"])
        return done("resolved", proposal, None)

    return done("failed", 0, "unknown_final_status")
