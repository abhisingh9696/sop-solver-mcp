"""Inventory policy engines: newsvendor, safety stock / reorder point, (s,S) simulation.

Deterministic, textbook formulas with declared assumptions. No demand forecasting
performed here: distributions or histories are supplied by the caller.
"""
import numpy as np
from scipy import stats

TOL = 1e-9


def newsvendor(data: dict) -> dict:
    if not isinstance(data, dict): raise ValueError('input object required')
    mu = data.get('demand_mean'); sigma = data.get('demand_std')
    cu = data.get('cost_underage'); co = data.get('cost_overage')
    for v in (mu, sigma, cu, co):
        if not isinstance(v, (int, float)) or not np.isfinite(v): raise ValueError('demand_mean/std and costs must be finite numbers')
    if sigma < 0 or cu <= 0 or co <= 0: raise ValueError('sigma must be >=0; costs must be positive')
    critical = cu / (cu + co)
    if sigma < TOL:
        q = mu
        expected_cost = 0.0
    else:
        q = mu + sigma * float(stats.norm.ppf(critical))
        z = float(stats.norm.ppf(critical))
        # standard newsvendor expected cost: co*E[(q-D)+] + cu*E[(D-q)+]
        r = (q - mu) / sigma
        expected_cost = sigma * (co * (stats.norm.pdf(r) + r * stats.norm.cdf(r)) +
                                 cu * (stats.norm.pdf(-r) - r * stats.norm.cdf(-r)))
    return {'order_quantity': float(q), 'critical_fractile': float(critical),
            'implied_service_level': float(critical), 'expected_mismatch_cost': float(expected_cost),
            'numeric_valid': True,
            'basis': 'Normal demand assumption; cost_underage = lost margin per unit short, cost_overage = loss per unit unsold. No salvage value modeling beyond cost_overage.'}


def safety_stock(data: dict) -> dict:
    if not isinstance(data, dict): raise ValueError('input object required')
    service = data.get('service_level')
    lead_time = data.get('lead_time_periods')
    demand_std = data.get('demand_std_per_period')
    lead_time_std = data.get('lead_time_std_periods', 0.0)
    avg_demand = data.get('avg_demand_per_period', 0.0)
    if not isinstance(service, (int, float)) or not 0 < service < 1: raise ValueError('service_level must be in (0,1)')
    if not isinstance(lead_time, (int, float)) or lead_time < 0 or not np.isfinite(lead_time): raise ValueError('lead_time_periods must be nonnegative')
    if not isinstance(demand_std, (int, float)) or demand_std < 0 or not np.isfinite(demand_std): raise ValueError('demand_std must be nonnegative')
    z = float(stats.norm.ppf(service))
    demand_var = demand_std ** 2 * lead_time
    lead_var = (avg_demand ** 2) * (lead_time_std ** 2)
    ss = z * np.sqrt(max(0.0, demand_var + lead_var))
    rop = avg_demand * lead_time + ss
    return {'z_score': z, 'safety_stock': float(ss), 'reorder_point': float(rop),
            'cycle_stock_basis': float(avg_demand * lead_time),
            'numeric_valid': True,
            'basis': 'Normal demand and lead time; demand during lead time variance = L*sd_d^2 + d^2*sd_L^2. No order cost/EOQ sizing; cycle stock sized by the caller.'}


def ss_policy_simulate(data: dict) -> dict:
    """(s,S) policy replay over a supplied demand series (order-up-to with reorder point)."""
    if not isinstance(data, dict): raise ValueError('input object required')
    demand = data.get('demand_history')
    if not isinstance(demand, list) or not 1 <= len(demand) <= 240 or any(not isinstance(x, (int, float)) or not np.isfinite(x) or x < 0 for x in demand):
        raise ValueError('demand_history must be 1-240 nonnegative numbers')
    s = data.get('order_up_to'); r = data.get('reorder_point')
    init = float(data.get('initial_inventory', 0) or 0)
    lead = data.get('lead_time_periods', 0)
    if not isinstance(lead, int) or not 0 <= lead <= 30: raise ValueError('lead_time_periods must be int 0-30')
    if not isinstance(s, (int, float)) or not isinstance(r, (int, float)) or s < 0 or r < 0 or r > s:
        raise ValueError('reorder_point and order_up_to must satisfy 0 <= r <= s')
    if init < 0: raise ValueError('initial_inventory must be nonnegative')
    inv = init; pipeline = [0.0] * (lead + 1); rows = []
    fill_total = 0; demand_total = 0; orders_placed = 0; stockouts = 0
    for t, d in enumerate(demand):
        inv += pipeline.pop(0)
        ship = min(inv, d); lost = d - ship
        inv -= ship; demand_total += d; fill_total += ship
        if lost > TOL: stockouts += 1
        if inv <= r + TOL and inv < s:
            order = s - inv; orders_placed += 1
        else:
            order = 0.0
        pipeline.append(order)
        rows.append({'period': t, 'demand': float(d), 'filled': float(ship), 'lost': float(lost),
                     'closing_inventory': float(inv), 'order_placed': float(order)})
    return {'fill_rate': float(fill_total / demand_total) if demand_total else 1.0,
            'lost_sales': float(demand_total - fill_total), 'stockout_periods': stockouts,
            'orders_placed': orders_placed, 'rows': rows, 'numeric_valid': True,
            'basis': 'Deterministic replay of the (s,S) policy on supplied history; order arrives after lead_time_periods; unfilled demand is lost. No stochastic claim.'}
