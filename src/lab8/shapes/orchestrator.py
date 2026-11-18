"""Shape C — orchestrator plus read-only investigators. Student work.

1. Build a handoff with `lab8.handoff.make_handoff`. Allowed tools are read-only.
   Never put parent_secret, raw headers, or the full claim row into facts.
2. `lab8.runtime.run_readonly_investigators` runs those tools.
3. The orchestrator quotes from the investigator's order view and calls
   `durability.issue_refund`. Investigators have no write registry.
   Pass the full quote; the host subtracts earlier refunds on the same order.
   `lookup_refund_history` is a read-only tool you may hand to an investigator.
4. Trace the sanitized handoff, not the parent context.

An LLM summary of the parent context is not a sanitizer. Use the allowlist.

Trace contract v1 (docs/contracts.md): return a trace_path to a JSON file written in this run,
{"run_id": <same run_id>, "events": [...]}, on every return path including early
ones. `lab8.trace.Trace` does this; your own tracer is fine if it matches.
"""

from __future__ import annotations

import sqlite3

from lab8 import durability
from lab8.db import connect
from lab8.handoff import make_handoff
from lab8.schemas import Budget, RunResult, make_run_id
from lab8.mock_llm import complete
from lab8.runtime import run_readonly_investigators
from lab8.shapes.common import finish, safe_dump
from lab8.tools import compute_compensation, get_claim_view, tool_cost
from lab8.trace import Trace

SHAPE = "orchestrator"
# Read-only tools handed to the investigator. make_handoff raises HandoffError
# if a write tool (issue_refund, append_audit) ever appears here.
# get_clause is left out on purpose: run_readonly_investigators skips it.
INVESTIGATOR_TOOLS = ["lookup_order", "lookup_refund_history", "lookup_policy"]


def run_claim(
   claim_id: str,
   *,
   seed: int,
   store_path: str,
   crash_at: str | None = None,
) -> RunResult:
   conn = connect(store_path)
   try:
      return _run(conn, claim_id, seed=seed, crash_at=crash_at)
   finally:
      conn.close()


def _run(conn: sqlite3.Connection, claim_id: str, *, seed: int, crash_at: str | None) -> RunResult:
   budget = Budget()
   trace = Trace(make_run_id(SHAPE, claim_id, seed))

   def done(status: str, proposal: int = 0, reason: str | None = None) -> RunResult:
      """Every return path goes through here, so every branch writes a trace via finish()."""
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

   # Bước 2: orchestrator reads the claim through the allowlisted view only
   # (claim_id, principal_id, order_id, ticket). Never SELECT * from claims:
   # that row carries parent_secret and raw headers.
   claim = get_claim_view(conn, claim_id)
   budget.add_tool(tool_cost())
   if not claim["found"]:
      return done("failed", reason="unknown_claim")

   # Ticket text is user data: it may hold PII and is never used for money,
   # so the trace records only whether an order_id exists.
   trace.add("claim", {"claim_id": claim_id, "has_order_id": bool(claim["order_id"])})

   # Orchestrator plans the delegation: one model call, counted in the Budget.
   # The prompt carries no ticket text; the model never decides the amount.
   complete(budget, seed=seed, purpose="plan", prompt=f"claim={claim_id}")

   # Missing order_id -> needs_info. Do not guess an order id from the ticket.
   if not claim["order_id"]:
      return done("needs_info", reason="missing_order_id")

   # Bước 3: handoff built from the allowlist in code, not an LLM summary.
   # facts stays empty: the orchestrator holds no verified fact yet, and nothing
   # from the ticket crosses the boundary. The investigator reads the order itself.
   handoff = make_handoff(
      request_id=f"REQ-{claim_id}-s{seed}",
      claim_id=claim_id,
      task="investigate_order_and_policy",
      facts={},
      allowed_tools=INVESTIGATOR_TOOLS,
   )
   # Trace the sanitized handoff, never the parent context (claim row, ticket).
   trace.add("handoff", safe_dump(handoff))

   # Investigator runs read tools only (worker=True inside the runtime),
   # scoped to the claim's own principal.
   investigated = run_readonly_investigators(conn, budget, handoff, principal_id=claim["principal_id"])
   tools = investigated["tools"]
   order_view = tools.get("lookup_order") or {}
   history = tools.get("lookup_refund_history") or {}
   trace.add(
      "investigator",
      {
         # lookup_order already applies the ORDER_FIELDS allowlist:
         # no phone, address, internal_note, item_name.
         "lookup_order": safe_dump(order_view),
         "refund_history": {k: history.get(k) for k in ("visible", "refund_count", "refunded_vnd")},
         "policy_count": len((tools.get("lookup_policy") or {}).get("policies") or []),
      },
   )

   # Order not found or owned by another principal -> escalated, no ledger write.
   if not order_view.get("visible"):
      return done("escalated", reason=order_view.get("reason"))

   # Bước 4: money is code. Quote from the structured order row the investigator
   # returned, against current policy only. Delay numbers in the ticket are ignored.
   quote = compute_compensation(conn, order_view["order"])
   budget.add_tool(tool_cost())
   proposal = int(quote["amount_vnd"])
   trace.add("quote", safe_dump(quote))

   # Model only drafts the reply text; it never sees or changes the amount decision.
   complete(budget, seed=seed, purpose="draft", prompt=f"proposal={proposal}")

   # Quote 0 -> denied. Do not call issue_refund (no ledger row, no audit needed).
   if proposal <= 0:
      return done("denied", reason=quote["reason"])

   # Host is the only writer. Pass the FULL quote: the host checks approval and
   # subtracts what earlier claims already got for the same order (T13, T14).
   # CP3: forward crash_at and let InjectedCrash propagate (T12, crash suite).
   try:
      outcome = durability.issue_refund(conn, claim_id, proposal, crash_at=crash_at)
   except durability.InjectedCrash:
      trace.add("crash", {"at": crash_at})
      trace.write()
      raise
   trace.add("host", {"ok": outcome["ok"], "reason": outcome["reason"], "conflict": outcome["conflict"]})

   if not outcome["ok"]:
      # Amount conflict on retry -> failed. Approval / cap refusal -> denied
      # (host already wrote policy_denied). proposal_amount stays the full quote.
      return done("failed" if outcome["conflict"] else "denied", proposal, outcome["reason"])
   return done("resolved", proposal)
