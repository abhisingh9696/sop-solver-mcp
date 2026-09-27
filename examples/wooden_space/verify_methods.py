"""Smoke-test every one of the 24 analytical methods — no case workflow needed.

Run:  python examples/wooden_space/verify_methods.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))

from sop_solver import engines
from sop_solver.method_schemas import validate

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ws_data

HIST = [150, 250, 100, 350, 150, 100, 50, 50, 120, 210, 95, 300, 160, 90, 60, 55,
        170, 240, 110, 340, 140, 105, 65, 52]
SEASONAL = [10, 20, 30, 40, 12, 24, 36, 48, 11, 22, 33, 44, 13, 26, 39, 52,
            14, 28, 42, 56, 12, 24, 36, 48]

FULL = ws_data.build('DOCX')
PLANNER = ws_data.build_planner('DOCX')


def run(method, inputs, label):
    inputs = validate(method, inputs)
    out = engines.run(method, inputs)
    assert isinstance(out, dict) and out.get('numeric_valid', True) is not False, \
        f'{method} FAILED: {out}'
    print(f'{label:28s} OK  {method}')


def main():
    run('descriptive', {'values': [12.0, 15.0, 9.0, 22.0, 18.0]}, '1. descriptive')
    run('formula', {'parameters': {'a': 4, 'b': 7},
                    'expressions': {'c': 'a + b', 'd': 'sqrt(c)'},
                    'checks': [{'left': 'd', 'right': '3.3166', 'relation': 'ge',
                                'tolerance': 0.01}]}, '2. formula')
    run('sensitivity', {'parameters': {'price': 100, 'cost': 60},
                        'expressions': {'margin': 'price - cost'},
                        'scenarios': [{'name': 'base', 'parameters': {}},
                                      {'name': 'up', 'parameters': {'price': 120}}]},
        '3. sensitivity')
    run('cashflow', {'cashflows': [-1000, 300, 400, 500, 600],
                     'discount_rate': 0.10}, '4. cashflow')
    run('linear_program', {'c': [-3, -2], 'A_ub': [[1, 1], [2, 1]], 'b_ub': [4, 5],
                           'bounds': [[0, None], [0, None]]}, '5. linear_program')
    run('milp', {'c': [3, 2], 'A_ub': [[2, 1]], 'b_ub': [4],
                 'bounds': [[0, None], [0, None]], 'integrality': [1, 0]}, '6. milp')
    run('welch_ttest', {'a': [23, 20, 22, 25, 24], 'b': [18, 19, 17, 20, 16]},
        '7. welch_ttest')
    run('ols', {'x': [[1, 2], [2, 4], [3, 5], [4, 4], [5, 5]],
                'y': [3, 7, 8, 10, 12]},
        '8. ols')
    run('forecast', {'history': HIST, 'horizon': 4}, '9. forecast')
    run('sop_plan', {'data': FULL, 'mode': 'economic'}, '10. sop_plan')
    run('plan_multi_period', {'data': PLANNER}, '11. plan_multi_period')
    rough = json.loads(json.dumps(PLANNER))
    for p in rough['products']:
        p['plan'] = p['demand']  # propose demand-as-production; expect overload
    run('rough_cut', {'data': rough}, '12. rough_cut')
    run('mrp_run', {'data': {
        'periods': ws_data.PERIODS,
        'items': [
            {'id': 'bed', 'demand': FULL['products'][0]['demand'], 'lead_time': 0,
             'bom': {'head_board': 1, 'leg_board': 1, 'centre_board': 1}},
            {'id': 'table', 'demand': FULL['products'][1]['demand'], 'lead_time': 0,
             'bom': {'top_board': 1, 'table_leg': 4}},
            {'id': 'head_board', 'lead_time': 1, 'lot_rule': 'fixed_lot', 'fixed_lot': 100},
            {'id': 'leg_board', 'lead_time': 1, 'lot_rule': 'max_stock', 'max_stock': 600},
            {'id': 'centre_board', 'lead_time': 1},
            {'id': 'top_board', 'lead_time': 1},
            {'id': 'table_leg', 'lead_time': 1, 'lot_rule': 'fixed_lot', 'fixed_lot': 500},
        ]}}, '13. mrp_run')
    run('inventory_policy', {'policy': 'newsvendor',
                             'data': {'demand_mean': 200, 'demand_std': 33,
                                      'cost_underage': 9, 'cost_overage': 1}},
        '14a. inventory new')
    run('inventory_policy', {'policy': 'safety_stock',
                             'data': {'service_level': 0.95, 'lead_time_periods': 2,
                                      'demand_std_per_period': 40,
                                      'avg_demand_per_period': 200}},
        '14b. inventory ss')
    run('inventory_policy', {'policy': 'ss_policy',
                             'data': {'demand_history': HIST, 'order_up_to': 300,
                                      'reorder_point': 150, 'initial_inventory': 200,
                                      'lead_time_periods': 1}},
        '14c. inventory ss-sim')
    run('plan_pnl', {'data': FULL}, '15. plan_pnl')
    run('simulate', {'data': FULL, 'templates': ['base', 'demand_high', 'capacity_shock']},
        '16. simulate')
    run('holt', {'history': HIST, 'horizon': 4}, '17. holt')
    run('holt_winters', {'history': SEASONAL, 'horizon': 4, 'season': 4}, '18. holt_winters')
    run('forecast_ladder', {'history': SEASONAL, 'horizon': 4, 'season': 4}, '19. forecast_ladder')
    run('forecast_adjust', {'forecast': [100, 110, 120], 'adjustments': {'1': 1.1},
                            'note': 'promo week 2'}, '20. forecast_adjust')
    run('forecast_track', {'history': HIST[:12], 'forecast': [h * 0.95 for h in HIST[:12]]},
        '21. forecast_track')
    run('arima', {'data': {'history': SEASONAL, 'horizon': 4, 'order': [2, 1, 1]}},
        '22. arima')
    run('regression_forecast', {'data': {
        'history': [120, 135, 128, 150, 142, 160, 155, 168, 172, 180],
        'features': {'promo': [0, 1, 0, 1, 0, 1, 0, 1, 0, 1]},
        'future_features': {'promo': [0, 1, 0]}}}, '23. regression_forecast')
    run('multi_echelon_inventory', {'data': {
        'stores': [
            {'id': 's1', 'avg_demand_per_period': 100, 'demand_std_per_period': 20,
             'lead_time_periods': 2, 'replenishment_lead_time_periods': 1},
            {'id': 's2', 'avg_demand_per_period': 150, 'demand_std_per_period': 25,
             'lead_time_periods': 2, 'replenishment_lead_time_periods': 1},
        ],
        'warehouse': {'demand_std_per_period': 15, 'lead_time_periods': 2},
        'service_level': 0.95}}, '24. multi_echelon_inventory')

    # headline reproductions from the real Wooden Space solve
    plan = engines.run('plan_multi_period', validate('plan_multi_period', {'data': PLANNER}))
    pan = plan['resources']['panelling']
    assert abs(max(pan['utilization'][:6]) - 1.0) < 1e-4, 'panelling should saturate W45-50'
    print('\nheadline: panelling utilisation W45-50 =',
          [round(u, 2) for u in pan['utilization'][:6]],
          '| objective', round(plan['objective'], 1))
    print('\nALL 24 METHODS VERIFIED')


if __name__ == '__main__':
    main()
