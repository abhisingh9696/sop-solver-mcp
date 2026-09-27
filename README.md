# sop-solver-mcp

Standalone **Sales & Operations Planning (S&OP)** MCP server.

- **24 analytical methods** — LP/MILP planning, MRP, inventory policies, forecasting
  (incl. ARIMA/SARIMA), finance, simulation, multi-echelon pooling, general math.
- **Full evidence-linked case workflow** — sources → brief → hypotheses → tasks →
  analysis jobs → assessments → alternatives → claims → review gates → deliverable.
- **The Jev decision gate** — API-backed routing (`decision_route`, via OpenRouter's
  `typesafe/jev-1.13` Decisions API) or offline (`jev_evaluate`, deterministic, free).

No LLM, no database server, no network required for the engines — every method is a
pure Python function (numpy/scipy/statsmodels). Extracted from a real 8-week
production-planning solve (the Wooden Space case) and its full analysis trail.

## Install

```bash
pip install git+https://github.com/abhisingh9696/sop-solver-mcp.git
```

Requires Python 3.10+. Installs numpy, scipy, statsmodels, pydantic, pypdf, openpyxl, mcp.

## Run as an MCP server

```bash
sop-solver                 # stdio (default)
sop-solver --transport http   # local HTTP on 127.0.0.1:8000
```

Claude Desktop / Hermes `mcpServers` config:

```json
{
  "mcp_servers": {
    "sop-solver": {
      "command": "sop-solver",
      "transport": "stdio",
      "env": { "CASE_DB": "D:/sop/cases.sqlite3" }
    }
  }
}
```

Optional: `OPENROUTER_API_KEY` enables the API-backed `decision_route`; without it the
call degrades to `ESCALATE_REASONING` and you use the free offline gate instead.

## The 24 analytical methods

| # | Method | What it does |
|---|--------|--------------|
| 1 | `descriptive` | count, mean, median, std, min/max, sum |
| 2 | `formula` | named parameters + ordered arithmetic expressions + checks (no Python exec) |
| 3 | `sensitivity` | explicit parameter scenarios over the same formula model |
| 4 | `cashflow` | NPV, discounted flows, peak funding exposure, discrete payback |
| 5 | `linear_program` | user-formulated continuous LP, residuals + duals (shadow prices, reduced costs) |
| 6 | `milp` | mixed-integer LP, solver gap + integrality violation report |
| 7 | `welch_ttest` | two-sample Welch comparison, effect estimate + interval |
| 8 | `ols` | least squares with intercept, rank check, fit diagnostics |
| 9 | `forecast` | naive / mean3 / seasonal-naive baselines with rolling-origin bands |
| 10 | `sop_plan` | single-echelon shared-resource + finite-material LP with full validation |
| 11 | `plan_multi_period` | produce/carry/backlog LP, overtime (Saturday) capacity, economic/chase/level shaping, **shadow prices** |
| 12 | `rough_cut` | load-vs-capacity screen, flags overloaded periods |
| 13 | `mrp_run` | BOM explosion, lot-for-lot / fixed / max-stock, planning time fence |
| 14 | `inventory_policy` | newsvendor, safety stock / reorder point, (s,S) policy replay |
| 15 | `plan_pnl` | one set of numbers: P&L, cash, DSO/DIO/CCC, fill rates, validated plan |
| 16 | `simulate` | template shocks (demand/supplier/capacity/price), best/worst EBITDA |
| 17 | `holt` | double exponential smoothing, fitted or supplied alpha/beta |
| 18 | `holt_winters` | seasonal smoothing, additive, fitted or supplied constants |
| 19 | `forecast_ladder` | baselines + SMA/WMA/EMA/SES/Holt/Holt-Winters, selected by rolling-origin MAE |
| 20 | `forecast_adjust` | judgmental per-period multiplier overlay — logged, never silent |
| 21 | `forecast_track` | bias, MAPE, RMSE, MASE, tracking signal vs actuals |
| 22 | `arima` | ARIMA/SARIMA (statsmodels), supplied orders, residual-based bands, AIC/BIC |
| 23 | `regression_forecast` | least-squares on host-supplied exogenous features (association, not causation) |
| 24 | `multi_echelon_inventory` | warehouse + stores safety-stock pooling, signed benefit |

Two entry styles:

```python
# DIRECT — no case needed
solve(method='plan_multi_period', inputs={'data': {...}})

# WORKFLOW — evidence-linked case (22 tools)
case_open -> source_register -> source_review -> record_upsert
          -> analysis_submit -> job_inspect -> decision_route
          -> case_check_completion -> deliverable_finalize -> artifact_read
```

## The Jev decision gate

Jev answers one question: **should the plan ship now, or is more evidence needed?**

```
gates:  confidence >= 0.85   AND   probability >= 0.85
        AND   margin >= 0.20  AND   evidence_count >= 2
        AND   no blockers
passed  -> ACT
failed  -> ESCALATE_REASONING
```

**A blocker beats confidence.** Even a 0.8-confident plan refuses to ship when a
material fact is unresolved — it ships CONDITIONAL, with every variant solved and the
open question stated. That is Jev's whole point: a confident answer must never paper
over an unresolved fact.

Two modes:

```python
# offline (free, deterministic)
jev_evaluate({'confidence': 0.9, 'probability': 0.9, 'margin': 0.3,
              'evidence_count': 2, 'blockers': []})            # -> ACT
jev_evaluate({..., 'blockers': ['demand_conflict:week48']})    # -> ESCALATE_REASONING

# API-backed routing (workflow decision_route; needs OPENROUTER_API_KEY)
# calls typesafe/jev-1.13 via the OpenRouter Decisions API; every call persists
# a jev_decision record — the decision log is automatic.
```

### How Jev drove the real Wooden Space solve

| Step | Gates | Verdict |
|------|-------|---------|
| Run the LP baseline | conf .90, prob .90, margin .30, evidence 4 | **ACT** — panelling pinned at 100% W45–W50 |
| Declare panelling the bottleneck | conf .92, prob .90, margin .35, evidence 4 | **ACT** — two independent models agreed |
| Pick the minimum Saturday set | conf .86, prob .85, margin .25, evidence 3 | **ACT** — greedy search found W45–49 |
| Ship before resolving the W48 demand conflict? | conf .80, **blocker: `demand_conflict:week48`** | **ESCALATE_REASONING** — both demand variants solved, shipped CONDITIONAL |

## Worked example — the Wooden Space case (ships with the repo)

`examples/wooden_space/` contains the full case data and two verification scripts.

```bash
python examples/wooden_space/verify_methods.py     # all 24 methods, smoke-tested
python examples/wooden_space/workflow_verify.py    # full case pipeline end-to-end
```

Real output (trimmed):

```
1. descriptive  OK ... 24. multi_echelon_inventory  OK
headline: panelling utilisation W45-50 = [1.0, 1.0, 1.0, 1.0, 1.0, 1.0] | objective 122045.5
ALL 24 METHODS VERIFIED

case_open: case_d45edb97000640fea390 revision 0
baseline job -> SUCCEEDED | backlog total 609.1
sat search: none 609.1 | W45-49 0.0
jev gate: ESCALATE_REASONING | blockers: ['demand_conflict:week48']
completion: CONDITIONAL
finalized: COMPLETED_CONDITIONAL
FULL WORKFLOW VERIFIED: open -> sources -> records -> analysis -> reviews -> Jev -> finalize
```

The example reproduces the real case's finding: **panelling is the binding
constraint** (100% utilisation weeks 45–50), the Saturday search lands on weeks
45–49, and the week-48 demand conflict (document 350 vs spreadsheet 150) is handled
exactly the way Jev mandates — both variants solved, result shipped conditional.

## The case workflow (evidence-linked, review-gated)

The workflow tools enforce a discipline that a bare engine call cannot:

- **Every claim links to evidence records**, and every evidence record must quote
  source text verbatim at a real locator.
- **Every source segment must be reviewed** (REVIEWED / EXCLUDED / NEEDS_OCR) before
  evidence can depend on it.
- **Analysis jobs are versioned** against their inputs; changing a source or record
  marks dependents stale, so no stale number can silently survive.
- **A passing review is required per required task** — execution success is not
  validation.
- **The decision challenge review** must cover the brief, every alternative and every
  material claim before `deliverable_finalize` will persist anything.
- **`case_check_completion`** returns BLOCKED / CONDITIONAL / PASSED with named
  blockers and conditions; the host resolves them or states them.

## Project layout

```
src/sop_solver/
├── server.py          # MCP tools: 22 (4 direct + 18 workflow) + 3 prompts + 1 resource
├── engines.py         # the 24 methods, dispatched by name
├── sop/               # S&OP engines: planner, optimizer, mrp, inventory, forecast,
│                      #   enterprise (arima/regression/multi-echelon), pnl, finance,
│                      #   critic (independent validation), schema (PlanningInput), simulation
├── jev.py             # decision_route client (OpenRouter) + offline jev_evaluate gate
├── models.py          # typed case records (pydantic)
├── method_schemas.py  # per-method input JSON schemas
├── service.py         # case workflow service (evidence -> task -> review -> deliverable)
├── store.py           # SQLite workspace: revisions, idempotency, stale propagation
├── sources.py         # PDF/XLSX/DOCX/CSV/TXT ingestion with page/sheet locators
├── exports.py         # case export bundle (ZIP)
└── sandbox.py         # opt-in Docker Python runner (disabled by default)
```

## Design notes

- **Shadow prices are the product.** Anyone can run an LP; the useful output is
  knowing one more panelling minute in W46 was worth ~18 units of avoided lateness
  and cutting minutes were worth zero.
- **No hidden reasoning API.** The MCP computes and audits; the host model reasons.
  Every number carries provenance, every derived figure carries its formula.
- **Deterministic and transparent.** Classical statistics only (no ML forecasting),
  declared assumptions on every output, warnings where boundaries apply.
- **Conservative by default.** Jev gates are hard thresholds; blockers are
  non-negotiable; `sop_plan` independently re-validates every hard constraint.

## License

MIT
