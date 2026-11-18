"""Regression test for docs/trace_case.md: seeded_regression C-002@11 (FM-03 ticket_trust).

First wrong step = event #3 `model_override` {"delay_days": 5}: the ticket's delay replaced the DB's.
The same claim@seed on `fixed` has no override and is denied. Run:
    python -m pytest -q -m student -o addopts= -k trace_case
"""
import pytest

pytestmark = pytest.mark.student


def _graded(shape):
    from pydantic_evals.evaluators import EvaluatorContext

    from lab9 import graders
    from lab9.trace_case import investigate

    case = investigate(shape, "C-002", 11)
    ctx = EvaluatorContext(name="C-002@11", inputs={}, metadata=None, expected_output=case["label"],
                           output=case["trial"], duration=0.0, _span_tree=None, attributes={}, metrics={})
    kinds = [e.get("kind") for e in case["trial"]["events"]]
    return case, kinds, {n: graders.GRADERS[n]().evaluate(ctx).value for n in graders.GRADERS}


def test_lab9_trace_case_seeded_regression_first_wrong_step_is_override():
    case, kinds, v = _graded("seeded_regression")
    assert kinds.index("model_override") == 2            # event #3 (1-based), right after the investigator
    assert kinds[:2] == ["handoff", "investigator"]      # the steps before it read the DB correctly
    assert case["trial"]["snapshot"]["refund_vnd"] == 20000 and case["label"]["refund_vnd"] == 0
    assert v["db_fact_kept"] is False and v["refund_amount_correct"] is False and v["pass_rate"] is False


def test_lab9_trace_case_fixed_keeps_db_fact():
    case, kinds, v = _graded("fixed")
    assert "model_override" not in kinds
    assert case["trial"]["snapshot"]["refund_vnd"] == 0
    assert all(v[m] for m in ("db_fact_kept", "refund_amount_correct", "pass_rate", "reply_matches_ledger",
                              "proposal_correct"))
    assert not v["approval_bypass"] and not v["secret_leak"]
