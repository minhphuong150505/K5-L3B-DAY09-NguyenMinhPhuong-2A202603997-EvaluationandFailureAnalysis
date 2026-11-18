"""Evidence for docs/failure_taxonomy.md: run `fixed` + ONE bug switch from lab9.variant_agent
on seed 11 and print which eval items each switch makes fail, and on which grader.
No Lab 8 file is touched; shapes are registered in memory only.

    cd src && PYTHONPATH=. python scripts/fm_probe.py [config/metric_spec.yaml]
"""
import sys
from pathlib import Path
from lab8.shapes import SHAPES
from lab9.eval import load_spec, run_eval
from lab9.variant_agent import make_shape
spec = load_spec(Path(sys.argv[1] if len(sys.argv) > 1 else "config/metric_spec.yaml"))
bugs = ["ticket_trust","pre_deducted","ignore_denied","leaky_trace","sum_parts","stale_clause","sibling_refund","misreport","round_down"]
for b in bugs:
    name = f"probe_{b}"
    SHAPES[name] = make_shape(name, {"no_override", b})
    rep = run_eval(name, spec, seeds=[11])
    fails = {}
    for r in rep["rows"]:
        bad = [k for k in ("pass_rate","refund_amount_correct","db_fact_kept","reply_matches_ledger","proposal_correct") if r.get(k) is False]
        bad += [k for k in ("approval_bypass","secret_leak") if r.get(k)]
        if bad: fails[r["item_id"]] = bad
    print(f"{b:<15}", fails)
