"""S&OP Solver MCP server: full analytical method set + case workflow + Jev gate.

Two entry styles, same engines:
  DIRECT   — `solve` runs any of the 24 methods standalone (no case required).
  WORKFLOW — case_open -> source/record -> analysis_submit -> decision_route ->
             deliverable_finalize: the evidence-linked, review-gated case pipeline.

Transport: stdio (default) or http (--transport http, plain, for local tooling).
"""
from __future__ import annotations

import os
import threading
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from .service import Service
from .models import CaseInput, Record, AnalysisSpec, RouteInput, FinalInput

INSTRUCTIONS = Path(__file__).with_name('host_instructions.md').read_text()

SOP_CYCLE = """# Monthly S&OP cycle — case {case_id}

Run this cycle with the S&OP Solver MCP as the workbench. The host reasons; the MCP computes and audits.

## Step 1 — Demand review
- `analysis_submit` method `forecast_ladder` per product family (or `holt` / `holt_winters` / `arima` for seasonal series).
- `forecast_track` against the last period's actuals; report bias and tracking signal.
- Record judgments via `forecast_adjust` — every overlay is logged, never silent.
- Save the consensus numbers as `evidence` records (label DERIVED_RESULT).

## Step 2 — Supply review
- `rough_cut` the proposed plan against shared resources; flag overloaded weeks.
- Resolve overloads with `plan_multi_period` (economic/chase/level, optional Saturday overtime capacity).
- `mrp_run` for multi-level BOMs with lot sizing; check projected-on-hand dips below zero.

## Step 3 — Pre-S&OP reconciliation
- `plan_pnl` produces the one set of numbers (P&L, cash, DSO/DIO/CCC, fill rates).
- `simulate` with templates demand_high, demand_low, supplier_failure, capacity_shock, price_cut; report best/worst EBITDA and risk exposure.
- Open `unknown` records for anything the numbers cannot explain; `assumption` records for policy inputs.

## Step 4 — Executive S&OP
- Open the decision as a `brief` with options; assess each `alternative` against the finance view.
- Use `decision_route` with `NEED_MORE_ANALYSIS` among the criteria as the executive gate: if selected, the decision is not ready.
- `deliverable_finalize` only after the challenge review passes. Cases flagged `finance_required` must carry a valid `plan_pnl` result.

## Step 5 — Decision log
- Every `decision_route` call persists a `jev_decision` record (question, criteria, selected option, gates, cost) — the S&OP decision log is automatic. Review it with `case_inspect` and page the audit with `audit_events`.

Offline / no-API-key environments: use `jev_evaluate` (deterministic gate) in place of `decision_route`; record the verdict as a `review` record.
"""

CONSULTING_REPORT = """# Consulting report skeleton — case {case_id}

Fill this 10-section skeleton from case records. The MCP never writes the report; it supplies the numbers, evidence links and audit trail.

1. **Executive summary** — recommendation, quantified impact (use `plan_pnl` KPI), decision boundary conditions.
2. **Problem statement** — decision brief, decision maker, options.
3. **Approach** — methods used (list `result` records with methods), data sources, assumptions accepted for analysis.
4. **Demand analysis** — `forecast_ladder` results, chosen model, WMAPE, adjustments and their log.
5. **Supply analysis** — capacity screen (`rough_cut`), plan (`plan_multi_period`), MRP, bottlenecks and shadow prices where reported.
6. **Financial analysis** — P&L rows, DSO/DIO/CCC, minimum closing cash, one set of numbers from `plan_pnl`.
7. **Risk and scenarios** — `simulate` templates, best/worst EBITDA, risk exposure; open unknowns.
8. **Options and recommendation** — alternatives with feasibility, Jev decision log (`jev_decision` records), reasons for the choice.
9. **Implementation** — steps, owners, decision boundaries, conditions.
10. **Appendices** — evidence references, audit events, limitations.

Rules: every number links to a record id; no figure appears in the report that is not in a case record; derived numbers carry their formula provenance.
"""

mcp = FastMCP('S&OP Solver', instructions=INSTRUCTIONS, stateless_http=True, json_response=True,
              max_request_body_size=22_000_000)

_service: Service | None = None
_lock = threading.Lock()


def service() -> Service:
    global _service
    with _lock:
        if _service is None:
            _service = Service(os.getenv('CASE_DB', './data/cases.sqlite3'))
    return _service


READ = {'readOnlyHint': True, 'destructiveHint': False, 'openWorldHint': False}
WRITE = {'readOnlyHint': False, 'destructiveHint': False, 'openWorldHint': False}


# ---------------------------------------------------------------- direct entry
@mcp.tool(annotations=READ)
def capabilities_inspect() -> dict:
    """Start here: every installed analytical method, integration limits and host responsibilities."""
    return service().capabilities()


@mcp.tool(annotations=READ)
def method_schema(method: str) -> dict:
    """Get exact JSON input schema for an analytical method before submitting a job."""
    from .method_schemas import SCHEMAS
    if method not in SCHEMAS:
        raise ValueError('unknown method')
    return SCHEMAS[method]


@mcp.tool(annotations=READ)
def solve(method: str, inputs: dict, timeout_seconds: int = 30) -> dict:
    """Run any analytical method directly, without opening a case. Pure function of its inputs."""
    from . import engines
    from .method_schemas import validate
    inputs = validate(method, inputs)
    return engines.run(method, inputs, min(max(timeout_seconds, 1), 90))


@mcp.tool(annotations=READ)
def jev_evaluate(criteria: dict, gates: dict | None = None) -> dict:
    """Offline Jev decision gate (no API call, no cost): ACT or ESCALATE_REASONING.

    criteria: {confidence, probability, margin, evidence_count, blockers}. A
    blocker (unresolved material fact) overrides every other gate — ship
    CONDITIONAL with blockers stated, never guess."""
    from .jev import jev_evaluate as gate
    return gate(criteria, gates)


# ---------------------------------------------------------------- case workflow
@mcp.tool(annotations=WRITE)
def case_open(data: CaseInput, request_id: str) -> dict:
    """Open an idempotent persistent case. Jev budget defaults to zero; set explicit allowed call count."""
    return service().open(data.model_dump(), request_id)


@mcp.tool(annotations=READ)
def case_list(limit: int = 20) -> dict:
    """List up to 100 single-owner cases for resuming prior investigations."""
    if not 1 <= limit <= 100:
        raise ValueError('limit 1-100')
    s = service().store
    with s.lock:
        return {'cases': [dict(r) for r in s.db.execute(
            'SELECT id,rev,state,created,updated FROM cases ORDER BY updated DESC LIMIT ?', (limit,))]}


@mcp.tool(annotations=READ)
def case_inspect(case_id: str) -> dict:
    """Read current revision, records, jobs, blockers and continuation instructions."""
    return service().inspect(case_id)


@mcp.tool(annotations=WRITE)
def source_register(case_id: str, expected_revision: int, request_id: str, name: str,
                    text: str | None = None, content_b64: str | None = None,
                    origin: str = 'CASE') -> dict:
    """Transfer source text or base64 PDF/XLSX/DOCX/CSV. Host-only attachments are not automatically visible."""
    return service().register_source(case_id, expected_revision, request_id, name, content_b64, text, origin)


@mcp.tool(annotations=READ)
def source_inspect(case_id: str, source_id: str, start: int = 0, limit: int = 5) -> dict:
    """Read page/sheet segments and their coverage status; paginate until all required material is reviewed."""
    return service().source_inspect(case_id, source_id, start, limit)


@mcp.tool(annotations=WRITE)
def source_review(case_id: str, expected_revision: int, request_id: str, source_id: str, locator: str,
                  status: str, note: str, replacement_text: str | None = None) -> dict:
    """Record host page review or corrected extraction. Changing a source invalidates dependent analyses."""
    return service().review_source(case_id, expected_revision, request_id, source_id, locator, status, note, replacement_text)


@mcp.tool(annotations=WRITE)
def record_upsert(case_id: str, expected_revision: int, request_id: str, record: Record,
                  depends_on: list[str] | None = None, record_id: str | None = None) -> dict:
    """Create/update typed evidence, brief, assumption, unknown, hypothesis, task, assessment, alternative, claim, review or external_result. Use existing ID when revising."""
    return service().record(case_id, expected_revision, request_id, record.model_dump(), depends_on, record_id)


@mcp.tool(annotations=READ)
def record_inspect(case_id: str, record_id: str, version: int | None = None) -> dict:
    """Read a record, dependency list and stale status; optionally inspect an immutable historical version."""
    return service().store.record(case_id, record_id, version)


@mcp.tool(annotations=READ)
def evidence_search(case_id: str, query: str, limit: int = 10) -> dict:
    """Lexical source search with locators. Does not replace full exhibit/page coverage review."""
    return service().search(case_id, query, limit)


@mcp.tool(annotations=WRITE)
def analysis_submit(case_id: str, expected_revision: int, request_id: str, spec: AnalysisSpec) -> dict:
    """Queue a numerical job against versioned task/input records. Unsupported capabilities fail explicitly."""
    return service().submit(case_id, expected_revision, request_id, spec.model_dump())


@mcp.tool(annotations=READ)
def job_inspect(case_id: str, job_id: str) -> dict:
    """Poll a job and inspect outputs/errors. SUCCEEDED means execution, not completed case reasoning."""
    return service().store.job(case_id, job_id)


@mcp.tool(annotations=WRITE)
def job_cancel(case_id: str, expected_revision: int, request_id: str, job_id: str) -> dict:
    """Request cooperative cancellation; an active solver/API may finish before observing cancellation."""
    return service().cancel(case_id, expected_revision, request_id, job_id)


@mcp.tool(annotations={'readOnlyHint': False, 'destructiveHint': False, 'openWorldHint': True})
def decision_route(case_id: str, expected_revision: int, request_id: str, request: RouteInput) -> dict:
    """Spend one allowed Jev call to advise a bounded next step using selected evidence; never bypass completion checks.

    Requires OPENROUTER_API_KEY (typesafe/jev-1.13 Decisions API). Without a key the
    call degrades to ESCALATE_REASONING — use jev_evaluate for offline gating."""
    return service().submit(case_id, expected_revision, request_id, request.model_dump(), kind='jev')


@mcp.tool(annotations=READ)
def case_check_completion(case_id: str) -> dict:
    """Return structural/evidence workflow blockers and conditions; not a proof of real-world correctness."""
    return service().completion(case_id)


@mcp.tool(annotations=WRITE)
def deliverable_finalize(case_id: str, expected_revision: int, request_id: str, deliverable: FinalInput) -> dict:
    """Persist host-written recommendation only after required evidence/review checks pass."""
    return service().finalize(case_id, expected_revision, request_id, deliverable.model_dump())


@mcp.tool(annotations=READ)
def artifact_read(case_id: str, deliverable_id: str) -> dict:
    """Retrieve final Markdown and supporting reference IDs."""
    r = service().store.record(case_id, deliverable_id)
    if r['kind'] != 'deliverable':
        raise ValueError('not a deliverable')
    return {'stale': r['stale'], **r['data']}


@mcp.tool(annotations=READ)
def audit_events(case_id: str, after_sequence: int = 0, limit: int = 50) -> dict:
    """Page through mutation/job audit events."""
    if not 1 <= limit <= 100:
        raise ValueError('limit 1-100')
    rows = service().store.events(case_id, after_sequence, limit)
    return {'events': rows, 'next_after': rows[-1]['seq'] if rows else None}


@mcp.tool(annotations=READ)
def export_bundle(case_id: str) -> dict:
    """Return a base64 ZIP of the case snapshot, current records, source extracts and final Markdown."""
    import base64
    from .exports import bundle
    return {'filename': case_id + '.zip', 'content_b64': base64.b64encode(bundle(service().store, case_id)).decode()}


# ---------------------------------------------------------------- prompts
@mcp.prompt()
def solve_case(objective: str) -> str:
    """Host reasoning workflow for a new complex case."""
    return INSTRUCTIONS + '\n\nUser objective: ' + objective


@mcp.prompt()
def sop_cycle(case_id: str) -> str:
    """Monthly S&OP cycle workflow template for the host (demand -> supply -> pre-S&OP -> executive)."""
    return SOP_CYCLE.format(case_id=case_id)


@mcp.prompt()
def consulting_report(case_id: str) -> str:
    """Consulting-grade 10-section report skeleton the host fills from case records; never generated by the MCP itself."""
    return CONSULTING_REPORT.format(case_id=case_id)


@mcp.resource('sop-solver://workflow')
def workflow() -> str:
    return INSTRUCTIONS


def main():
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('--transport', choices=['stdio', 'http'], default='stdio')
    a = p.parse_args()
    service()
    try:
        if a.transport == 'stdio':
            mcp.run(transport='stdio')
        else:
            import uvicorn
            uvicorn.run(mcp.streamable_http_app(), host=os.getenv('MCP_BIND', '127.0.0.1'),
                        port=int(os.getenv('MCP_PORT', '8000')), access_log=False)
    finally:
        service().close()


if __name__ == '__main__':
    main()
