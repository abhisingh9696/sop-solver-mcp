"""Standalone S&OP MCP server — exposes the engines as tools. No LLM involved."""
from __future__ import annotations

import json
from dataclasses import asdict

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from . import engines
from .engines import JevCriteria

app = Server("sop-solver")


def _tool(name: str, desc: str, schema: dict) -> Tool:
    return Tool(name=name, description=desc, inputSchema=schema)


TOOLS = [
    _tool("plan_multi_period", "Multi-period produce/carry/backlog LP with shared resources",
          {"type": "object", "properties": {"data": {"type": "object"}, "mode": {"type": "string",
           "enum": ["economic", "service"]}}, "required": ["data"]}),
    _tool("rough_cut", "Screen a proposed production plan against shared-resource capacity",
          {"type": "object", "properties": {"data": {"type": "object"},
           "proposed_production": {"type": "object"}}, "required": ["data", "proposed_production"]}),
    _tool("mrp_run", "BOM explosion into part-level net requirements and planned orders",
          {"type": "object", "properties": {"data": {"type": "object"}, "lot_size": {"type": "string",
           "enum": ["lot_for_lot", "fixed"]}, "fixed_qty": {"type": "number"}}, "required": ["data"]}),
    _tool("inventory_policy", "Newsvendor sizing or safety stock / reorder point",
          {"type": "object", "properties": {"policy": {"type": "string", "enum": ["newsvendor",
           "reorder_point"]}, "service_level": {"type": "number"}, "demand_std": {"type": "number"},
           "lead_time": {"type": "integer"}, "unit_cost": {"type": "number"},
           "selling_price": {"type": "number"}, "salvage": {"type": "number"}}}),
    _tool("plan_pnl", "Revenue, conversion/holding/backlog cost and margin for a plan",
          {"type": "object", "properties": {"data": {"type": "object"}, "plan": {"type": "object"}},
           "required": ["data", "plan"]}),
    _tool("simulate", "Template shock scenarios with re-optimization",
          {"type": "object", "properties": {"data": {"type": "object"}, "shocks": {"type": "object"}},
           "required": ["data", "shocks"]}),
    _tool("forecast_ladder", "Naive/SMA/SES/Holt selected by rolling-origin MAE",
          {"type": "object", "properties": {"history": {"type": "array", "items": {"type": "number"}},
           "horizon": {"type": "integer"}}, "required": ["history"]}),
    _tool("jev_evaluate", "The Jev decision gate: pass = ACT, fail = ESCALATE_REASONING",
          {"type": "object", "properties": {"criteria": {"type": "object"}}, "required": ["criteria"]}),
]


@app.list_tools()
async def _list_tools() -> list[Tool]:
    return TOOLS


@app.call_tool()
async def _call(name: str, args: dict) -> list[TextContent]:
    if name == "plan_multi_period":
        out = engines.plan_multi_period(args["data"], args.get("mode", "economic"))
    elif name == "rough_creat":
        out = engines.rough_cut(args["data"], args["proposed_production"])
    elif name == "rough_cut":
        out = engines.rough_cut(args["data"], args["proposed_production"])
    elif name == "mrp_run":
        out = engines.mrp_run(args["data"], args.get("lot_size", "lot_for_lot"),
                              args.get("fixed_qty", 0.0))
    elif name == "inventory_policy":
        out = engines.inventory_policy(**{k: v for k, v in args.items() if k != "policy"},
                                       **{"policy": args["policy"]})
    elif name == "plan_pnl":
        out = engines.plan_pnl(args["data"], args["plan"])
    elif name == "simulate":
        out = engines.simulate(args["data"], args["shocks"])
    elif name == "forecast_ladder":
        out = engines.forecast_ladder(args["history"], args.get("horizon", 4))
    elif name == "jev_evaluate":
        out = engines.jev_evaluate(JevCriteria(**args["criteria"]))
    else:
        out = {"status": "ERROR", "message": f"unknown tool {name}"}
    return [TextContent(type="text", text=json.dumps(out, default=str))]


def main() -> None:
    with stdio_server() as (read, write):
        app.run(read, write, app.create_initialization_options())


if __name__ == "__main__":
    main()
