from __future__ import annotations

import time
from backend.ai_assistant.authorization import AuthorizationContext
from backend.ai_assistant.models import EntityReference, LineageResult
from backend.ai_assistant.security import MAX_LINEAGE_DEPTH, MAX_LINEAGE_DURATION_MS, MAX_LINEAGE_EDGES, MAX_LINEAGE_NODES
from backend.research.services import get_research_service
from backend.trading.persistence import TradingStore


class LineageService:
    def trace(self, reference: EntityReference, *, auth_context: AuthorizationContext | None = None) -> LineageResult:
        started = time.perf_counter()
        nodes: list[dict] = []
        edges: list[dict] = []
        missing: list[str] = []
        warnings: list[str] = []
        seen: set[str] = set()

        def limited() -> bool:
            duration_ms = (time.perf_counter() - started) * 1000
            return (
                len(nodes) >= MAX_LINEAGE_NODES
                or len(edges) >= MAX_LINEAGE_EDGES
                or duration_ms > MAX_LINEAGE_DURATION_MS
            )

        def add_node(node_id: str, node_type: str, version: str | None = None, timestamp: str | None = None) -> None:
            if limited():
                warnings.append("lineage_limit_reached")
                return
            if node_id in seen:
                warnings.append(f"cycle_or_duplicate:{node_id}")
                return
            seen.add(node_id)
            nodes.append({"id": node_id, "type": node_type, "version": version, "timestamp": timestamp})

        def add_edge(source: str, target: str, relation: str) -> None:
            if limited():
                warnings.append("lineage_limit_reached")
                return
            edges.append({"source": source, "target": target, "relation": relation})

        def can_read(row: dict | None) -> bool:
            return auth_context is None or auth_context.can_read_account_row(row)

        data = TradingStore().load()
        research = get_research_service().registry
        current = reference.entity_id
        add_node(current, reference.entity_type, reference.entity_version)

        if reference.entity_type == "candidate":
            cand = research.get("candidates", current)
            if not cand:
                return LineageResult(tuple(nodes), tuple(edges), ("candidate",), tuple(warnings))
            if auth_context and not auth_context.can_read_research(cand.get("owner")):
                return LineageResult(tuple(), tuple(), ("candidate",), ("scope_mismatch",))
            scorecard_id = cand.get("scorecard_id")
            if scorecard_id:
                add_node(scorecard_id, "scorecard", cand.get("strategy_version"), cand.get("created_at"))
                add_edge(scorecard_id, current, "promotes_candidate")
            else:
                missing.append("scorecard")
            deployment = next((row for row in data["deployments"].values() if row.get("candidate_id") == current), None)
            if deployment and can_read(deployment):
                did = deployment["deployment_id"]
                add_node(did, "deployment", str(deployment.get("version")), deployment.get("created_at"))
                add_edge(current, did, "deployed_as")
                current = did
            else:
                missing.append("deployment")

        if (reference.entity_type == "deployment" or current.startswith("deploy_")) and MAX_LINEAGE_DEPTH >= 1:
            deployment = data["deployments"].get(current)
            if deployment and can_read(deployment):
                for risk in data["risk_evaluations"].values():
                    if risk.get("deployment_id") == current and can_read(risk):
                        rid = risk["evaluation_id"]
                        add_node(rid, "risk_evaluation", risk.get("risk_policy_id"), risk.get("created_at"))
                        add_edge(current, rid, "risk_checked")
                for order in data["orders"].values():
                    if order.get("deployment_id") == current and can_read(order):
                        oid = order["order_id"]
                        add_node(oid, "paper_order", str(order.get("version")), order.get("created_at"))
                        add_edge(current, oid, "created_order")
                        self._order_children(oid, data, add_node, add_edge, missing, auth_context)
            else:
                missing.append("deployment")

        if reference.entity_type == "paper_order":
            order = data["orders"].get(current)
            if order and not can_read(order):
                return LineageResult(tuple(nodes[:1]), tuple(), ("paper_order",), ("scope_mismatch",))
            self._order_children(current, data, add_node, add_edge, missing, auth_context)

        if reference.entity_type == "risk_evaluation":
            risk = data["risk_evaluations"].get(current)
            if risk and can_read(risk):
                deployment_id = risk.get("deployment_id")
                if deployment_id:
                    add_node(deployment_id, "deployment")
                    add_edge(deployment_id, current, "risk_checked")
            else:
                missing.append("risk_evaluation")

        return LineageResult(tuple(nodes), tuple(edges), tuple(missing), tuple(warnings))

    def _order_children(self, order_id: str, data: dict, add_node, add_edge, missing: list[str], auth_context: AuthorizationContext | None) -> None:
        fills = [
            row
            for row in data["fills"].values()
            if row.get("order_id") == order_id and (auth_context is None or auth_context.can_read_account_row(row))
        ]
        if not fills:
            missing.append("fill")
        for fill in fills:
            fid = fill["fill_id"]
            add_node(fid, "paper_fill", str(fill.get("sequence")), fill.get("created_at"))
            add_edge(order_id, fid, "filled_by")
            add_node(fill.get("instrument_id", "position"), "position")
            add_edge(fid, fill.get("instrument_id", "position"), "updates_position")
        reconciliations = [
            row
            for row in data["reconciliations"]
            if any(fill.get("account_id") == row.get("account_id") for fill in fills)
            and (auth_context is None or auth_context.can_read_account_row(row))
        ]
        for recon in reconciliations[-1:]:
            rid = recon["reconciliation_id"]
            add_node(rid, "reconciliation", rid, recon.get("created_at"))
            add_edge(fills[-1]["instrument_id"] if fills else order_id, rid, "reconciled_by")


lineage_service = LineageService()
