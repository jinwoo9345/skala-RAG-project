"""API/모델 다운로드 없는 실행 흐름 검증용 가상 공급자. 실제 기술 사실이 아니다."""

from config import Settings
from schemas import (
    Conflicts,
    CounterResult,
    Evaluation,
    Extraction,
    ReportDraft,
    Synthesis,
)
from services import Services


class DemoRetriever:
    def search(self, query, *, perspective, technology=None, role=None, k=None, mode="hybrid"):
        technology = technology or "InfiniGen"
        return [
            {
                "chunk_id": f"demo-{technology}-p1",
                "technology": technology,
                "document_id": f"demo-{technology}",
                "source": f"DEMO 가상 문서 {technology}",
                "source_url": f"https://example.test/demo/{technology}",
                "page": 1,
                "role": role or "core",
                "content": f"{technology} DEMO fixture evidence for workflow testing only. "
                "GPU=DEMO model=DEMO context=DEMO batch=DEMO. This is not a research result.",
            }
        ]


class DemoWeb:
    def __init__(self):
        self.calls = []

    def search(self, query, **kwargs):
        self.calls.append(query)
        return [
            {
                "url": "https://example.test/demo/web",
                "title": "DEMO 가상 웹 자료",
                "content": "DEMO web evidence for workflow testing only. No real technology facts. "
                "This synthetic claim has a synthetic limitation.",
            }
        ]


class DemoQuestionRewriter:
    def batch(self, inputs):
        return [value["question"] + " rewritten" for value in inputs]


class DemoReportWriter:
    def invoke(self, value):
        payload = value["payload"]
        ids = list(payload["evidence"])
        paragraphs = (
            [
                {
                    "text": "DEMO: 실행 흐름 확인용 문단입니다. 실제 기술 분석이 아닙니다.",
                    "evidence_ids": ids[:1],
                }
            ]
            if ids
            else []
        )
        return ReportDraft(
            summary=paragraphs,
            sections=[{"section_id": key, "paragraphs": paragraphs} for key in payload["sections"]],
        )


class DemoLLM:
    def __init__(self, scenario="normal"):
        self.scenario = scenario
        self.technical_calls = {}

    def generate(self, task, instructions, payload, schema, *, judge=False):
        if schema is Extraction:
            technology = payload["technology"]
            if task == "extract:technical":
                self.technical_calls[technology] = self.technical_calls.get(technology, 0) + 1
                if self.scenario == "retry_exhausted" or (
                    self.scenario == "retry_success" and self.technical_calls[technology] == 1
                ):
                    return Extraction(facts=[])
            source = payload["chunks"][0]
            quote = source["content"].split(". ")[0] + "."
            return Extraction(
                facts=[
                    {
                        "chunk_id": source["chunk_id"],
                        "item": item,
                        "claim": f"DEMO: {technology}의 {item} 가상 근거",
                        "quote": quote,
                        "kind": "Fact",
                        "numeric": False,
                        "experimental_condition": "",
                        "condition_chunk_id": "",
                        "speaker": "",
                        "affiliation": "",
                    }
                    for item in payload["items"]
                ]
            )
        if schema is Evaluation:
            findings = []
            for technology in payload["technologies"]:
                own = [i for i, e in payload["evidence"].items() if e["technology"] == technology]
                for criterion in payload["criteria"]:
                    if not own or (self.scenario == "second_missing" and payload["perspective"] == "market"):
                        continue
                    findings.append(
                        {
                            "technology": technology,
                            "criterion": criterion,
                            "claim": f"DEMO: {technology} {criterion} 가상 평가",
                            "kind": "Inference",
                            "evidence_ids": own[:1],
                            "limitation": "실제 평가 아님",
                            "trl_level": 3 if payload["perspective"] == "trl" else None,
                        }
                    )
            return Evaluation(findings=findings, limitations=["테스트 전용 가상 자료"])
        if schema is CounterResult:
            source = payload["sources"][0]
            found = self.scenario == "counter_found"
            return CounterResult(
                found=found,
                source_id=source["chunk_id"] if found else "",
                counter_claim="DEMO 가상 제약 사항" if found else "",
                quote="This synthetic claim has a synthetic limitation." if found else "",
                kind="Fact",
                experimental_condition="",
            )
        if schema is Conflicts:
            return Conflicts(conflicts=[])
        if schema is Synthesis:
            ids = [f["evidence_ids"][0] for a in payload["analyses"].values() for f in a.get("findings", [])]
            text = (
                [
                    {
                        "text": "DEMO: 두 기술의 가상 평가를 통합했습니다. 실제 결론이 아닙니다.",
                        "evidence_ids": ids[:2],
                    }
                ]
                if ids
                else []
            )
            return Synthesis(summary=text, commonalities=text, differences=[], tradeoffs=[], conclusion=text)
        raise ValueError("지원하지 않는 demo schema")


def demo_services(scenario="normal", settings=None):
    return Services(
        DemoLLM(scenario),
        DemoRetriever(),
        DemoWeb(),
        settings or Settings(),
        mode="demo",
        question_rewriter=DemoQuestionRewriter(),
        report_writer=DemoReportWriter(),
    )
