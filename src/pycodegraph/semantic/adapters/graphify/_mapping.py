"""Static mapping tables for graphify-out → TOCS contract types.

Decision 1 (issue #100): the 45 graphify-out node types collapse to the 15
contract :class:`EntityKind` values. Each row below carries a short comment
explaining the mapping rationale. Types without an obvious contract home
(``Change``, ``Configuration``, ``Platform``, ``Version``, …) default to
``project_concept`` — the contract layer doesn't model them as first-class
entities, but they're still meaningful as documentation terms.
"""

from __future__ import annotations

from ...types import EntityKind, RelationKind

#: Maps every graphify-out ``node.type`` to a contract ``EntityKind``.
#:
#: Rationale per row:
#: - ``documentation``/``documentation_page``/``documentation_section``/
#:   ``documentation_index``/``Document``/``content`` — these are .rst
#:   documents (whole files, sections, or pages). The contract's closest
#:   kind is ``document_section``.
#: - ``concept``/``Concept``/``Term``/``ansible_concept``/``topic``/
#:   ``Change``/``Configuration``/``Platform``/``Version``/``release``/
#:   ``core_release``/``ExternalResource``/``ansible_test_category``/...
#:   graphify-out labels these as "concepts" in the loose sense; the
#:   contract's ``project_concept`` is the catch-all for documentation
#:   terms that are not first-class code entities.
#: - ``Module``/``module``/``filter``/``filter_group``/``tool``/...
#:   graphify-out uses these for plugins but does not expose the source
#:   symbol structure (no qualified_name, no line span). The contract's
#:   ``module`` kind fits — they're labelled modules in Ansible.
#: - ``file``/``directory``/``repository``/``community``/``topic`` —
#:   descriptive; collapse to ``project_concept``.
#: - ``test``/``ansible_test`` — graphify-out treats these as concept
#:   nodes (ansible-test sanity categories). Map to ``project_concept``.
#:
#: Types NOT in this map are treated as ``project_concept`` by the fallback
#: in :func:`entity_kind_for_node_type`.
NODE_TYPE_TO_ENTITY_KIND: dict[str, EntityKind] = {
    # --- documentation nodes → document_section ---
    "documentation": EntityKind.DOCUMENT_SECTION,
    "documentation_page": EntityKind.DOCUMENT_SECTION,
    "documentation_section": EntityKind.DOCUMENT_SECTION,
    "documentation_index": EntityKind.DOCUMENT_SECTION,
    "Document": EntityKind.DOCUMENT_SECTION,
    "content": EntityKind.DOCUMENT_SECTION,
    # --- module-like → module ---
    "Module": EntityKind.MODULE,
    "module": EntityKind.MODULE,
    "filter": EntityKind.MODULE,
    "filter_group": EntityKind.MODULE,
    "tool": EntityKind.MODULE,
    "ansible_lookup": EntityKind.MODULE,
    "connection": EntityKind.MODULE,
    "strategy_plugin": EntityKind.MODULE,
    # --- all other concept-ish → project_concept ---
    "concept": EntityKind.PROJECT_CONCEPT,
    "Concept": EntityKind.PROJECT_CONCEPT,
    "Term": EntityKind.PROJECT_CONCEPT,
    "ansible_concept": EntityKind.PROJECT_CONCEPT,
    "ansible_keyword": EntityKind.PROJECT_CONCEPT,
    "ansible_variable": EntityKind.PROJECT_CONCEPT,
    "ansible_directive": EntityKind.PROJECT_CONCEPT,
    "ansible_test": EntityKind.PROJECT_CONCEPT,
    "ansible_test_category": EntityKind.PROJECT_CONCEPT,
    "config-option": EntityKind.PROJECT_CONCEPT,
    "env_var": EntityKind.PROJECT_CONCEPT,
    "fact": EntityKind.PROJECT_CONCEPT,
    "keyword": EntityKind.PROJECT_CONCEPT,
    "label": EntityKind.PROJECT_CONCEPT,
    "platform": EntityKind.PROJECT_CONCEPT,
    "Platform": EntityKind.PROJECT_CONCEPT,
    "release": EntityKind.PROJECT_CONCEPT,
    "core_release": EntityKind.PROJECT_CONCEPT,
    "Version": EntityKind.PROJECT_CONCEPT,
    "Change": EntityKind.PROJECT_CONCEPT,
    "Configuration": EntityKind.PROJECT_CONCEPT,
    "ExternalResource": EntityKind.PROJECT_CONCEPT,
    "topic": EntityKind.PROJECT_CONCEPT,
    "Topic": EntityKind.PROJECT_CONCEPT,
    "variable": EntityKind.PROJECT_CONCEPT,
    "test": EntityKind.PROJECT_CONCEPT,
    "file": EntityKind.PROJECT_CONCEPT,
    "directory": EntityKind.PROJECT_CONCEPT,
    "repository": EntityKind.PROJECT_CONCEPT,
    "community": EntityKind.PROJECT_CONCEPT,
}


def entity_kind_for_node_type(node_type: str | None) -> EntityKind:
    """Map a graphify-out node.type to a contract EntityKind.

    Returns ``project_concept`` as the fallback for unknown types
    (decision 1, issue #100): the contract layer has no first-class
    representation for graphify-out's descriptive types (Change,
    Configuration, Platform, Version, …), but they remain meaningful
    documentation terms.
    """
    if node_type is None:
        return EntityKind.PROJECT_CONCEPT
    return NODE_TYPE_TO_ENTITY_KIND.get(node_type, EntityKind.PROJECT_CONCEPT)


# =============================================================================
# Link relation → contract RelationKind
# =============================================================================

#: graphify-out link relations that produce a :class:`RelationKind.DOCUMENTS_CONCEPT`.
#:
#: Decision 2 (issue #100): only ``documents``/``describes``/``defines``/
#: ``explains`` can be reliably mapped to ``documents_concept``. All other
#: graphify-out link relations are too coarse to fit any of the 7 fine-grained
#: ``documents_*`` kinds without producing semantically wrong relations
#: (COMMON-012: a semantic claim without a matching typed relation is
#: contextual only). They are dropped — no relation is emitted.
DOCUMENTS_CONCEPT_RELATIONS: frozenset[str] = frozenset(
    {"documents", "describes", "defines", "explains"}
)


def link_relation_to_contract(link_rel: str | None) -> str | None:
    """Return the contract relation kind value for a graphify-out link relation.

    Returns ``None`` when the link relation has no contract mapping — the
    adapter drops such links (no relation emitted). This is intentional
    (decision 2): the unknown relations catalogued in graphify-out are
    contextual only and shouldn't claim a typed contract slot.
    """
    if link_rel is None:
        return None
    if link_rel in DOCUMENTS_CONCEPT_RELATIONS:
        return RelationKind.DOCUMENTS_CONCEPT.value
    return None
