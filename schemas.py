from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, Field


Verdict = Literal[
    "correct",
    "partially_correct",
    "likely_incorrect",
    "insufficient_evidence",
]


class EvidenceItem(BaseModel):
    file: str
    line_start: int = Field(ge=1)
    line_end: int = Field(ge=1)
    quote: str
    interpretation: str


class TimelineItem(BaseModel):
    time: str
    stage: str
    status: Literal["pass", "warning", "fail", "info", "unknown"]
    description: str
    evidence: list[EvidenceItem] = Field(default_factory=list)


class RootCauseCandidate(BaseModel):
    cause: str
    confidence: Literal["high", "medium", "low"]
    reasoning: str
    evidence: list[EvidenceItem] = Field(default_factory=list)


class AnalysisResult(BaseModel):
    reported_error: str
    verdict: Verdict
    confidence_score: int = Field(ge=0, le=100)
    executive_summary: str

    what_is_proven: list[str] = Field(default_factory=list)
    what_is_not_proven: list[str] = Field(default_factory=list)

    supporting_evidence: list[EvidenceItem] = Field(default_factory=list)
    contradicting_or_limiting_evidence: list[EvidenceItem] = Field(default_factory=list)

    test_timeline: list[TimelineItem] = Field(default_factory=list)
    failure_stage: str
    failure_explanation: str

    root_cause_candidates: list[RootCauseCandidate] = Field(default_factory=list)
    recommended_checks: list[str] = Field(default_factory=list)

    suggested_error_message: str
    missing_logs_or_data: list[str] = Field(default_factory=list)


RuntimeClassification = Literal["slow", "within_expected", "insufficient_data"]


class RuntimeEvidenceItem(BaseModel):
    file: str
    line_start: int = Field(ge=1)
    line_end: int = Field(ge=1)
    time: str = ""
    quote: str
    interpretation: str = ""


class RuntimeGapItem(BaseModel):
    duration_seconds: float = Field(ge=0)
    duration_text: str
    start_time: str
    end_time: str
    suspected_stage: str
    suspected_stage_label: str
    before_event: RuntimeEvidenceItem
    after_event: RuntimeEvidenceItem


class RuntimeStageItem(BaseModel):
    stage: str
    label: str
    duration_seconds: float = Field(ge=0)
    duration_text: str
    percent_of_process: float = Field(ge=0, le=100)


class RuntimeTimelineInterval(BaseModel):
    stage: str
    label: str
    start_time: str
    end_time: str
    duration_seconds: float = Field(ge=0)
    duration_text: str
    percent_of_process: float = Field(ge=0, le=100)
    is_gap: bool = False



class RuntimeRootCauseCandidate(BaseModel):
    cause: str
    confidence: Literal["high", "medium", "low"]
    reasoning: str
    evidence: list[RuntimeEvidenceItem] = Field(default_factory=list)


class RuntimeRetryTimeoutItem(BaseModel):
    occurrence_index: int = Field(ge=1)
    event_type: Literal["retry", "timeout", "suspected_timeout", "repeated_action"]
    detection: Literal["explicit", "inferred"]
    time: str
    stage: str
    stage_label: str
    operation: str
    what_was_happening: str
    initiator: str
    target_module: str
    execution_mode: str
    test_item: str = ""
    attempt_number: int | None = Field(default=None, ge=1)
    declared_timeout_seconds: float | None = Field(default=None, ge=0)
    declared_timeout_text: str = ""
    observed_wait_seconds: float | None = Field(default=None, ge=0)
    observed_wait_text: str = ""
    likely_trigger: str
    impact: str
    confidence: Literal["high", "medium", "low"]
    current_event: RuntimeEvidenceItem
    context_before: list[RuntimeEvidenceItem] = Field(default_factory=list)
    context_after: list[RuntimeEvidenceItem] = Field(default_factory=list)


class RuntimeRetryTimeoutGroup(BaseModel):
    group_index: int = Field(ge=1)
    event_type: str
    detection: Literal["explicit", "inferred", "mixed"]
    stage: str
    stage_label: str
    operation: str
    initiator: str
    target_module: str
    execution_mode: str
    test_item: str = ""
    count: int = Field(ge=1)
    first_time: str
    last_time: str
    total_observed_wait_seconds: float = Field(ge=0)
    total_observed_wait_text: str
    explanation: str
    occurrence_indexes: list[int] = Field(default_factory=list)


class RuntimeProcessItem(BaseModel):
    process_index: int = Field(ge=1)
    classification: Literal["slow", "within_expected"]
    is_slow: bool
    complete: bool
    boundary_source: str
    result_status: str
    start_time: str
    end_time: str
    total_duration_seconds: float = Field(ge=0)
    total_duration_text: str
    threshold_seconds: float = Field(ge=0)
    threshold_text: str
    over_threshold_seconds: float = Field(ge=0)
    over_threshold_text: str
    event_count: int = Field(ge=0)
    retry_count: int = Field(ge=0)
    explicit_retry_count: int = Field(ge=0)
    inferred_repeated_action_count: int = Field(ge=0)
    timeout_count: int = Field(ge=0)
    explicit_timeout_count: int = Field(ge=0)
    suspected_timeout_count: int = Field(ge=0)
    error_marker_count: int = Field(ge=0)
    stage_breakdown: list[RuntimeStageItem] = Field(default_factory=list)
    longest_gaps: list[RuntimeGapItem] = Field(default_factory=list)
    timeline_intervals: list[RuntimeTimelineInterval] = Field(default_factory=list)
    explicit_elapsed: list[dict] = Field(default_factory=list)
    retry_timeout_events: list[RuntimeRetryTimeoutItem] = Field(default_factory=list)
    retry_timeout_groups: list[RuntimeRetryTimeoutGroup] = Field(default_factory=list)
    retry_by_module: dict[str, int] = Field(default_factory=dict)
    timeout_by_module: dict[str, int] = Field(default_factory=dict)
    start_evidence: RuntimeEvidenceItem
    end_evidence: RuntimeEvidenceItem




class RuntimePlainContributor(BaseModel):
    rank: int = Field(ge=1)
    title: str
    category: str
    test_item: str = ""
    explanation: str
    evidence_level: Literal["confirmed", "inferred", "mixed"]
    count: int = Field(ge=1)
    observed_wait_seconds: float = Field(ge=0)
    observed_wait_text: str
    impact_summary: str

class RuntimeAIInsight(BaseModel):
    slow_reason_summary: str
    plain_language_conclusion: str = ""
    diagnosis_sequence: list[str] = Field(default_factory=list)
    main_contributors: list[RuntimePlainContributor] = Field(default_factory=list)
    not_main_contributors: list[str] = Field(default_factory=list)
    priority_checks: list[str] = Field(default_factory=list)
    certainty_explanation: str = ""
    root_cause_candidates: list[RuntimeRootCauseCandidate] = Field(default_factory=list)
    recommended_checks: list[str] = Field(default_factory=list)
    missing_logs_or_data: list[str] = Field(default_factory=list)


class RuntimeAnalysisResult(BaseModel):
    process_label: str = ""
    timing_scope: str = "transaction"
    batch_start_time: str = ""
    batch_end_time: str = ""
    batch_duration_seconds: float | None = Field(default=None, ge=0)
    batch_duration_text: str = "-"
    boundary_explanation: str = ""
    classification: RuntimeClassification
    is_slow: bool
    confidence_score: int = Field(ge=0, le=100)
    threshold_minutes: float = Field(gt=0)
    threshold_seconds: float = Field(gt=0)
    total_duration_seconds: float | None = Field(default=None, ge=0)
    total_duration_text: str
    over_threshold_seconds: float = Field(ge=0)
    over_threshold_text: str
    executive_summary: str
    slow_reason_summary: str
    plain_language_conclusion: str = ""
    diagnosis_sequence: list[str] = Field(default_factory=list)
    main_contributors: list[RuntimePlainContributor] = Field(default_factory=list)
    not_main_contributors: list[str] = Field(default_factory=list)
    priority_checks: list[str] = Field(default_factory=list)
    certainty_explanation: str = ""
    primary_process_index: int | None = None
    processes: list[RuntimeProcessItem] = Field(default_factory=list)
    stage_breakdown: list[RuntimeStageItem] = Field(default_factory=list)
    longest_gaps: list[RuntimeGapItem] = Field(default_factory=list)
    timeline_intervals: list[RuntimeTimelineInterval] = Field(default_factory=list)
    retry_timeout_events: list[RuntimeRetryTimeoutItem] = Field(default_factory=list)
    retry_timeout_groups: list[RuntimeRetryTimeoutGroup] = Field(default_factory=list)
    retry_by_module: dict[str, int] = Field(default_factory=dict)
    timeout_by_module: dict[str, int] = Field(default_factory=dict)
    root_cause_candidates: list[RuntimeRootCauseCandidate] = Field(default_factory=list)
    recommended_checks: list[str] = Field(default_factory=list)
    missing_logs_or_data: list[str] = Field(default_factory=list)
    deterministic_notes: list[str] = Field(default_factory=list)
    ai_used: bool = False
    ai_summary: str = ""


class CopilotMessage(BaseModel):
    role: str
    content: str


class CopilotRequest(BaseModel):
    provider: str
    api_key: str | None = None
    base_url: str | None = None
    model: str | None = None
    reported_error: str = ""
    verdict: str = ""
    executive_summary: str = ""
    evidence_excerpt: str = ""
    messages: list[CopilotMessage]

