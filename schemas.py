"""LLM 입출력 계약. 근거 ID와 인용문은 별도의 코드 검증을 거친다."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExtractedFact(StrictModel):
    chunk_id: str
    item: str
    claim: str
    quote: str
    kind: Literal["Fact", "Opinion"]
    numeric: bool
    experimental_condition: str
    condition_chunk_id: str
    speaker: str
    affiliation: str


class Extraction(StrictModel):
    facts: list[ExtractedFact]


class Finding(StrictModel):
    technology: str
    criterion: str
    claim: str
    kind: Literal["Fact", "Opinion", "Inference"]
    evidence_ids: list[str]
    limitation: str
    trl_level: int | None = Field(ge=1, le=9)


class Evaluation(StrictModel):
    findings: list[Finding]
    limitations: list[str]


class CounterResult(StrictModel):
    found: bool
    source_id: str
    counter_claim: str
    quote: str
    kind: Literal["Fact", "Opinion"]
    experimental_condition: str


class Conflict(StrictModel):
    category: str
    description: str
    evidence_ids: list[str]
    conditions: str
    implication: str


class Conflicts(StrictModel):
    conflicts: list[Conflict]


class CitedText(StrictModel):
    text: str
    evidence_ids: list[str]


class Synthesis(StrictModel):
    summary: list[CitedText]
    commonalities: list[CitedText]
    differences: list[CitedText]
    tradeoffs: list[CitedText]
    conclusion: list[CitedText]


class ReportSection(StrictModel):
    section_id: str
    paragraphs: list[CitedText]


class ReportDraft(StrictModel):
    summary: list[CitedText]
    sections: list[ReportSection]
