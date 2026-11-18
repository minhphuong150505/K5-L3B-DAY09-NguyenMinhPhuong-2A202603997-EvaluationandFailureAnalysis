"""Gate robustness: a no_significant_drop metric with nothing to pair must block as MISSING, not crash."""
import copy

import pytest

pytestmark = pytest.mark.student


def _report(rows, k_values=()):
    from lab9.eval import aggregate

    ids = [m for m in ("pass_rate", "refund_amount_correct") if m in rows[0]]
    return {"run_id": "t", "manifest": {"eval_set_sha": "x", "items": 2, "seeds": [1, 2]},
            "rows": rows, "evaluator_failures": [], "metrics": aggregate(rows, ids, k_values=list(k_values))}


ROWS = [{"item_id": f"E-{i:03d}", "seed": s, "slices": [], "pass_rate": True, "refund_amount_correct": True,
         "cost": 1.0} for i in range(2) for s in (1, 2)]


def test_lab9_gate_blocks_metric_missing_from_baseline():
    from lab9 import gate

    pr = _report(copy.deepcopy(ROWS))
    old = [{k: v for k, v in r.items() if k != "refund_amount_correct"} for r in ROWS]   # baseline predates the metric
    base = _report(old)
    spec = {"metrics": [{"id": "refund_amount_correct", "kind": "capability", "blocking": True,
                         "threshold": {"min": 0.9, "no_significant_drop": True}}]}
    blocked, lines = gate.check(pr, base, spec)
    assert blocked == ["refund_amount_correct"]
    assert "re-run the baseline" in lines[0] and "MISSING" in lines[0]


def test_lab9_gate_non_row_metric_with_drop_rule_is_missing_not_crash():
    from lab9 import gate

    pr, base = _report(copy.deepcopy(ROWS), k_values=[2]), _report(copy.deepcopy(ROWS), k_values=[2])
    spec = {"pass_k": [2], "metrics": [{"id": "pass_hat_2", "kind": "capability", "blocking": True,
                                        "threshold": {"min": 0.8, "no_significant_drop": True}}]}
    blocked, lines = gate.check(pr, base, spec)
    assert blocked == ["pass_hat_2"] and "not a per-row metric" in lines[0]


def test_lab9_gate_still_merges_when_metric_present():
    from lab9 import gate

    rep = _report(copy.deepcopy(ROWS))
    spec = {"metrics": [{"id": "refund_amount_correct", "kind": "capability", "blocking": True,
                         "threshold": {"min": 0.9, "no_significant_drop": True}}]}
    assert gate.check(rep, copy.deepcopy(rep), spec)[0] == []
