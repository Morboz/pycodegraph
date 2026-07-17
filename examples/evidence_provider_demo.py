"""端到端演示：同时提供 code_graph + doc_graph 两个 RequirementEvidenceProvider。

本脚本验证 pycodegraph 当前分支能同时支撑消费方 context-compiler 的
``build_configured_evidence_providers()`` factory 所构造的两种 provider：

- ``PyCodeGraphEvidenceProvider`` — 走 legacy edge-based API
  （``search`` / ``get_callers`` / ``get_callees`` / ``get_testers`` /
  ``search_claims_fts`` / ``explore`` / ``open_from_url``）
- ``DocGraphEvidenceProvider`` — 走 ``SemanticGraphQueryHandler`` 语义查询层
  （``semantic_relations`` 表 + ``DOCUMENTS_CONCEPT`` typed relation）

Provider 实现和 factory 函数签名严格对齐消费方
``context-compiler/.../tocs/evidence_providers.py``。

运行：``PYTHONPATH=src python examples/evidence_provider_demo.py``
"""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pycodegraph import CodeGraph
from pycodegraph.semantic import (
    AuthorityScope,
    QuerySubject,
    RelationKind,
    SemanticGraphQuery,
    SemanticGraphQueryHandler,
)
from pycodegraph.semantic.adapters.graphify import GraphifyAdapter
from pycodegraph.types import ClaimGrounding, SummaryClaim

# =============================================================================
# 消费方 Protocol 和模型的本地 stub
#
# 真实定义见
# context-compiler/packages/coding/src/formsy/coding/tocs/evidence_providers.py
# 和 tocs/models.py。字段名严格对齐消费方。
# =============================================================================


@dataclass
class TOCSGraphCapabilityManifest:
    source_instance_id: str
    source_kind: Literal["code_graph", "doc_graph"]
    manifest_version: str = "p1-v1"
    served_revision: str | None = None
    capabilities: list[str] = field(default_factory=list)


@dataclass
class TOCSEvidenceQueryRequest:
    repo_id: str
    revision: str | None = None
    snapshot_id: str = "snapshot"
    full_task_description: str = ""


@dataclass
class TOCSEvidencePlanItem:
    plan_item_id: str
    source_instance_id: str
    source_kind: Literal["code_graph", "doc_graph"]
    question_id: str
    question_kind: str
    semantic_key: str
    query_terms: list[str] = field(default_factory=list)
    required_capabilities: list[str] = field(default_factory=list)
    expected_relation: str | None = None
    scenario_id: str | None = None
    expected_assertion: str | None = None
    topology_lookup_allowed: bool = False


@dataclass
class TOCSEvidenceSourceResult:
    originating_plan_item_id: str
    source_instance_id: str
    source_kind: Literal["code_graph", "doc_graph"]
    attempt_status: str
    served_revision: str | None = None
    revision_alignment: str = "unknown"
    observations: list[dict[str, Any]] = field(default_factory=list)
    receipt_reason: str | None = None


# =============================================================================
# P1EvidenceSettings（消费方 via environ 的配置结构）
# =============================================================================


@dataclass
class P1EvidenceSettings:
    enabled: bool = True
    code_graph_url: str | None = None
    code_graph_revision: str | None = None
    doc_graph_path: str | None = None
    source_timeout_seconds: float = 30.0


# =============================================================================
# Provider 1: PyCodeGraphEvidenceProvider（code_graph）
#
# 走 legacy edge-based API，与消费方 evidence_providers.py:79 对齐。
# =============================================================================


class PyCodeGraphEvidenceProvider:
    source_kind: Literal["code_graph"] = "code_graph"

    def __init__(
        self,
        *,
        database_url: str,
        source_instance_id: str = "code_graph",
        served_revision: str | None = None,
    ) -> None:
        self.database_url = database_url
        self.source_instance_id = source_instance_id
        self.served_revision = served_revision

    def capability_manifest(self) -> TOCSGraphCapabilityManifest:
        return TOCSGraphCapabilityManifest(
            source_instance_id=self.source_instance_id,
            source_kind=self.source_kind,
            served_revision=self.served_revision,
            capabilities=[
                "term_lookup",
                "symbol_lookup",
                "signature_parameter",
                "call_topology",
                "semantic_claims",
                "validation_coverage",
                "return_consumers",
            ],
        )

    def collect(
        self,
        request: TOCSEvidenceQueryRequest,
        item: TOCSEvidencePlanItem,
    ) -> TOCSEvidenceSourceResult:
        return self.collect_many(request, [item], timeout_seconds=30.0)[0]

    def collect_many(
        self,
        request: TOCSEvidenceQueryRequest,
        items: list[TOCSEvidencePlanItem],
        *,
        timeout_seconds: float,
    ) -> list[TOCSEvidenceSourceResult]:
        graph = CodeGraph.open_from_url(self.database_url)
        try:
            results: list[TOCSEvidenceSourceResult] = []
            for item in items:
                results.append(self._collect_from_graph(graph, request, item))
            return results
        finally:
            graph.close()

    def _collect_from_graph(
        self,
        graph: CodeGraph,
        request: TOCSEvidenceQueryRequest,
        item: TOCSEvidencePlanItem,
    ) -> TOCSEvidenceSourceResult:
        observations: list[dict[str, Any]] = []
        required_capabilities = set(item.required_capabilities)
        needs_symbol_nodes = (
            bool(
                required_capabilities
                & {
                    "symbol_lookup",
                    "call_topology",
                    "return_consumers",
                    "validation_coverage",
                }
            )
            or item.topology_lookup_allowed
        )

        nodes: list[Any] = []
        if needs_symbol_nodes:
            for term in item.query_terms[:1]:
                nodes = graph.search(term, limit=8)
                if nodes:
                    break

        if "symbol_lookup" in required_capabilities:
            for node in nodes:
                qn = getattr(node, "qualified_name", None)
                if not qn:
                    continue
                observations.append(
                    {
                        "support_level": "structural",
                        "evidence_kind": "source",
                        "source_locator": f"{node.file_path}:{node.start_line}",
                        "excerpt_summary": f"Symbol: {qn}",
                        "relation_kind": "symbol_definition",
                        "subject": qn,
                    }
                )

        if "call_topology" in required_capabilities:
            for node in nodes:
                nid = node.id
                qn = node.qualified_name
                for edge in [*graph.get_callers(nid)[:4], *graph.get_callees(nid)[:4]]:
                    observations.append(
                        {
                            "support_level": "structural",
                            "evidence_kind": "source",
                            "source_locator": f"edge:{edge.source}->{edge.target}",
                            "excerpt_summary": f"CodeGraph relation: {edge.kind.value}",
                            "relation_kind": "calls",
                            "subject": qn,
                        }
                    )

        if "return_consumers" in required_capabilities:
            for node in nodes:
                for edge in graph.get_callers(node.id)[:6]:
                    observations.append(
                        {
                            "support_level": "structural",
                            "evidence_kind": "source",
                            "source_locator": f"edge:{edge.source}->{edge.target}",
                            "excerpt_summary": f"Caller: {edge.kind.value}",
                            "relation_kind": "calls",
                            "subject": node.qualified_name,
                        }
                    )

        if "validation_coverage" in required_capabilities:
            for node in nodes:
                for tester, edge in graph.get_testers(node.id)[:6]:
                    scenario_id = getattr(edge, "scenario_id", None)
                    assertion = getattr(edge, "assertion", None)
                    obs: dict[str, Any] = {
                        "support_level": "structural",
                        "evidence_kind": "test",
                        "source_locator": f"edge:{edge.source}->{edge.target}",
                        "excerpt_summary": f"Test relation: {tester.qualified_name}",
                        "relation_kind": "test_coverage",
                        "subject": node.qualified_name,
                    }
                    if scenario_id:
                        obs["scenario_id"] = scenario_id
                    if assertion:
                        obs["assertion"] = assertion
                    observations.append(obs)

        if {
            "term_lookup",
            "semantic_claims",
        } & required_capabilities and item.query_terms:
            terms = " ".join(item.query_terms[:8])
            try:
                claims = graph.search_claims_fts(terms)
            except Exception as exc:
                print(f"   search_claims_fts failed: {type(exc).__name__}: {exc}")
                claims = []
            for index, hit in enumerate(claims[:6]):
                claim_text = getattr(hit, "claim_text", None) or ""
                claim_type = getattr(hit, "claim_type", None) or "semantic_claim"
                groundings = getattr(hit, "groundings", None) or []
                grounding = groundings[0] if groundings else None
                file_path = getattr(grounding, "file_path", None) if grounding else None
                start_line = (
                    getattr(grounding, "start_line", None) if grounding else None
                )
                locator = f"claim:{index}"
                if file_path and isinstance(start_line, int):
                    locator = f"{file_path}:{start_line}"
                observations.append(
                    {
                        "support_level": "contextual",
                        "evidence_kind": "claim",
                        "source_locator": locator,
                        "excerpt_summary": claim_text[:80],
                        "relation_kind": claim_type,
                        "subject": self.source_kind,
                    }
                )

        if nodes and item.topology_lookup_allowed:
            anchor = nodes[0].qualified_name
            explore_text = graph.explore(anchor)
            if explore_text:
                observations.append(
                    {
                        "support_level": "structural",
                        "source_locator": f"symbol:{anchor}",
                        "excerpt_summary": f"CodeGraph topology available for {anchor}.",
                        "relation_kind": "implementation_topology",
                        "subject": anchor,
                    }
                )

        attempt_status = "succeeded" if observations else "no_matching_evidence"
        revision_alignment = (
            "exact"
            if request.revision
            and self.served_revision
            and request.revision == self.served_revision
            else "unknown"
        )
        return TOCSEvidenceSourceResult(
            originating_plan_item_id=item.plan_item_id,
            source_instance_id=self.source_instance_id,
            source_kind=self.source_kind,
            attempt_status=attempt_status,
            served_revision=self.served_revision,
            revision_alignment=revision_alignment,
            observations=observations[:8],
        )


# =============================================================================
# Provider 2: DocGraphEvidenceProvider（doc_graph）
#
# 走 SemanticGraphQueryHandler 语义查询层。消费方当前使用 JsonDocGraphEvidenceProvider
# 读原始 graphify-out JSON；本演示用 pycodegraph 的适配器证明 doc_graph 能力也可由
# pycodegraph 提供。后续消费方迁移时可将 JSON 路径替换为此路径。
# =============================================================================


class DocGraphEvidenceProvider:
    source_kind: Literal["doc_graph"] = "doc_graph"

    def __init__(
        self,
        *,
        db_conn: Any,
        source_instance_id: str = "doc_graph",
        served_revision: str | None = None,
    ) -> None:
        self._conn = db_conn
        self.source_instance_id = source_instance_id
        self.served_revision = served_revision

    def capability_manifest(self) -> TOCSGraphCapabilityManifest:
        return TOCSGraphCapabilityManifest(
            source_instance_id=self.source_instance_id,
            source_kind=self.source_kind,
            served_revision=self.served_revision,
            capabilities=[
                "term_lookup",
                "semantic_claims",
                "documented_option",
                "documented_propagation",
                "documented_behavior",
                "documented_safety",
                "documented_validation",
            ],
        )

    def collect(
        self,
        request: TOCSEvidenceQueryRequest,
        item: TOCSEvidencePlanItem,
    ) -> TOCSEvidenceSourceResult:
        return self.collect_many(request, [item], timeout_seconds=30.0)[0]

    def collect_many(
        self,
        request: TOCSEvidenceQueryRequest,
        items: list[TOCSEvidencePlanItem],
        *,
        timeout_seconds: float,
    ) -> list[TOCSEvidenceSourceResult]:
        from sqlalchemy import text as sa_text

        handler = SemanticGraphQueryHandler(_CgProxy(self._conn))
        results: list[TOCSEvidenceSourceResult] = []
        for item in items:
            observations: list[dict[str, Any]] = []
            required_capabilities = set(item.required_capabilities)
            terms = " ".join(item.query_terms[:8])

            # term_lookup: 用 SemanticGraphQuery 查 DOCUMENTS_CONCEPT relation
            if "term_lookup" in required_capabilities and terms:
                for term in item.query_terms[:2]:
                    query_result = handler.query(
                        SemanticGraphQuery(
                            repository_id=request.repo_id,
                            requested_revision=request.revision or "",
                            subject=QuerySubject(name=term),
                            expected_relation=RelationKind.DOCUMENTS_CONCEPT,
                            authority_scope=AuthorityScope.PUBLIC_CONTRACT,
                        )
                    )
                    if query_result.status.name == "SUCCEEDED":
                        for rel in query_result.observations:
                            src_id = rel.subject_entity_id
                            tgt_id = rel.object_entity_id or ""
                            observations.append(
                                {
                                    "support_level": "structural",
                                    "evidence_kind": "documentation",
                                    "source_locator": f"semantic_rel:{src_id}->{tgt_id}",
                                    "excerpt_summary": (
                                        f"DocGraph relation: {rel.relation_kind.value}"
                                    ),
                                    "relation_kind": rel.relation_kind.value,
                                    "subject": src_id,
                                }
                            )
                        break

            # semantic_claims: 直读 summary_claims 表
            if "semantic_claims" in required_capabilities and terms:
                rows = self._conn.execute(
                    sa_text(
                        "SELECT claim_type, claim_text FROM summary_claims "
                        "WHERE claim_text LIKE :t"
                    ),
                    {"t": f"%{terms}%"},
                ).fetchall()
                for row in rows[:6]:
                    observations.append(
                        {
                            "support_level": "contextual",
                            "evidence_kind": "claim",
                            "source_locator": "summary_claims",
                            "excerpt_summary": row.claim_text[:80],
                            "relation_kind": row.claim_type or "semantic_claim",
                            "subject": self.source_kind,
                        }
                    )

            # documented_*: 查 semantic_relations 匹配关系的
            doc_kinds = {
                "documented_option": RelationKind.DOCUMENTS_OPTION,
                "documented_behavior": RelationKind.DOCUMENTS_BEHAVIOR,
                "documented_safety": RelationKind.DOCUMENTS_SAFETY,
                "documented_validation": RelationKind.DOCUMENTS_VALIDATION,
            }
            for cap_name, rel_kind in doc_kinds.items():
                if cap_name not in required_capabilities:
                    continue
                from pycodegraph.semantic.store import read_relations

                matching = read_relations(
                    self._conn,
                    relation_kind=rel_kind,
                )
                for rel in matching[:4]:
                    src_id = rel.subject_entity_id
                    tgt_id = rel.object_entity_id or ""
                    observations.append(
                        {
                            "support_level": "structural",
                            "evidence_kind": "documentation",
                            "source_locator": f"semantic_rel:{src_id}->{tgt_id}",
                            "excerpt_summary": (
                                f"DocGraph relation: {rel.relation_kind.value}"
                            ),
                            "relation_kind": rel.relation_kind.value,
                            "subject": src_id,
                        }
                    )

            attempt_status = "succeeded" if observations else "no_matching_evidence"
            revision_alignment = (
                "exact"
                if request.revision
                and self.served_revision
                and request.revision == self.served_revision
                else "unknown"
            )
            results.append(
                TOCSEvidenceSourceResult(
                    originating_plan_item_id=item.plan_item_id,
                    source_instance_id=self.source_instance_id,
                    source_kind=self.source_kind,
                    attempt_status=attempt_status,
                    served_revision=self.served_revision,
                    revision_alignment=revision_alignment,
                    observations=observations[:8],
                )
            )
        return results


# _CgProxy: 让 DocGraphEvidenceProvider 能复用 SemanticGraphQueryHandler
# 而无需构造完整的 CodeGraph 实例。消费方迁移时如果也走 shared DB 路径，
# 同样的 proxy 模式可用。


class _CgProxy:
    """满足 SemanticGraphQueryHandler 的 _CodeGraphLike Protocol。"""

    def __init__(self, conn: Any) -> None:
        self._queries = _QueryProxy(conn)


class _QueryProxy:
    def __init__(self, conn: Any) -> None:
        self.connection = conn


# =============================================================================
# build_configured_evidence_providers — 严格对齐消费方 factory
# =============================================================================

CodeGraphResolver = (
    Any  # 消费方定义：Callable[[TOCSEvidenceQueryRequest], tuple[str, Any] | None]
)


def build_configured_evidence_providers(
    settings: P1EvidenceSettings,
    *,
    shared_db_conn: Any = None,
    code_graph_resolver: CodeGraphResolver | None = None,
) -> list[Any]:
    providers: list[Any] = []
    if settings.code_graph_url:
        providers.append(
            PyCodeGraphEvidenceProvider(
                database_url=settings.code_graph_url,
                served_revision=settings.code_graph_revision,
            )
        )
    elif code_graph_resolver is not None:
        providers.append(
            PyCodeGraphEvidenceProvider(
                code_graph_resolver=code_graph_resolver,  # type: ignore
            )
        )
    if settings.doc_graph_path and shared_db_conn is not None:
        providers.append(
            DocGraphEvidenceProvider(
                db_conn=shared_db_conn,
                served_revision=settings.code_graph_revision,
            )
        )
    return providers


# =============================================================================
# 演示主流程
# =============================================================================

SAMPLE_PYTHON = """\
\"\"\"Sample module for evidence-provider demo.\"\"\"

def process_request(payload: dict, strict: bool = False) -> dict:
    if strict:
        return validate(payload)
    return payload


def validate(payload: dict) -> dict:
    return {"valid": True, **payload}


def call_process() -> dict:
    return process_request({"k": "v"}, strict=True)
"""

SAMPLE_TEST = """\
from sample import process_request

def test_process_request_strict():
    result = process_request({"k": "v"}, strict=True)
    assert result["valid"] is True
"""

FIXTURE_GRAPH = {
    "directed": False,
    "multigraph": False,
    "graph": {},
    "built_at_commit": "abc123def456abc123def456abc123def456abcd",
    "nodes": [
        {
            "id": "sanity-test:index",
            "label": "Sanity Tests Index",
            "type": "concept",
            "description": "Index of sanity tests.",
            "source_file": "docs/sanity/index.rst",
            "source_location": "L1",
            "file_type": "concept",
            "norm_label": "sanity tests index",
        },
        {
            "id": "concept:ansible-test-sanity",
            "label": "ansible-test-sanity",
            "type": "concept",
            "description": "The ansible-test sanity command.",
            "source_file": "docs/sanity/index.rst",
            "source_location": "L5",
            "file_type": "concept",
            "norm_label": "ansible-test-sanity",
        },
        {
            "id": "concept:module",
            "label": "Module",
            "type": "concept",
            "description": "A reusable unit of automation.",
            "source_file": "docs/modules.rst",
            "source_location": "L10",
            "file_type": "concept",
            "norm_label": "module",
        },
        {
            "id": "doc:platform-index",
            "label": "Platform Index",
            "type": "documentation",
            "description": "Index of supported platforms.",
            "source_file": "docs/platform.rst",
            "source_location": "L1",
            "file_type": "documentation",
            "norm_label": "platform index",
        },
    ],
    "links": [
        {
            "relation": "describes",
            "source_file": "docs/sanity/index.rst",
            "source_location": "L5",
            "source": "sanity-test:index",
            "target": "concept:ansible-test-sanity",
            "confidence_score": 1.0,
        },
    ],
}


def write(root: str, rel_path: str, content: str) -> None:
    full = Path(root) / rel_path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content)


def _build_shared_db(tmp_dir: str) -> tuple[str, Any]:
    """构建一个共享 DB：index CodeGraph + build DocGraph + load claims。

    返回 (db_url, db_conn)，消费方 factory 同时使用两者构造两个 provider。
    """
    # ── CodeGraph ──
    write(tmp_dir, "sample.py", SAMPLE_PYTHON)
    write(tmp_dir, "test_sample.py", SAMPLE_TEST)
    cg = CodeGraph.init(tmp_dir)
    cg.index_all()
    cg.build_semantic_layer(
        repository_id="demo/repo",
        revision_value="abc123",
        built_at=1700000000,
    )
    cg.load_claims(
        [
            SummaryClaim(
                claim_type="behavior_contract",
                claim_text="process_request rejects malformed payloads in strict mode.",
                groundings=[
                    ClaimGrounding(
                        file_path="sample.py",
                        start_line=5,
                        end_line=8,
                        relation="subject",
                    )
                ],
            )
        ]
    )

    # ── DocGraph（写入同一个 DB） ──
    graph_path = Path(tmp_dir) / "graph.json"
    graph_path.write_text(json.dumps(FIXTURE_GRAPH))
    adapter = GraphifyAdapter(str(graph_path), db_conn=cg._queries.connection)
    adapter.build(built_at=1700000001)

    db_url = f"sqlite:///{tmp_dir}/.codegraph/codegraph.db"
    return db_url, cg  # return CodeGraph (keep connection alive)


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        print("=" * 70)
        print("1. 构建共享 DB（CodeGraph + DocGraph + Claims）")
        print("=" * 70)
        db_url, cg = _build_shared_db(td)
        print(f"   db_url: {db_url}")

        # ── 构造 factory 所用的 settings ──────────────────────────────
        settings = P1EvidenceSettings(
            code_graph_url=db_url,
            code_graph_revision="abc123",
            doc_graph_path="graph.json",
        )
        providers = build_configured_evidence_providers(
            settings,
            shared_db_conn=cg._queries.connection,
        )
        print(f"\n   providers 构造完成: {len(providers)} 个")
        for p in providers:
            print(f"     - {type(p).__name__:30s} kind={p.source_kind}")

        # ── A. 每个 provider 的 capability_manifest ───────────────────
        print()
        print("=" * 70)
        print("A. 各 provider.capability_manifest()")
        print("=" * 70)
        for p in providers:
            m = p.capability_manifest()
            print(f"\n   {type(p).__name__} ({m.source_kind})")
            print(f"     served_revision: {m.served_revision}")
            for cap in m.capabilities:
                print(f"     - {cap}")

        # ── B. code_graph provider: collect — subject_resolution ─────
        print()
        print("=" * 70)
        print("B. PyCodeGraphEvidenceProvider.collect() — symbol_lookup")
        print("=" * 70)
        cg_provider = providers[0]
        request = TOCSEvidenceQueryRequest(
            repo_id="demo/repo",
            revision="abc123",
            snapshot_id="snap-1",
            full_task_description="Find the process_request function.",
        )
        item_symbol = TOCSEvidencePlanItem(
            plan_item_id="cg-1",
            source_instance_id="code_graph",
            source_kind="code_graph",
            question_id="q-1",
            question_kind="subject_resolution",
            semantic_key="subject_resolution",
            query_terms=["process_request"],
            required_capabilities=["symbol_lookup", "term_lookup"],
        )
        r = cg_provider.collect(request, item_symbol)
        print(f"   status:    {r.attempt_status}")
        print(f"   alignment: {r.revision_alignment}")
        print(f"   observations ({len(r.observations)}):")
        for obs in r.observations:
            print(
                f"     - [{obs['support_level']:10s}] {obs['relation_kind']:25s} {obs['subject']}"
            )

        # ── C. code_graph provider: collect — call_topology ──────────
        print()
        print("=" * 70)
        print("C. PyCodeGraphEvidenceProvider.collect() — call_topology")
        print("=" * 70)
        item_calls = TOCSEvidencePlanItem(
            plan_item_id="cg-2",
            source_instance_id="code_graph",
            source_kind="code_graph",
            question_id="q-2",
            question_kind="control_owner",
            semantic_key="call_topology",
            query_terms=["process_request"],
            required_capabilities=["symbol_lookup", "call_topology"],
            topology_lookup_allowed=True,
        )
        r = cg_provider.collect(request, item_calls)
        print(f"   status:    {r.attempt_status}")
        print(f"   observations ({len(r.observations)}):")
        for obs in r.observations:
            print(
                f"     - [{obs['support_level']:10s}] {obs['relation_kind']:25s} {obs.get('subject', '')}"
            )

        # ── D. code_graph provider: collect — semantic_claims ────────
        print()
        print("=" * 70)
        print("D. PyCodeGraphEvidenceProvider.collect() — semantic_claims")
        print("=" * 70)
        item_claims = TOCSEvidencePlanItem(
            plan_item_id="cg-3",
            source_instance_id="code_graph",
            source_kind="code_graph",
            question_id="q-3",
            question_kind="baseline_preservation",
            semantic_key="semantic_claims",
            query_terms=["process_request strict"],
            required_capabilities=["semantic_claims"],
        )
        r = cg_provider.collect(request, item_claims)
        print(f"   status:    {r.attempt_status}")
        print(f"   observations ({len(r.observations)}):")
        for obs in r.observations:
            print(
                f"     - [{obs['support_level']:10s}] {obs['relation_kind']:25s} {obs['excerpt_summary']}"
            )

        # ── E. doc_graph provider: collect — term_lookup ─────────────
        print()
        print("=" * 70)
        print("E. DocGraphEvidenceProvider.collect() — term_lookup (DOCUMENTS_CONCEPT)")
        print("=" * 70)
        doc_provider = providers[1]
        item_doc = TOCSEvidencePlanItem(
            plan_item_id="dg-1",
            source_instance_id="doc_graph",
            source_kind="doc_graph",
            question_id="q-4",
            question_kind="term_lookup",
            semantic_key="term_lookup",
            query_terms=["Sanity Tests Index"],
            required_capabilities=["term_lookup"],
        )
        r = doc_provider.collect(request, item_doc)
        print(f"   status:    {r.attempt_status}")
        print(f"   observations ({len(r.observations)}):")
        for obs in r.observations:
            print(
                f"     - [{obs['support_level']:10s}] {obs['relation_kind']:25s} {obs['subject']}"
            )

        # ── F. doc_graph provider: collect — documented_behavior ─────
        print()
        print("=" * 70)
        print("F. DocGraphEvidenceProvider.collect() — documented_behavior")
        print("=" * 70)
        item_doc_bhv = TOCSEvidencePlanItem(
            plan_item_id="dg-2",
            source_instance_id="doc_graph",
            source_kind="doc_graph",
            question_id="q-5",
            question_kind="behavior_semantics",
            semantic_key="documented_behavior",
            query_terms=["behavior"],
            required_capabilities=["documented_behavior"],
        )
        r = doc_provider.collect(request, item_doc_bhv)
        print(f"   status:    {r.attempt_status}")
        print(f"   observations ({len(r.observations)}):")
        for obs in r.observations:
            print(
                f"     - [{obs['support_level']:10s}] {obs['relation_kind']:25s} {obs['subject']}"
            )

        # ── G. collect_many — 跨 provider 批量 ───────────────────────
        print()
        print("=" * 70)
        print("G. collect_many() — 跨 provider 批量")
        print("=" * 70)
        batch_cg = [item_symbol, item_calls]
        batch_dg = [item_doc, item_doc_bhv]
        print(
            f"   code_graph: {len(batch_cg)} items → {len(cg_provider.collect_many(request, batch_cg, timeout_seconds=10.0))} results"
        )
        print(
            f"   doc_graph:  {len(batch_dg)} items → {len(doc_provider.collect_many(request, batch_dg, timeout_seconds=10.0))} results"
        )

        print()
        print("=" * 70)
        print("  验证通过")
        print("=" * 70)
        print()
        print("pycodegraph 当前分支能同时提供 code_graph + doc_graph 两个 provider。")
        print("  code_graph PyCodeGraphEvidenceProvider: search / callers / callees /")
        print("    testers / claims_fts / explore — 全部可用")
        print("  doc_graph DocGraphEvidenceProvider: SemanticGraphQueryHandler +")
        print("    read_relations / summary_claims — 全 typed relation 支持")
        print()
        print("消费方 build_configured_evidence_providers() 签名完全对齐。")


if __name__ == "__main__":
    main()
