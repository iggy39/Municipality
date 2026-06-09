from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RagDashboardQueryRequest(StrictModel):
    question: str = Field(min_length=1)
    muni: str | None = None
    top_k: int = Field(default=8, ge=1, le=50)
    semantic_node_id: int | None = Field(default=None, ge=1)
    semantic_label: str | None = None
    semantic_mode: str = "off"
    filters: dict[str, Any] = Field(default_factory=dict)
    debug_mode: bool = False


class RagDashboardInteraction(StrictModel):
    type: str = Field(min_length=1)
    id: str | None = None
    filters: dict[str, Any] | None = None


class RagDashboardInteractionRequest(StrictModel):
    state: dict[str, Any] = Field(default_factory=dict)
    interaction: RagDashboardInteraction


class EvidencePageSpan(StrictModel):
    start: int | None = None
    end: int | None = None


class RagDashboardEvidence(StrictModel):
    id: str
    source_type: str
    source_title: str
    source_url: str | None = None
    retrieval_artifact_id: str
    artifact_kind: str
    retrieval_set_id: str | None = None
    header_path: list[str] = Field(default_factory=list)
    page_span: EvidencePageSpan = Field(default_factory=EvidencePageSpan)
    start_offset: int | None = None
    end_offset: int | None = None
    bbox: list[int] | None = None
    text: str
    confidence_label: str
    extraction_warnings: list[str] = Field(default_factory=list)


class ResidentEvidenceLink(StrictModel):
    label_he: str
    icon: str
    evidence_ref: str


class TimeAnchor(StrictModel):
    kind: str
    start: str | None = None
    end: str | None = None
    precision: str | None = None
    label_he: str | None = None
    confidence_label: str | None = None
    evidence_refs: list[str] = Field(default_factory=list)


class CodeLabel(StrictModel):
    code: str
    label_he: str
    vocabulary: str | None = None
    raw_text: str | None = None
    confidence_label: str | None = None


class RagDashboardDecision(StrictModel):
    id: str
    title: str
    raw_decision_text: str
    summary: str
    primary_time: TimeAnchor
    time_anchors: list[TimeAnchor] = Field(default_factory=list)
    decision_kind: CodeLabel
    outcome_status: CodeLabel
    legal_effect: CodeLabel
    topic_ids: list[str] = Field(default_factory=list)
    entity_ids: list[str] = Field(default_factory=list)
    timeline_event_ids: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    resident_evidence_links: list[ResidentEvidenceLink] = Field(default_factory=list)
    normalized_by: str
    curation_status: str
    limitations: list[str] = Field(default_factory=list)


class TopicNodeContract(StrictModel):
    id: str
    label: str
    origin: str
    generated_label: str
    curated_label: str | None = None
    topic_type: CodeLabel
    category_ids: list[str] = Field(default_factory=list)
    primary_category_id: str | None = None
    parent_id: str | None = None
    child_ids: list[str] = Field(default_factory=list)
    sibling_ids: list[str] = Field(default_factory=list)
    path_ids: list[str] = Field(default_factory=list)
    depth: int = 0
    sort_order: int = 0
    mention_count: int = 0
    decision_count: int = 0
    recent_activity_at: str | None = None
    time_rollup: dict[str, Any] = Field(default_factory=dict)
    decision_rollups: dict[str, Any] = Field(default_factory=dict)
    merged_from_topic_ids: list[str] = Field(default_factory=list)
    split_from_topic_id: str | None = None
    hidden_from_public: bool = False
    confidence_label: str | None = None
    evidence_refs: list[str] = Field(default_factory=list)
    curation_status: str
    selected: bool = False


class RelatedTopic(StrictModel):
    id: str
    label: str
    target_topic_id: str
    relation_type: str
    relation_label_he: str
    count: int = 0
    selected: bool = False


class MapEntityType(StrictModel):
    code: str
    label_he: str
    vocabulary: str | None = None


class SchematicShape(StrictModel):
    kind: str
    coordinates: list[Any] = Field(default_factory=list)


class MapEntity(StrictModel):
    id: str
    label: str
    entity_type: MapEntityType
    spatial_representation: Literal["schematic"]
    schematic_shape: SchematicShape
    real_geometry: None = None
    geometry_provenance: None = None
    confidence_label: str
    uncertainty_reasons: list[str] = Field(default_factory=list)
    active_from: str | None = None
    active_to: str | None = None
    activity_score: int = 0
    is_recent_high_activity: bool = False
    topic_ids: list[str] = Field(default_factory=list)
    decision_ids: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    selected: bool = False


class MapProvenance(StrictModel):
    status: Literal["schematic_only"]
    label_he: str
    description_he: str
    source_evidence_refs: list[str] = Field(default_factory=list)


class MapLegendRow(StrictModel):
    id: str
    label: str


class DashboardMap(StrictModel):
    spatial_representation: Literal["schematic"]
    label: str
    real_geometry: None = None
    geometry_provenance: None = None
    provenance: MapProvenance
    entities: list[MapEntity] = Field(default_factory=list)
    legend: list[MapLegendRow] = Field(default_factory=list)
    real_gis_available: Literal[False]


class TimelineEvent(StrictModel):
    id: str
    date: str
    date_label: str
    title: str
    summary: str
    selected: bool = False
    decision_ids: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)


class DashboardTimeline(StrictModel):
    selected_event_id: str | None = None
    events: list[TimelineEvent] = Field(default_factory=list)


class MainCivicWorkspace(StrictModel):
    map: DashboardMap
    timeline: DashboardTimeline


class DashboardState(StrictModel):
    municipality_id: str | None = None
    current_question: str
    search_intent: str | None = None
    intent_resolution: dict[str, Any] = Field(default_factory=dict)
    selected_time_range: dict[str, Any] | None = None
    selected_category_id: str | None = None
    selected_topic_node_id: str | None = None
    selected_map_entity_id: str | None = None
    selected_timeline_event_id: str | None = None
    selected_evidence_id: str | None = None
    current_answer_id: str | None = None
    active_detail_drawer_mode: str
    confidence_filter: str | None = None
    source_type_filter: list[str] = Field(default_factory=list)
    filter_modal_open: bool = False
    active_filter_count: int = 0
    active_filter_summary: dict[str, Any] = Field(default_factory=dict)
    popular_searches_open: bool = False
    cached_answer_id: str | None = None
    map_mode: Literal["schematic"] = "schematic"
    generation_status: str
    login_state: str | None = None
    error: dict[str, Any] | None = None


class DiscoveryRow(StrictModel):
    id: str
    label: str
    count: int
    selected: bool = False


class FocusedTopicNode(StrictModel):
    id: str
    label: str
    count: int | None = None
    selected: bool = False


class FocusedTopicTreeContext(StrictModel):
    selected_topic_id: str | None = None
    root: dict[str, Any]
    parent: dict[str, Any] | None = None
    breadcrumbs: list[dict[str, Any]] = Field(default_factory=list)
    siblings: list[dict[str, Any]] = Field(default_factory=list)
    children: list[FocusedTopicNode] = Field(default_factory=list)
    nearby_topics: list[dict[str, Any]] = Field(default_factory=list)
    collapsed_categories: list[dict[str, Any]] = Field(default_factory=list)


class StartDiscoveryPanel(StrictModel):
    categories: list[DiscoveryRow] = Field(default_factory=list)
    hot_topics: list[DiscoveryRow] = Field(default_factory=list)
    focused_topic_tree_context: FocusedTopicTreeContext


class EndDetailDrawer(StrictModel):
    mode: str
    answer_id: str | None = None
    title: str
    question: str
    brief: str
    confidence_label: str
    decisions: list[RagDashboardDecision] = Field(default_factory=list)
    related_topics: list[RelatedTopic] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class DashboardContracts(StrictModel):
    topic_nodes: list[TopicNodeContract] = Field(default_factory=list)
    decisions: list[RagDashboardDecision] = Field(default_factory=list)
    evidence: list[RagDashboardEvidence] = Field(default_factory=list)
    map_entities: list[MapEntity] = Field(default_factory=list)
    related_topics: list[RelatedTopic] = Field(default_factory=list)


class RagDashboardPayload(StrictModel):
    ui_copy: dict[str, Any]
    state: DashboardState
    start_discovery_panel: StartDiscoveryPanel
    main_civic_workspace: MainCivicWorkspace
    end_detail_drawer: EndDetailDrawer
    contracts: DashboardContracts
    evidence: list[RagDashboardEvidence] = Field(default_factory=list)
    evidence_preview: RagDashboardEvidence | None = None
    interaction_result: dict[str, Any] | None = None
