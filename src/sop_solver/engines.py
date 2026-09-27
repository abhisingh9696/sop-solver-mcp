"""S&OP planning engines — the standalone capability extracted from the Wooden Space solve.

Every engine here is pure Python: scipy LP + numpy. No LLM, no network, no DB.
The MCP layer (server.py) exposes them as tools; the Jev gate (jev.py) decides
when a planning question is confident enough to act without asking again.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, linprog, milp


# ---------------------------------------------------------------------------
# Engine 1: plan_multi_period — produce/carry/backlog LP with shared resources
# ---------------------------------------------------------------------------

def plan_multi_period(data: dict, mode: str = "economic") -> dict:
    """Multi-period produce/carry/backlog LP.

    data = {
      "periods": ["W45", ...],
      "products": [{"id": "bed", "demand": [...], "resources": {"panelling": 22},
                    "holding_cost": 1, "backlog_cost": 100, "conversion_cost": 100,
                    "selling_price": 1000, "production_limit": [..] (optional)}],
      "resources": [{"id": "panelling", "capacity": [7200, ...]}],
    }
    Returns production/inventory/backlog paths, utilisation and per-period
    shadow prices (marginal value of one more capacity minute).
    """
    periods = data["periods"]
    prods = data["products"]
    res = data["resources"]
    nw = len(periods)
    npd = len(prods)
    nv = nw * npd * 3

    def V(pi, kind, t):
        base = {"P": 0, "I": 1, "U": 2}[kind]
        return (base * npd + pi) * nw + t

    c = np.zeros(nv)
    for pi, p in enumerate(prods):
        for t in range(nw):
            c[V(pi, "I", t)] = p.get("holding_cost", 1.0)
            c[V(pi, "U", t)] = p.get("backlog_cost", 100.0)
            if p.get("conversion_cost"):
                c[V(pi, "P", t)] = p["conversion_cost"]

    A_ub, b_ub, tags = [], [], []
    for r in res:
        for t in range(nw):
            row = np.zeros(nv)
            for pi, p in enumerate(prods):
                mins = p.get("resources", {}).get(r["id"], 0.0)
                if mins:
                    row[V(pi, "P", t)] = mins
            A_ub.append(row)
            b_ub.append(r["capacity"][t])
            tags.append(f"res:{r['id']}:{t}")
    for pi, p in enumerate(prods):
        for t in range(nw):
            if p.get("production_limit"):
                row = np.zeros(nv)
                row[V(pi, "P", t)] = 1.0
                A_ub.append(row)
                b_ub.append(p["production_limit"][t])
                tags.append(f"lim:{p['id']}:{t}")
    if mode == "service":
        for pi in range(npd):
            for t in range(nw):
                row = np.zeros(nv)
                row[V(pi, "U", t)] = 1.0
                A_ub.append(row)
                b_ub.append(0.0)
                tags.append(f"nobacklog:{prods[pi]['id']}:{t}")

    A_eq, b_eq = [], []
    for pi, p in enumerate(prods):
        for t in range(nw):
            row = np.zeros(nv)
            row[V(pi, "P", t)] = 1.0
            row[V(pi, "I", t)] = -1.0
            row[V(pi, "U", t)] = 1.0
            if t > 0:
                row[V(pi, "I", t - 1)] = 1.0
                row[V(pi, "U", t - 1)] = -1.0
            A_eq.append(row)
            b_eq.append(p["demand"][t])

    out = linprog(c, A_ub=np.array(A_ub), b_ub=np.array(b_ub),
                  A_eq=np.array(A_eq), b_eq=np.array(b_eq),
                  bounds=[(0, None)] * nv, method="highs")
    if out.status != 0:
        return {"status": "ERROR", "message": out.message}
    duals = {tag: round(float(-out.ineqlin.marginals[i]), 4) for i, tag in enumerate(tags)}
    result = {"status": "OPTIMAL", "mode": mode, "periods": periods,
              "objective": round(float(out.fun), 2), "products": {}, "resources": {},
              "resource_shadow_prices": duals}
    for pi, p in enumerate(prods):
        result["products"][p["id"]] = {
            "production": [round(out.x[V(pi, "P", t)], 1) for t in range(nw)],
            "inventory": [round(out.x[V(pi, "I", t)], 1) for t in range(nw)],
            "backlog": [round(out.x[V(pi, "U", t)], 1) for t in range(nw)],
        }
    for r in res:
        loads = [sum(p.get("resources", {}).get(r["id"], 0.0) * out.x[V(pi, "P", t)]
                     for pi, p in enumerate(prods)) for t in range(nw)]
        result["resources"][r["id"]] = {
            "capacity": r["capacity"],
            "load": [round(v, 1) for v in loads],
            "utilisation": [round(v / cap, 4) if cap else 0 for v, cap in zip(loads, r["capacity"])],
        }
    return result


# ---------------------------------------------------------------------------
# Engine 2: rough_cut — load-vs-capacity screen for a proposed plan
# ---------------------------------------------------------------------------

def rough_cut(data: dict, proposed_production: dict[str, list[float]]) -> dict:
    """Screen a proposed production plan against shared-resource capacity."""
    prods = data["products"]
    res = data["resources"]
    periods = data["periods"]
    rows = []
    for r in res:
        for t, per in enumerate(periods):
            load = sum(p.get("resources", {}).get(r["id"], 0.0)
                       * proposed_production.get(p["id"], [0] * len(periods))[t]
                       for p in prods)
            cap = r["capacity"][t]
            rows.append({"resource": r["id"], "period": per,
                         "load": round(load, 1), "capacity": cap,
                         "utilisation_pct": round(100 * load / cap, 1) if cap else None,
                         "overloaded": load > cap + 1e-6,
                         "shortfall": round(max(0.0, load - cap), 1)})
    return {"status": "OK", "rows": rows,
            "any_overload": any(x["overloaded"] for x in rows)}


# ---------------------------------------------------------------------------
# Engine 3: mrp_run — BOM explosion with net requirements and lot sizing
# ---------------------------------------------------------------------------

def mrp_run(data: dict, lot_size: str = "lot_for_lot", fixed_qty: float = 0.0) -> dict:
    """Explode product demand through each product's BOM into part-level
    gross/net requirements and planned orders (lot-for-lot or fixed qty)."""
    prods = data["products"]
    periods = data["periods"]
    nw = len(periods)
    parts: dict[str, list[tuple[str, float]]] = {}
    for p in prods:
        for part, qty in p.get("bom", {}).items():
            parts.setdefault(part, []).append((p["id"], qty))
    result = {}
    for part, parents in parts.items():
        gross = [0.0] * nw
        for pid, qty in parents:
            prod = next(x for x in prods if x["id"] == pid)
            for t in range(nw):
                gross[t] += qty * prod["demand"][t]
        on_hand = 0.0
        net, orders, ending = [], [], []
        for t in range(nw):
            available = on_hand
            netreq = max(0.0, gross[t] - available)
            net.append(round(netreq, 1))
            order = fixed_qty if (lot_size == "fixed" and netreq > 0) else netreq
            orders.append(round(order, 1))
            on_hand = available + order - gross[t]
            ending.append(round(on_hand, 1))
        result[part] = {"gross_requirements": [round(g, 1) for g in gross],
                        "net_requirements": net, "planned_orders": orders,
                        "ending_inventory": ending}
    return {"status": "OK", "lot_size": lot_size, "parts": result}


# ---------------------------------------------------------------------------
# Engine 4: inventory_policy — newsvendor / safety stock / reorder point
# ---------------------------------------------------------------------------

def inventory_policy(policy: str = "newsvendor", service_level: float = 0.95,
                     demand_std: float = 0.0, lead_time: int = 1,
                     unit_cost: float = 10.0, selling_price: float = 15.0,
                     salvage: float = 5.0) -> dict:
    """Newsvendor critical-ratio sizing, or safety stock / reorder point."""
    from scipy.stats import norm
    if policy == "newsvendor":
        cu = selling_price - unit_cost
        co = unit_cost - salvage
        ratio = cu / (cu + co) if cu + co > 0 else 0.0
        q = float(norm.ppf(ratio)) * demand_std if demand_std > 0 else 0.0
        return {"policy": "newsvendor", "critical_ratio": round(ratio, 4),
                "order_quantity": round(q, 1), "underage_cost": round(cu, 2),
                "overage_cost": round(co, 2)}
    z = float(norm.ppf(service_level))
    ss = z * demand_std * (lead_time ** 0.5)
    return {"policy": policy, "service_level": service_level, "z": round(z, 4),
            "safety_stock": round(ss, 1), "reorder_point": round(ss + demand_std * lead_time, 1)}


# ---------------------------------------------------------------------------
# Engine 5: plan_pnl — one-set-of-numbers finance view for a plan
# ---------------------------------------------------------------------------

def plan_pnl(data: dict, plan: dict) -> dict:
    """Revenue, conversion/holding/backlog cost and margin for a plan."""
    nw = len(data["periods"])
    revenue = conv = hold = back = 0.0
    for p in data["products"]:
        path = plan["products"].get(p["id"], {})
        for t in range(nw):
            produced = path.get("production", [0] * nw)[t]
            inv = path.get("inventory", [0] * nw)[t]
            backl = path.get("backlog", [0] * nw)[t]
            revenue += produced * p.get("selling_price", 0.0)
            conv += produced * p.get("conversion_cost", 0.0)
            hold += inv * p.get("holding_cost", 0.0)
            back += backl * p.get("backlog_cost", 0.0)
    total_cost = conv + hold + back
    margin = revenue - total_cost
    return {"revenue": round(revenue, 2), "conversion_cost": round(conv, 2),
            "holding_cost": round(hold, 2), "backlog_cost": round(back, 2),
            "total_cost": round(total_cost, 2), "margin": round(margin, 2),
            "margin_pct": round(100 * margin / revenue, 2) if revenue else 0.0}


# ---------------------------------------------------------------------------
# Engine 6: simulate — template shock scenarios with re-optimization
# ---------------------------------------------------------------------------

def simulate(data: dict, shocks: dict) -> dict:
    """Re-plan under demand / capacity shocks (shallow-copied, never mutates input)."""
    import copy
    d = copy.deepcopy(data)
    dm = shocks.get("demand_multiplier", 1.0)
    cm = shocks.get("capacity_multiplier", 1.0)
    if dm != 1.0:
        for p in d["products"]:
            p["demand"] = [round(v * dm, 1) for v in p["demand"]]
    if cm != 1.0:
        for r in d["resources"]:
            r["capacity"] = [round(v * cm, 1) for v in r["capacity"]]
    result = plan_multi_period(d)
    result["shocks_applied"] = {"demand_multiplier": dm, "capacity_multiplier": cm}
    return result


# ---------------------------------------------------------------------------
# Engine 7: forecast_ladder — naive/SMA/SES/Holt selected by rolling-origin MAE
# ---------------------------------------------------------------------------

def forecast_ladder(history: list[float], horizon: int = 4) -> dict:
    h = list(history)
    candidates = {"naive": lambda s: s[-1]}
    candidates["sma3"] = lambda s: sum(s[-3:]) / 3 if len(s) >= 3 else s[-1]

    def ses(s, alpha=0.3):
        f = s[0]
        for v in s[1:]:
            f = alpha * v + (1 - alpha) * f
        return f
    candidates["ses0.3"] = ses

    def holt(s, alpha=0.3, beta=0.1):
        level, trend = s[0], s[1] - s[0]
        for v in s[1:]:
            new_level = alpha * v + (1 - alpha) * (level + trend)
            trend = beta * (new_level - level) + (1 - beta) * trend
            level = new_level
        return level + trend
    candidates["holt"] = holt

    mae = {}
    for name, fn in candidates.items():
        errors = []
        for i in range(max(2, len(h) - 3), len(h)):
            errors.append(abs(fn(h[:i]) - h[i]))
        mae[name] = round(sum(errors) / len(errors), 3) if errors else None
    best = min((v, k) for k, v in mae.items() if v is not None)[1]
    return {"mae": mae, "selected": best,
            "forecast": [round(candidates[best](h), 1)] * horizon}


# ---------------------------------------------------------------------------
# Jev gate — decide when a planning question is confident enough to act
# ---------------------------------------------------------------------------

@dataclass
class JevCriteria:
    confidence: float          # model confidence in the chosen action
    probability: float         # estimated probability the action succeeds
    margin: float              # margin between top action and runner-up
    evidence_count: int        # supporting evidence records
    blockers: list[str] = field(default_factory=list)


def jev_evaluate(criteria: JevCriteria, gates: dict | None = None) -> dict:
    """The Jev decision gate, exactly as it behaved in the Wooden Space solve.

    Gates: confidence >= 0.85, probability >= 0.85, margin >= 0.2,
    evidence_count >= 2, no blockers. Pass = act; fail = ESCALATE_REASONING
    (ask the operator / run more analysis before acting).
    """
    g = gates or {}
    checks = {
        "confidence": criteria.confidence >= g.get("min_confidence", 0.85),
        "probability": criteria.probability >= g.get("min_probability", 0.85),
        "margin": criteria.margin >= g.get("min_margin", 0.2),
        "evidence": criteria.evidence_count >= g.get("min_evidence", 2),
        "blockers": len(criteria.blockers) == 0,
    }
    passed = all(checks.values())
    return {"gates_passed": passed, "checks": checks,
            "action_taken": "ACT" if passed else "ESCALATE_REASONING",
            "criteria": {"confidence": criteria.confidence, "probability": criteria.probability,
                         "margin": criteria.margin, "evidence_count": criteria.evidence_count,
                         "blockers": criteria.blockers}}
