"""Deterministic MRP explosion: net requirements, lot sizing, planning time fence.

Single-run, level-by-level BOM explosion over a fixed horizon. Lot rules per item:
lot_for_lot (default), fixed_lot, max_stock (replenish up to a maximum stock level).
No stochastic lead time; scheduled receipts and on-hand are point values.
"""
import numpy as np

TOL = 1e-9


def mrp_run(data: dict) -> dict:
    if not isinstance(data, dict): raise ValueError('input object required')
    items = data.get('items') or []
    periods = data.get('periods') or []
    n = len(periods)
    if not 1 <= n <= 60 or not 1 <= len(items) <= 100: raise ValueError('1-60 periods, 1-100 items required')
    ids = [it['id'] for it in items]
    if len(set(ids)) != len(ids): raise ValueError('duplicate item ids')
    item = {it['id']: it for it in items}

    def vec(it, key):
        v = it.get(key, [0.0] * n)
        if not isinstance(v, list) or len(v) != n or any(not isinstance(x, (int, float)) or not np.isfinite(x) or x < 0 for x in v):
            raise ValueError(f"{it['id']}: {key} must be n nonnegative numbers")
        return [float(x) for x in v]

    for it in items:
        lead = it.get('lead_time', 0)
        if not isinstance(lead, int) or not 0 <= lead < n: raise ValueError(f"{it['id']}: lead_time must be an int in [0, n)")
        for dep, qty in (it.get('bom') or {}).items():
            if dep not in item: raise ValueError(f"{it['id']}: BOM child {dep} not defined")
            if not isinstance(qty, (int, float)) or qty <= 0: raise ValueError('BOM quantities must be positive')

    # level ordering: end items (no parents) first, components after their parents
    parent_of = {i: set() for i in ids}
    for it in items:
        for dep in (it.get('bom') or {}):
            parent_of.setdefault(dep, set()).add(it['id'])
    order = []
    seen = set()
    while len(order) < len(items):
        progressed = False
        for it in items:
            if it['id'] in seen: continue
            if not parent_of.get(it['id'], set()) <= seen: continue
            seen.add(it['id']); order.append(it['id']); progressed = True
        if not progressed: raise ValueError('BOM cycle detected')

    gross = {i: [0.0] * n for i in ids}
    # gross holds CHILD requirements derived from parent planned releases only;
    # each item's own demand is handled separately in its scheduling loop.
    releases = {}
    outputs = {}
    for pid in order:
        p = item[pid]
        lot_rule = p.get('lot_rule', 'lot_for_lot')
        if lot_rule not in ('lot_for_lot', 'fixed_lot', 'max_stock'): raise ValueError(f'{pid}: lot_rule must be lot_for_lot, fixed_lot or max_stock')
        fence = p.get('planning_time_fence', 0)
        if not isinstance(fence, int) or not 0 <= fence < n: raise ValueError(f'{pid}: planning_time_fence must be int in [0,n)')
        fixed = float(p.get('fixed_lot', 1) or 1)
        max_stock = float(p.get('max_stock', 0) or 0)
        if lot_rule == 'fixed_lot' and fixed <= 0: raise ValueError(f'{pid}: fixed_lot must be positive')
        if lot_rule == 'max_stock' and max_stock <= 0: raise ValueError(f'{pid}: max_stock must be positive')
        demand = vec(p, 'demand')
        receipts = vec(p, 'scheduled_receipts')
        onhand = float(p.get('initial_inventory', 0) or 0)
        net = [0.0] * n
        projected = [0.0] * n
        orders = [0.0] * n
        for t in range(n):
            gross_t = demand[t] + gross[pid][t]
            available = onhand + receipts[t]
            if t < fence:
                net[t] = 0.0
                projected[t] = available - gross_t
                onhand = projected[t]
                continue
            if lot_rule == 'max_stock':
                gap = max(0.0, max_stock - available + gross_t)
                if fixed > 1:
                    order = np.ceil(gap / fixed) * fixed
                else:
                    order = gap
            elif lot_rule == 'fixed_lot':
                need = max(0.0, gross_t - available)
                order = np.ceil(need / fixed) * fixed
            else:
                order = max(0.0, gross_t - available)
            net[t] = order
            projected[t] = available + order - gross_t
            onhand = projected[t]
        lead = int(p['lead_time'])
        rel = [0.0] * n
        for t in range(n):
            if t + lead < n: rel[t] = net[t + lead]
        releases[pid] = rel
        # children gross requirements come from parent releases at the child's level
        for dep, qty in (p.get('bom') or {}).items():
            for t in range(n):
                gross[dep][t] += rel[t] * qty
        outputs[pid] = {'net_requirements': [round(v, 6) for v in net],
                        'projected_on_hand': [round(v, 6) for v in projected],
                        'planned_receipts': [round(v, 6) for v in net],
                        'planned_releases': [round(v, 6) for v in rel],
                        'lot_rule': lot_rule}
    return {'status': 'COMPLETE', 'periods': periods, 'items': outputs, 'numeric_valid': True,
            'basis': 'Point MRP with BOM explosion level by level; lot sizing applied at each level; time fence freezes orders. No stochastic lead times or lot sizing beyond the three supported rules.'}
