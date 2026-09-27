"""Multi-period production planning LP and rough-cut capacity screening.

plan_multi_period: produce/carry/backlog per product per period with shared resources,
optional overtime (Saturday) capacity, and chase/level strategy shaping.
rough_cut: load-vs-capacity screen for a proposed production plan (no optimization).

Both are deterministic LP/arithmetic tools; business interpretation stays with the host.
"""
import numpy as np
from scipy.optimize import linprog

TOL = 1e-6


def _clean(d):
    if not isinstance(d, dict): raise ValueError('input object required')
    return d


def plan_multi_period(data: dict) -> dict:
    d = _clean(data)
    products = d.get('products') or []
    resources = d.get('resources') or []
    periods = d.get('periods') or []
    strategy = d.get('strategy', 'economic')
    if strategy not in ('economic', 'chase', 'level'):
        raise ValueError("strategy must be economic, chase or level")
    n = len(periods)
    if not 1 <= n <= 60: raise ValueError('1-60 periods required')
    if not 1 <= len(products) <= 50: raise ValueError('1-50 products required')
    if not resources: raise ValueError('at least one shared resource required')
    pid = [p['id'] for p in products]
    if len(set(pid)) != len(pid): raise ValueError('duplicate product ids')
    rid = [r['id'] for r in resources]
    if len(set(rid)) != len(rid): raise ValueError('duplicate resource ids')

    def vec(p, key, default=None):
        v = p.get(key, default)
        if v is None:
            raise ValueError(f"{p.get('id')}: {key} required")
        if not isinstance(v, list) or len(v) != n or any(not isinstance(x, (int, float)) or not np.isfinite(x) or x < 0 for x in v):
            raise ValueError(f"{p.get('id')}: {key} must be n nonnegative numbers")
        return [float(x) for x in v]

    # Variable layout per product: produce[t], carry[t], backlog[t]; per resource: overtime[t]
    nv = len(products) * 3 * n + len(resources) * n
    base = {pid[i]: i * 3 * n for i in range(len(products))}
    ov_base = len(products) * 3 * n
    c = np.zeros(nv)
    aub_rows = []; bub = []; aeq_rows = []; beq = []

    def add_eq(coeffs, val):
        r = np.zeros(nv)
        for idx, v in coeffs.items(): r[idx] = v
        aeq_rows.append(r); beq.append(val)

    def add_ub(coeffs, hi):
        r = np.zeros(nv)
        for idx, v in coeffs.items(): r[idx] = v
        aub_rows.append(r); bub.append(hi)

    # cost weights per strategy
    hold_w = 1.0; back_w = 1.0; ov_w = 1.0
    if strategy == 'chase':   # discourage inventory build: holding is expensive relative to backlog
        hold_w = 3.0
    elif strategy == 'level': # smooth production changes
        pass

    bounds = []
    for i, p in enumerate(products):
        b = base[p['id']]
        demand = vec(p, 'demand')
        init = float(p.get('initial_inventory', 0) or 0)
        init_back = float(p.get('initial_backlog', 0) or 0)
        hold = float(p.get('holding_cost', 1) or 0)
        back = float(p.get('backlog_cost', 1) or 0)
        prod_cost = float(p.get('production_cost', 0) or 0)
        for t in range(n):
            idx = b + 3 * t
            c[idx] = prod_cost
            c[idx + 1] = hold_w * hold
            c[idx + 2] = back_w * back
            bounds.append((0, None)); bounds.append((0, None)); bounds.append((0, None))
            coeffs = {idx: 1.0, idx + 1: -1.0, idx + 2: 1.0}
            if t > 0: coeffs[b + 3 * (t - 1) + 1] = 1.0
            if t > 0: coeffs[b + 3 * (t - 1) + 2] = -1.0
            rhs_val = demand[t] - (init if t == 0 else 0.0) + (init_back if t == 0 else 0.0)
            add_eq(coeffs, rhs_val)
        # terminal backlog policy: no open backlog at horizon end
        add_eq({b + 3 * (n - 1) + 2: 1.0}, 0.0)
        # level strategy: bound period-over-period production swings at +/-25% of demand
        if strategy == 'level':
            for t in range(1, n):
                bound = 0.25 * max(demand[t], demand[t - 1], 1.0)
                add_ub({b + 3 * t: 1.0, b + 3 * (t - 1): -1.0}, bound)
                add_ub({b + 3 * (t - 1): 1.0, b + 3 * t: -1.0}, bound)

    for r in resources:
        cap = r.get('capacity')
        if not isinstance(cap, list) or len(cap) != n or any(not isinstance(x, (int, float)) or x < 0 for x in cap):
            raise ValueError(f"{r.get('id')}: capacity must be n nonnegative numbers")
        extra = r.get('overtime_capacity', [0.0] * n)
        if not isinstance(extra, list) or len(extra) != n: raise ValueError(f"{r.get('id')}: overtime_capacity length mismatch")
        ov_cost = float(r.get('overtime_cost', 1) or 0)
        for t in range(n):
            oidx = ov_base + rid.index(r['id']) * n + t
            c[oidx] = ov_w * ov_cost
            bounds.append((0, float(extra[t])))
            coeffs = {oidx: -1.0}
            for p in products:
                hours = p.get('resources', {}).get(r['id'])
                if hours is None: raise ValueError(f"{p['id']}: missing hours for resource {r['id']}")
                if not isinstance(hours, (int, float)) or hours < 0: raise ValueError('hours must be nonnegative')
                coeffs[base[p['id']] + 3 * t] = float(hours)
            add_ub(coeffs, float(cap[t]))

    A_ub = np.array(aub_rows) if aub_rows else None
    b_ub = np.array(bub) if bub else None
    A_eq = np.array(aeq_rows) if aeq_rows else None
    b_eq = np.array(beq) if beq else None
    res = linprog(c, A_ub=A_ub, b_ub=b_ub, A_eq=A_eq, b_eq=b_eq, bounds=bounds, method='highs')
    if not res.success:
        return {'status': 'INFEASIBLE' if res.status == 2 else 'NOT_OPTIMAL', 'solver_status': int(res.status),
                'message': str(res.message), 'numeric_valid': False}
    x = res.x
    out_products = {}
    for p in products:
        b = base[p['id']]
        out_products[p['id']] = {
            'production': [float(v) for v in x[b:b + 3 * n:3]],
            'carry': [float(v) for v in x[b + 1:b + 3 * n:3]],
            'backlog': [float(v) for v in x[b + 2:b + 3 * n:3]]}
    out_resources = {}
    for j, r in enumerate(resources):
        out_resources[r['id']] = {
            'overtime_used': [float(v) for v in x[ov_base + j * n:ov_base + (j + 1) * n]],
            'load': [], 'capacity': [float(v) for v in r['capacity']]}
        for t in range(n):
            load = sum(out_products[p['id']]['production'][t] * p.get('resources', {}).get(r['id'], 0) for p in products)
            out_resources[r['id']]['load'].append(float(load))
        out_resources[r['id']]['utilization'] = [l / cap if cap > 0 else (0.0 if l < TOL else None)
                                                 for l, cap in zip(out_resources[r['id']]['load'], r['capacity'])]
    duals = {}
    if getattr(res, 'ineqlin', None) is not None and getattr(res.ineqlin, 'marginals', None) is not None:
        duals['resource_shadow_prices'] = [float(v) for v in res.ineqlin.marginals]
    return {'status': 'OPTIMAL', 'periods': periods, 'strategy': strategy,
            'products': out_products, 'resources': out_resources,
            'objective': float(res.fun), 'duals': duals,
            'numeric_valid': True,
            'warning': 'Optimality applies to the supplied formulation. Shadow prices are per inequality row in row order (capacity rows first), sign per scipy convention (>=0 for binding upper bounds). Chase/level are cost-shaping heuristics, not firm policies.'}


def rough_cut(data: dict) -> dict:
    d = _clean(data)
    products = d.get('products') or []
    resources = d.get('resources') or []
    periods = d.get('periods') or []
    n = len(periods)
    if not 1 <= n <= 60 or not products or not resources: raise ValueError('periods, products and resources required')
    per_resource = []
    for r in resources:
        cap = r.get('capacity')
        if not isinstance(cap, list) or len(cap) != n: raise ValueError(f"{r.get('id')}: capacity length mismatch")
        load = [0.0] * n
        for p in products:
            plan = p.get('plan')
            if not isinstance(plan, list) or len(plan) != n: raise ValueError(f"{p.get('id')}: plan length mismatch")
            hours = p.get('resources', {}).get(r['id'], 0)
            for t in range(n): load[t] += float(plan[t]) * float(hours)
        util = [l / c if c > 0 else (0.0 if l < TOL else None) for l, c in zip(load, cap)]
        over = [periods[t] for t in range(n) if util[t] is not None and util[t] > 1 + TOL]
        per_resource.append({'resource': r['id'], 'load': [round(v, 4) for v in load],
                             'capacity': [float(c) for c in cap],
                             'utilization': [None if v is None else round(v, 4) for v in util],
                             'overloaded_periods': over})
    overloaded = any(x['overloaded_periods'] for x in per_resource)
    return {'status': 'FEASIBLE' if not overloaded else 'OVERLOADED', 'resources': per_resource,
            'numeric_valid': True,
            'suggestion': None if not overloaded else 'Overloaded periods: consider pull-forward into earlier idle periods, optional Saturday (overtime) capacity, or a 1-2 period delivery slide — rerun plan_multi_period with overtime_capacity to quantify.'}
