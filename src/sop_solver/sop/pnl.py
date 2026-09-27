"""One-set-of-numbers finance view and template-based stress simulation.

plan_pnl: full PlanningInput -> optimize -> independent validation -> P&L/cash/CCC rows.
simulate: replay + re-optimize under demand/price/capacity shock templates.
"""
from .schema import PlanningInput
from .optimizer import optimize
from .critic import validate
from .finance import reconcile
from .simulation import simulate as replay


def plan_pnl(data: dict) -> dict:
    d = PlanningInput.model_validate(data)
    mode = data.get('mode', 'economic') if isinstance(data, dict) else 'economic'
    plan = optimize(d, mode)
    v = validate(d, plan)
    if not v['feasible']:
        return {'status': 'INFEASIBLE', 'violations': v['violations'], 'numeric_valid': False}
    fin = v['financials']
    kpi = {'revenue': fin['revenue'], 'cogs': fin['cogs'], 'gross_margin': fin['revenue'] - fin['cogs'],
           'ebitda': fin['ebitda'], 'ebit': fin['ebit'], 'planning_cost': fin['planning_cost'],
           'dso_days': fin['dso_days'], 'dio_days': fin['dio_days'], 'dpo_days': fin['dpo_days'],
           'ccc_days': fin['ccc_days'], 'minimum_closing_cash': fin['minimum_closing_cash'],
           'worst_stress_fill_rate': None}
    stress = replay(d, plan)
    kpi['worst_stress_fill_rate'] = stress['worst_fill_rate']
    kpi['service_fill_rates'] = v['fill_rates']
    kpi['capacity'] = v['capacity']
    return {'status': 'OPTIMAL', 'periods': d.periods, 'currency': d.currency,
            'finance_rows': fin['rows'], 'kpi': kpi, 'numeric_valid': True,
            'basis': fin['basis'] + ' Stress replay: fixed-plan demand multipliers; no Monte Carlo.'}


def simulate(data: dict) -> dict:
    """Template shocks: demand multipliers, capacity factor, price factor. Re-optimizes."""
    d = PlanningInput.model_validate(data['data'])
    scenarios = []
    base = plan_pnl(data['data'])
    templates = data.get('templates', ['base'])
    for name in templates:
        if name == 'base':
            scenarios.append({'template': 'base', 'kpi': base['kpi'], 'feasible': base['status'] == 'OPTIMAL'})
            continue
        # build a shocked copy via model_dump and JSON-safe mutation
        import copy
        alt = copy.deepcopy(data['data'])
        if name == 'demand_high' or name == 'demand_low':
            mult = {'demand_high': 1.2, 'demand_low': 0.8}[name]
            for p in alt['products']:
                p['demand'] = [v * mult for v in p['demand']]
        elif name == 'supplier_failure':
            for m in alt.get('materials', []):
                m['purchase_limit'] = [0.0] * len(alt['periods'])
                m['scheduled_receipts'] = [0.0] * len(alt['periods'])
        elif name == 'capacity_shock':
            for r in alt['resources']:
                r['capacity'] = [v * 0.8 for v in r['capacity']]
        elif name == 'price_cut':
            for p in alt['products']:
                p['selling_price'] = p['selling_price'] * 0.9
                p['demand'] = [v * 1.1 for v in p['demand']]
        else:
            raise ValueError(f'unknown template {name}; use base, demand_high, demand_low, supplier_failure, capacity_shock, price_cut')
        try:
            pn = plan_pnl(alt)
            scenarios.append({'template': name, 'kpi': pn['kpi'], 'feasible': pn['status'] == 'OPTIMAL',
                              'violations': pn.get('violations', [])})
        except ValueError as exc:
            scenarios.append({'template': name, 'feasible': False, 'error': str(exc)[:200]})
    best = None; worst = None
    feas = [s for s in scenarios if s.get('feasible')]
    def ebitda(s):
        v = s.get('kpi', {}).get('ebitda')
        return v if isinstance(v, (int, float)) else None
    if feas:
        with_ebitda = [s for s in feas if ebitda(s) is not None]
        if with_ebitda:
            best = max(with_ebitda, key=ebitda)
            worst = min(with_ebitda, key=ebitda)
    risk = None
    if best and worst and ebitda(best) is not None and ebitda(worst) is not None:
        risk = ebitda(best) - ebitda(worst)
    return {'scenarios': scenarios, 'best_ebitda': ebitda(best) if best else None,
            'worst_ebitda': ebitda(worst) if worst else None,
            'risk_exposure': risk,
            'numeric_valid': True,
            'basis': 'Deterministic template shocks, each re-optimized. Templates are illustrative, not calibrated probabilities.'}
