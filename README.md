# sop-solver-mcp

Standalone **Sales & Operations Planning (S&OP)** MCP server. Pure-Python planning engines (scipy LP) plus a **Jev decision gate** — extracted from a real 8-week production-planning solve (the Wooden Space case). No LLM, no database, no network: every engine runs locally.

## Install

```bash
pip install git+https://github.com/abhisingh9696/sop-solver-mcp.git
```

## Run as an MCP server (stdio)

```bash
sop-solver
```

Claude Desktop / Hermes `mcp_servers` config:

```json
{
  "mcp_servers": {
    "sop-solver": {
      "command": "sop-solver",
      "transport": "stdio"
    }
  }
}
```

## Tools

| Tool | What it does |
|------|--------------|
| `plan_multi_period(data, mode)` | Produce/carry/backlog LP over N periods with shared resources. Returns production/inventory/backlog paths, utilisation, and **shadow prices** per resource-period (marginal value of one more capacity minute). `mode="service"` forces zero backlog. |
| `rough_cut(data, proposed_production)` | Screen any proposed plan against capacity; flags overloaded periods and shortfalls. |
| `mrp_run(data, lot_size, fixed_qty)` | BOM explosion into part-level gross/net requirements and planned orders (lot-for-lot or fixed). |
| `inventory_policy(...)` | Newsvendor critical-ratio sizing, or safety stock / reorder point at a target service level. |
| `plan_pnl(data, plan)` | Revenue, conversion/holding/backlog cost and margin for a plan from `plan_multi_period`. |
| `simulate(data, shocks)` | Re-plan under `{"demand_multiplier": 1.2, "capacity_multiplier": 0.9}` shocks. |
| `forecast_ladder(history, horizon)` | Naive / SMA3 / SES / Holt selected by rolling-origin MAE. |
| `jev_evaluate(criteria)` | The Jev decision gate — see below. |

## The Jev decision gate

Jev answers one question: **should the agent act now, or go gather more evidence first?**

```
gates_passed = confidence >= 0.85
           and probability >= 0.85
           and margin >= 0.2        # top action must beat the runner-up by this much
           and evidence_count >= 2  # at least two supporting evidence records
           and no blockers

passed  -> ACT
failed  -> ESCALATE_REASONING
```

### How Jev drove the Wooden Space solve (real trace)

The case: 2 products (beds, tables), 4 stages (cutting → panelling → assembly → packing), 8 weeks (45–52), 3 panelling machines, 2 cutting machines, 22-person workforce with a skill matrix, an optional 6th working day, and a lateness tolerance ladder (1 week OK, 2 manageable, 3 undesirable).

| Step | Jev call | Inputs | Outcome |
|------|----------|--------|---------|
| 1 | Act on the LP baseline? | conf 0.9, prob 0.9, margin 0.3, evidence 4 | **ACT** — plan_multi_period ran, panelling pinned at 100% W45–W50 |
| 2 | Declare panelling the bottleneck? | conf 0.92, prob 0.9, margin 0.35, evidence 4 | **ACT** — two independent models agreed |
| 3 | Pick the minimum Saturday set? | conf 0.86, prob 0.85, margin 0.25, evidence 3 | **ACT** — greedy search found weeks 45–49 (DOCX) / 45–47 (XLSX) |
| 4 | Ship before resolving the week-48 demand conflict? | conf 0.8, prob 0.85, margin 0.3, evidence 2, **blocker: `demand_conflict:week48`** | **ESCALATE_REASONING** — blocker present, so the final deliverable was marked *conditional* with both variants solved instead of guessing |

That last call is the point of Jev: **a blocker beats confidence.** Even a 0.8-confident answer does not act when a material fact is unresolved — it surfaces the conflict and ships a conditional result.

## Worked example (this repo ships it)

`examples/wooden_space/` contains the full case input and a verification script:

```bash
python examples/wooden_space/verify.py
```

Output (real):

```
1. plan_multi_period: objective 312954.55 | panelling utilisation W45-W50: [1.0 1.0 1.0 1.0 1.0 1.0]
2. rough_cut: any_overload = True | worst shortfall = 7000
3. mrp_run: parts = ['head_board', 'leg_board', 'centre_board', 'top_board', 'table_leg']
4. newsvendor: ratio = 0.9474 | order = 97.2
   safety stock: 197.4 | rop: 437.4
5. plan_pnl: revenue = 2319900.0 | margin = 2006954.6 (86.51%)
6. simulate: objective = 622909.09 | shocks = {'demand_multiplier': 1.2, 'capacity_multiplier': 0.9}
7. forecast_ladder: selected = naive | mae = {'naive': 33.333, ...}
8. jev (pass): ACT
   jev (fail): ESCALATE_REASONING | blockers: ['demand_conflict:week48']
ALL VERIFICATIONS PASSED
```

## Input contract (planning data)

```jsonc
{
  "dataset_id": "wooden_space_8week",
  "currency": "INR",
  "bucket": "week",
  "periods": ["W45", "W46", "W47", "W48", "W49", "W50", "W51", "W52"],
  "products": [
    { "id": "bed", "unit": "unit",
      "demand": [150, 250, 100, 350, 150, 100, 50, 50],
      "selling_price": 1000, "conversion_cost": 100,
      "holding_cost": 1, "backlog_cost": 100,
      "resources": {"cutting": 12, "panelling": 22, "assembly_packing": 33},
      "bom": {"head_board": 1, "leg_board": 1, "centre_board": 1} }
  ],
  "resources": [
    { "id": "cutting",   "capacity": [4800, 4800, 4800, 4800, 4800, 4800, 4800, 4800] },
    { "id": "panelling", "capacity": [7200, 7200, 7200, 7200, 7200, 7200, 7200, 7200] }
  ]
}
```

The 6th-working-day decision is just a capacity edit: raise the week's capacity from 2400 → 2880 minutes per machine and re-solve. The shadow prices tell you *which* week's extra minutes are worth the most.

## Design notes

- **Shadow prices are the product.** Anyone can run an LP; the useful output is knowing that one more panelling minute in W46 was worth ~18 units of avoided lateness, and that cutting minutes were worth zero.
- **Jev is deliberately conservative.** Gates are hard-coded defaults (0.85/0.85/0.2/2) but overridable per call. A blocker list is non-negotiable.
- **No hidden state.** Every tool is a pure function of its inputs.

## License

MIT
