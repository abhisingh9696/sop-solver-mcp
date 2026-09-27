"""Wooden Space case data — full planning contract.

Products: bed, table. Stages: cutting, panelling, assembly_packing.
Materials: wood_board, table_leg. 8 weeks (W45-W52).

Two demand variants reproduce the real case's open conflict:
  DOCX (conservative): beds [150,250,100,350,150,100,50,50]
  XLSX (lower):        beds [150,250,100,150,150,100,50,50]
Both are valid PlanningInput dictionaries.
"""
from __future__ import annotations

N = 8
PERIODS = ['W45', 'W46', 'W47', 'W48', 'W49', 'W50', 'W51', 'W52']

# per-unit resource minutes (best-case routing from the case's T1/T2 tables)
BED_RES = {'cutting': 12, 'panelling': 22, 'assembly_packing': 33}
TABLE_RES = {'cutting': 7, 'panelling': 18, 'assembly_packing': 13}

# machine fleet: 2 cutting x 2400, 3 panelling x 2400, 22 people x 2400
CAPACITY = {
    'cutting': [4800] * N,
    'panelling': [7200] * N,
    'assembly_packing': [52800] * N,
}

# the optional 6th working day: +480 min/machine, +480 person-min/person
SATURDAY = {
    'cutting': 960,
    'panelling': 1440,
    'assembly_packing': 10560,
}


def _product(pid, unit, demand, price, conv, res, bom):
    return {
        'id': pid, 'unit': unit,
        'demand': demand,
        'history': demand,
        'initial_inventory': 0, 'initial_inventory_value': 0, 'initial_backlog': 0,
        'selling_price': price, 'conversion_cost': conv,
        'holding_cost': 5, 'backlog_cost': 200,
        'outsource_cost': price * 1.25, 'outsource_limit': [60] * N,
        'production_limit': [1000] * N,
        'safety_stock': [0] * N,
        'resources': res, 'bom': bom,
    }


def build(variant: str = 'DOCX') -> dict:
    beds = [150, 250, 100, 350, 150, 100, 50, 50] if variant == 'DOCX' else \
           [150, 250, 100, 150, 150, 100, 50, 50]
    tables = [200, 150, 200, 300, 250, 50, 20, 150]
    return {
        'schema_version': '1.0',
        'dataset_id': f'wooden_space_8week_{variant.lower()}',
        'currency': 'INR',
        'bucket': 'week',
        'periods': PERIODS,
        'products': [
            _product('bed', 'unit', beds, 12000, 300, BED_RES, {'wood_board': 3}),
            _product('table', 'unit', tables, 4500, 120, TABLE_RES,
                     {'wood_board': 1, 'table_leg': 4}),
        ],
        'resources': [
            {'id': 'cutting', 'capacity': CAPACITY['cutting']},
            {'id': 'panelling', 'capacity': CAPACITY['panelling']},
            {'id': 'assembly_packing', 'capacity': CAPACITY['assembly_packing']},
        ],
        'materials': [
            {'id': 'wood_board', 'initial_inventory': 500, 'unit_cost': 400,
             'lead_time_buckets': 1, 'scheduled_receipts': [0] * N,
             'purchase_limit': [2000] * N},
            {'id': 'table_leg', 'initial_inventory': 2000, 'unit_cost': 60,
             'lead_time_buckets': 1, 'scheduled_receipts': [0] * N,
             'purchase_limit': [8000] * N},
        ],
        'finance': {
            'opening_cash': 500000, 'opening_ar': 200000, 'opening_ap': 100000,
            'ar_lag_buckets': 1, 'ap_lag_buckets': 1,
            'fixed_opex': [150000] * N, 'capex': [0] * N,
            'depreciation': [50000] * N, 'financing_inflows': [0] * N,
            'minimum_cash': 100000,
        },
        'policy': {
            'min_on_time_fill_rate': 0.85,
            'require_zero_terminal_backlog': True,
            'gate_min_confidence': 0.85,
            'gate_min_top_probability': 0.85,
            'gate_min_margin': 0.20,
            'stress_demand_multipliers': [1.0, 1.2],
        },
    }


def build_planner(variant: str = 'DOCX', saturdays: tuple = ()) -> dict:
    """plan_multi_period input: produce/carry/backlog LP with shared resources.

    saturdays: tuple of period indices where the optional 6th day is worked."""
    full = build(variant)
    products = []
    for p in full['products']:
        products.append({'id': p['id'], 'demand': p['demand'],
                         'holding_cost': p['holding_cost'], 'backlog_cost': p['backlog_cost'],
                         'resources': p['resources']})
    resources = []
    for r in full['resources']:
        cap = list(r['capacity'])
        ot = [0.0] * N
        for t in saturdays:
            ot[t] = float(SATURDAY[r['id']])
        resources.append({'id': r['id'], 'capacity': cap,
                          'overtime_capacity': ot, 'overtime_cost': 1.5})
    return {'periods': PERIODS, 'products': products, 'resources': resources,
            'strategy': 'economic'}
