"""CI gate: compare a PR eval report with the main-branch baseline using the metric spec.

    python -m lab9.gate --pr reports/eval_workflow.json --baseline reports/baseline_v1.json

exit 0 = merge, exit 1 = blocked. Safety metrics: max 0, no averaging away.
Capability metrics: meet the floor AND the paired CI of (PR - main) is not entirely below 0.
Thresholds may only get stricter on the same eval-set version (ratchet).
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from lab8.console import utf8_stdio
from lab9 import stats
from lab9.eval import SPEC, aggregate, load_spec, per_item
from lab9.graders import GRADERS


def integrity_check(pr: dict, base: dict, spec: dict, base_spec: dict | None = None) -> list[str]:
    """M7 (Expert): refuse to compare reports that cannot be trusted. Return a list of problems
    (empty = trustworthy). A gate that passes a broken report is worse than no gate.
    Must catch at least:
      - any metric value in pr["metrics"] that is NaN or inf
      - len(pr["rows"]) != manifest items x len(manifest seeds)   (dropped / duplicated rows)
      - the (item_id, seed) set of PR differs from the baseline's (cherry-picked seeds/items)
      - pr["evaluator_failures"] non-empty, or a row whose value for a graded spec metric is
        None (a crashed grader silently scores as "missing" and drops out of the mean)
      - base_spec given and a threshold in spec is LOOSER than in base_spec for the same
        eval-set version (ratchet: max may not rise, min may not fall, a blocking metric may
        not become non-blocking, a metric may not disappear; the same for spec["slices"],
        including dropping no_significant_drop)
      - an item's slice tags in PR rows differ from the baseline's (relabelling an item out of a
        slice hides its regression from the slice gate)"""
    problems: list[str] = []
    rows, base_rows = pr.get("rows") or [], base.get("rows") or []
    man, base_man = pr.get("manifest") or {}, base.get("manifest") or {}

    # 1. metric values must be finite numbers (NaN < min is False: a naive gate lets it through)
    for k, v in (pr.get("metrics") or {}).items():
        if isinstance(v, float) and not math.isfinite(v):
            problems.append(f"metric {k} is {v} (not a finite number)")

    # 2. row count = items x seeds, no duplicated (item, seed)
    seeds = list(man.get("seeds") or [])
    want_n = int(man.get("items") or 0) * len(seeds)
    if len(rows) != want_n:
        problems.append(f"{len(rows)} rows, manifest says {man.get('items')} items x {len(seeds)} seeds = {want_n}")
    keys = [(r.get("item_id"), r.get("seed")) for r in rows]
    dups = sorted({k for k in keys if keys.count(k) > 1}, key=str)
    if dups:
        problems.append(f"{len(dups)} duplicated (item, seed) row(s), e.g. {dups[0]}")

    # 3. same (item, seed) set and seed list as the baseline (no cherry-picked items or seeds)
    base_keys = {(r.get("item_id"), r.get("seed")) for r in base_rows}
    if set(keys) != base_keys:
        miss, extra = len(base_keys - set(keys)), len(set(keys) - base_keys)
        problems.append(f"(item, seed) set differs from the baseline: {miss} missing, {extra} extra")
    if sorted(seeds) != sorted(base_man.get("seeds") or []):
        problems.append(f"seeds {seeds} differ from the baseline's {base_man.get('seeds')}")
    if man.get("items") != base_man.get("items"):
        problems.append(f"manifest items {man.get('items')} differs from the baseline's {base_man.get('items')}")

    # 4. crashed graders: evaluator failures, or a graded metric that is None / not a bool on a row
    if pr.get("evaluator_failures"):
        problems.append(f"{len(pr['evaluator_failures'])} evaluator failure(s), first: {pr['evaluator_failures'][0]}")
    graded = [m["id"] for m in spec.get("metrics", []) if m["id"] in GRADERS]
    for mid in graded:
        bad = [r for r in rows if not isinstance(r.get(mid), bool)]
        if bad:
            problems.append(f"{len(bad)} row(s) with no valid {mid} score (None/missing/non-bool), "
                            f"e.g. {bad[0].get('item_id')}@{bad[0].get('seed')}")

    # 5. reported metrics must be what the rows say (a hand-edited number is not evidence)
    if rows and not problems:
        ids = [m for m in graded if m in rows[0]]
        has_cost = all("cost" in r for r in rows)
        recomputed = aggregate([r if has_cost else {**r, "cost": 0.0} for r in rows], ids,
                               k_values=spec.get("pass_k", []))
        if not has_cost:
            recomputed.pop("cost_per_success", None)
        for k, v in recomputed.items():
            got = (pr.get("metrics") or {}).get(k)
            if k in (pr.get("metrics") or {}) and not _close(got, v):
                problems.append(f"metric {k}={got} but the rows give {v}")

    # 6. slice tags of an item may not change (moving an item out of a slice hides it)
    def tags(rs):
        out: dict[str, set] = {}
        for r in rs:
            out.setdefault(r.get("item_id"), set()).add(tuple(sorted(r.get("slices") or [])))
        return out
    pt, bt = tags(rows), tags(base_rows)
    relabelled = sorted(i for i in pt.keys() & bt.keys() if pt[i] != bt[i] or len(pt[i]) > 1)
    if relabelled:
        problems.append(f"slice tags changed vs the baseline for {len(relabelled)} item(s), e.g. {relabelled[0]}")

    # 7. ratchet: on the same eval-set version thresholds may only get stricter
    if base_spec is not None:
        problems += ratchet_problems(spec, base_spec)
    return problems


def _close(a, b) -> bool:
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return math.isfinite(a) and abs(a - b) <= 1e-9
    return a == b


def _looser(new: dict, old: dict, where: str) -> list[str]:
    """Compare one metric/slice rule with the base one. Returns the ways it got looser."""
    out = []
    nth = new.get("threshold", new) if "threshold" in old or "threshold" in new else new
    oth = old.get("threshold", old) if "threshold" in old or "threshold" in new else old
    if old.get("blocking") and not new.get("blocking"):
        out.append(f"{where}: blocking -> non-blocking")
    for key in ("max", "max_increase"):
        if key in oth and (key not in nth or nth[key] > oth[key]):
            out.append(f"{where}: {key} {oth[key]} -> {nth.get(key, 'removed')}")
    if "min" in oth and ("min" not in nth or nth["min"] < oth["min"]):
        out.append(f"{where}: min {oth['min']} -> {nth.get('min', 'removed')}")
    if oth.get("no_significant_drop") and not nth.get("no_significant_drop"):
        out.append(f"{where}: no_significant_drop dropped")
    if old.get("metric") and new.get("metric") != old.get("metric"):
        out.append(f"{where}: metric {old['metric']} -> {new.get('metric')}")
    return out


def ratchet_problems(spec: dict, base_spec: dict) -> list[str]:
    if spec.get("eval_set_version") != base_spec.get("eval_set_version"):
        return []   # a new eval-set version starts a new baseline; thresholds may be re-set there
    out = []
    new_m = {m["id"]: m for m in spec.get("metrics", [])}
    for m in base_spec.get("metrics", []):
        if m["id"] not in new_m:
            out.append(f"RATCHET metric {m['id']} removed")
        else:
            out += [f"RATCHET {p}" for p in _looser(new_m[m["id"]], m, m["id"])]
    new_s = {(s["name"], s.get("metric")): s for s in spec.get("slices") or []}
    for s in base_spec.get("slices") or []:
        k = (s["name"], s.get("metric"))
        if k not in new_s:
            out.append(f"RATCHET slice {s['name']}/{s.get('metric')} removed")
        else:
            out += [f"RATCHET {p}" for p in _looser(new_s[k], s, f"slice:{s['name']}/{s.get('metric')}")]
    for key in ("seeds", "eval_set", "labels"):
        if key in base_spec and spec.get(key) != base_spec.get(key):
            out.append(f"RATCHET {key} changed on the same eval-set version")
    return out


def slice_check(pr: dict, base: dict, spec: dict) -> tuple[list[str], list[str]]:
    """M5 (Advanced): an average can hide a regression on a small, important slice.
    For each entry of spec["slices"] ({"name", "metric", "min"?, "no_significant_drop"?, "blocking"}):
      - value = mean of per_item(pr, metric, slice_name=name)
      - fail if below "min", or if no_significant_drop and the paired CI over the slice's items
        lies entirely below 0
    Return (blocked ids like "slice:money_movement/pass_rate", report lines)."""
    blocked, lines = [], []
    bs = spec.get("bootstrap", {})
    for sl in spec.get("slices") or []:
        name, metric = sl["name"], sl["metric"]
        sid = f"slice:{name}/{metric}"
        a, b = per_item(pr, metric, slice_name=name), per_item(base, metric, slice_name=name)
        verdict, note, val = "ok", "", None
        if not a:
            verdict = "missing"            # an empty slice cannot vouch for anything
        else:
            val = round(sum(a.values()) / len(a), 4)
            if "min" in sl and val < sl["min"]:
                verdict = "fail"
            if "max" in sl and val > sl["max"]:
                verdict = "fail"
            if sl.get("no_significant_drop"):
                keys = sorted(set(a) & set(b))
                if keys:
                    d, lo, hi = stats.paired_bootstrap([a[k] for k in keys], [b[k] for k in keys],
                                                       n_resamples=bs.get("n_resamples", 1000), rng=bs.get("rng", 2027))
                    note = f"Δ {d:+.3f} CI [{lo:+.3f}, {hi:+.3f}]"
                    if hi < 0:
                        verdict = "fail"
                else:
                    verdict, note = "missing", "no shared items with the baseline"
        if verdict != "ok" and sl.get("blocking"):
            blocked.append(sid)
        th = {k: sl[k] for k in ("min", "max", "no_significant_drop") if k in sl}
        label = {"ok": "ok", "missing": "MISSING"}.get(verdict, "BLOCK" if sl.get("blocking") else "warn")
        lines.append(f"  {sid:<36} {val!s:<8} n={len(a):<3} {json.dumps(th):<36} {note:<28} {label}")
    return blocked, lines


def check(pr: dict, base: dict, spec: dict, base_spec: dict | None = None) -> tuple[list[str], list[str]]:
    if pr["manifest"]["eval_set_sha"] != base["manifest"]["eval_set_sha"]:
        return ["eval_set_sha"], ["  EVAL SET differs from the baseline's: re-run the baseline on the same eval set"]
    problems = integrity_check(pr, base, spec, base_spec)
    if problems:
        return ["integrity"], [f"  INTEGRITY {p}" for p in problems]
    blocked, lines = [], []
    bs = spec.get("bootstrap", {})
    for m in spec["metrics"]:
        mid, th, val = m["id"], m.get("threshold", {}), pr["metrics"].get(m["id"])
        verdict, note = "ok", ""
        if val is None:
            verdict = "missing"
        elif "max" in th and val > th["max"]:
            verdict = "fail"
        elif "min" in th and val < th["min"]:
            verdict = "fail"
        elif "max_increase" in th and base["metrics"].get(mid):
            if val > base["metrics"][mid] * (1 + th["max_increase"]):
                verdict = "fail"
        if th.get("no_significant_drop") and val is not None:
            a, b = per_item(pr, mid), per_item(base, mid)
            keys = sorted(set(a) & set(b))
            if not keys:
                # No per-item scores to pair: the baseline predates this metric (re-run it), or the
                # metric is not graded per row (e.g. pass_hat_5). Say so instead of crashing.
                verdict = "missing"
                note = ("baseline has no per-item scores: re-run the baseline" if a
                        else "not a per-row metric: no_significant_drop cannot apply")
            else:
                d, lo, hi = stats.paired_bootstrap([a[k] for k in keys], [b[k] for k in keys],
                                                   n_resamples=bs.get("n_resamples", 1000), rng=bs.get("rng", 2027))
                note = f"Δ {d:+.3f} CI [{lo:+.3f}, {hi:+.3f}]"
                if hi < 0:
                    verdict = "fail"
        if verdict != "ok" and m.get("blocking"):
            blocked.append(mid)
        label = {"ok": "ok", "missing": "MISSING"}.get(verdict, "BLOCK" if m.get("blocking") else "warn")
        lines.append(f"  {mid:<22} {val!s:<8} {json.dumps(th):<40} {note:<28} {label}")
    if spec.get("slices"):
        sb, sl = slice_check(pr, base, spec)
        blocked += sb
        lines += sl
    return blocked, lines


def main(argv: list[str] | None = None) -> int:
    utf8_stdio()
    ap = argparse.ArgumentParser(prog="lab9.gate")
    ap.add_argument("--pr", required=True)
    ap.add_argument("--baseline", required=True)
    ap.add_argument("--spec", default=str(SPEC))
    ap.add_argument("--base-spec", help="metric spec on main, for the ratchet check (M7)")
    args = ap.parse_args(argv)
    pr = json.loads(Path(args.pr).read_text(encoding="utf-8"))
    base = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
    base_spec = load_spec(Path(args.base_spec)) if args.base_spec else None
    blocked, lines = check(pr, base, load_spec(Path(args.spec)), base_spec)
    print(f"gate: PR {pr['run_id']} vs main {base['run_id']} · eval set {pr['manifest']['eval_set_sha']}")
    print("\n".join(lines))
    if blocked:
        print(f"exit 1 — blocked: {', '.join(blocked)}")
        return 1
    print("exit 0 — merge")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
