"""End-to-end verification of every engine against the Wooden Space case.

Run:  python examples/wooden_space/verify.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from sop_solver import engines
from sop_solver.engines import JevCriteria, jev_evaluate


def main() -> None:
    data = json.loads((Path(__file__).parent / "case_data.json").read_text())

    # 1. Solve the 8-week cycle
    plan = engines.plan_multi_period(data)
    assert plan["status"] == "OPTIMAL", plan
    pan = plan["resources"]["panelling"]["utilisation"]
    print("1. plan_multi_period: objective", plan["objective"],
          "| panelling utilisation W45-W50:", pan[:6])

    # 2. Screen an overloaded plan
    over = {"bed": [400] * 8, "table": [300] * 8}
    screen = engines.rough_cut(data, over)
    assert screen["any_overload"]
    print("2. rough_cut: any_overload =", screen["any_overload"],
          "| worst shortfall =", max(x["shortfall"] for x in screen["rows"]))

    # 3. MRP
    mrp = engines.mrp_run(data)
    assert "top_board" in mrp["parts"]
    print("3. mrp_run: parts =", list(mrp["parts"]),
          "| top_board planned W45 =", mrp["parts"]["top_board"]["planned_orders"][0])

    # 4. Inventory policies
    nv = engines.inventory_policy(policy="newsvendor", demand_std=60, unit_cost=100,
                                  selling_price=1000, salvage=50)
    assert 0 < nv["critical_ratio"] < 1
    print("4. newsvendor: ratio =", nv["critical_ratio"], "| order =", nv["order_quantity"])
    ss = engines.inventory_policy(policy="reorder_point", service_level=0.95,
                                  demand_std=60, lead_time=4)
    print("   safety stock:", ss["safety_stock"], "| rop:", ss["reorder_point"])

    # 5. P&L
    pnl = engines.plan_pnl(data, plan)
    assert pnl["revenue"] > 0
    print("5. plan_pnl: revenue =", pnl["revenue"], "| margin =", pnl["margin"],
          f"({pnl['margin_pct']}%)")

    # 6. Simulation
    sim = engines.simulate(data, {"demand_multiplier": 1.2, "capacity_multiplier": 0.9})
    assert sim["status"] == "OPTIMAL"
    print("6. simulate: objective =", sim["objective"], "| shocks =", sim["shocks_applied"])

    # 7. Forecast ladder
    fl = engines.forecast_ladder([150, 250, 100, 350, 150, 100, 50, 50])
    assert fl["selected"]
    print("7. forecast_ladder: selected =", fl["selected"], "| mae =", fl["mae"])

    # 8. Jev gate — one pass, one fail
    ok = jev_evaluate(JevCriteria(confidence=0.9, probability=0.9, margin=0.3, evidence_count=3))
    assert ok["gates_passed"]
    print("8. jev (pass):", ok["action_taken"])
    esc = jev_evaluate(JevCriteria(confidence=0.7, probability=0.8, margin=0.1,
                                   evidence_count=1, blockers=["demand_conflict:week48"]))
    assert not esc["gates_passed"]
    print("   jev (fail):", esc["action_taken"], "| blockers:", esc["criteria"]["blockers"])

    print("\nALL VERIFICATIONS PASSED")


if __name__ == "__main__":
    main()
