# PLAN: Supervisor 패턴 최소 구현 계획

- 기준: Notion "Multi-Agent Orchestration" 가이드라인
- 브랜치: `supervisor`
- 선택 패턴: **Supervisor**. Orchestrator-Workers, Agent-as-Tool, Swarm은 다루지 않는다.
- 원칙: 가이드라인이 요구하는 것만 구현한다(overengineering 금지).
- LangSmith: `.env`에 `LANGSMITH_TRACING`, `LANGSMITH_API_KEY`, `LANGSMITH_PROJECT=skala-RAG-supervisor` 설정 완료

## 이전 초안에서 제거·축소한 항목 (overengineering)
| 기존 계획 | 처리 | 이유 |
|---|---|---|
| `prompts/` 디렉토리로 프롬프트 이동 | 제거 | 샘플 디렉토리일 뿐 필수가 아니다. 평가는 "README 구성과 실제 구조 일치"만 본다. |
| `supervisor/` 패키지 | 단일 모듈 `supervisor.py`로 축소 | 조정 계층 분리(F-5)는 모듈 하나로 충분하다. |
| `perspectives` dict로 4개 분석 필드 통합 | 제거, 기존 `trl_analysis` 등 유지 | 가이드라인 요구가 아니며 report·synthesis 전반에 변경이 번진다. |
| 병합 Reducer(`merge_dict`) | 제거 | Supervisor는 한 번에 하나만 실행해 동시 쓰기가 없다. C-6은 근거 설명으로 충족한다. |
| 체크포인터 기반 재개 구현 | 제거 | C-5는 "재개에 필요한 최소 상태가 State에 있는가"만 묻는다. `node_status`, `last_error`, 재시도 횟수로 충족한다. |
| 출처 다양성·임계값 기반 충분성 점수 | 제거 | 기존 기준 커버리지 검사(`first/second_evidence_check`)를 그대로 충분성 판단에 쓴다. |
| 라우팅 10단계, 의존도 기반 선택 | 단순화 | "비었거나 부족한 것"을 찾는 규칙만 둔다. |
| `run.json` 라우팅·재작업 집계, PDF 10장 자동 검사 | 제거 | 트레이스와 로그로 확인하면 된다. 10장 제한은 제출 전에 수동으로 확인한다. |
| 품질 평가의 항목별 다중 규칙 | 항목당 규칙 1개 + LLM Judge 1회로 축소 | 3안 Hybrid의 최소형이다. |

## Supervisor 한정 검증
가이드라인의 Orchestrator-Workers 전용 항목이 계획에 들어가지 않았는지 대조했다.

| Orchestrator 전용 항목 | 계획 반영 여부 |
|---|---|
| 서브 태스크 목록을 구조화해 State에 저장 | 없음 |
| Dynamic Fan-out(`Send`), 고정 Fan-out 금지 | 없음. 기존 고정 `fan_out`/`fan_in`도 제거해 Supervisor 순차 라우팅으로 대체 |
| Workers 결과 누적 후 synthesizer 집계 | 없음. 기존 `synthesis` Agent는 Supervisor가 호출하는 하위 에이전트일 뿐 병합 노드가 아님 |
| 일부 worker 실패 시 계속/재시도/제외 fallback | 없음. 대신 Supervisor 필수 항목인 "근거 부족 시 재작업"(B-4)과 C-5 에러 상태로 처리 |
| C-6 "Orchestrator 동적 Fan-out 동시 쓰기" | Supervisor 기준으로만 답한다(순차 실행이라 동시 쓰기 없음) |
| 평가 기준 F-2의 "Orchestrator = dynamic fan-out" | 제외. "Supervisor = 라우팅/재작업 수"만 대상 |

Agent-as-Tool, Swarm도 다루지 않는다.
나머지 항목(State Schema 7개, 품질 평가, README, LangSmith, 보고서)은 패턴과 무관하게 공통 필수이므로 유지한다.

---

### 1. 범위
노션 가이드라인 중 **Supervisor 패턴 필수 항목과 공통 필수 항목만** 구현한다. 공통 필수 항목은 State Schema, 품질 평가, 산출물이다.

### 2. 가이드라인 요구사항
- **A. 패턴 특징**
  - A-1: 하위 에이전트에게 동적 라우팅
  - A-2: 증거 충분성 판단 후 보고서 작성
  - A-3: 관점별 재조사·추가 검색 루프
- **B. 필수 구현**
  - B-1: 하위 에이전트는 Supervisor와만 통신하고, 하위 에이전트 간 직접 통신은 금지
  - B-2: State(수집된 관점, 근거 충분도)에 따라 `add_conditional_edges`로 분기. 순서 하드코딩 금지
  - B-3: 근거 충분성 평가 후에 보고서 작성. 스텝 수 고정 금지
  - B-4: 근거 부족 시 해당 하위 에이전트에게 재작업 요청
- **C. State Schema 7항목:** 제어 vs 페이로드 분리, 관측성 위치, 지속성 비용, 상관, 재개/복구, 동시 처리, 종료 보장. 설계 근거를 README에 쓰고 코드에 구현한다. 샘플은 참고용이며, "결정 로그는 State 밖 관측성 계층에 두고 trace_id로 연결한다"는 원칙을 따른다.
- **D. 품질 평가**
  - D-1: 보고서 생성 후 품질 평가 노드
  - D-2: 미달 시 Loop
  - D-3: Groundedness, 중립성, 편향 통제, 관점 커버리지
  - D-4: 1안·2안·3안 중 택일
- **E. 산출물**
  - Branch 분리
  - README(샘플의 모든 섹션 포함. State Schema 7항목, Contributors에서 PM·PL 제외)
  - LangSmith 트레이스 캡처(`tracing-1.png`…)
  - 보고서는 기존 구성 그대로, 최대 10장. SUMMARY와 REFERENCE 필수
  - 제출: `Agent_{캠퍼스}_{X반}_{이름...}.zip`, Slack 스레드, DAY 2 퇴근 전
- **평가 기준(Supervisor 해당분):** 패턴 정합성 20, 동적 동작 실증(라우팅·재작업 수) 20, State 설계 20, 품질 평가 노드 15, 모듈 분리 5, 재현성(무한 루프 없이 보고서 생성) 10, 보고서 10

### 3. 현재 코드의 차이
- `graph.py`가 `technical → first_check → … → fan_out → 4개 Agent → fan_in → second_check → counter_evidence → conflict → synthesis → report → END`로 **노드끼리 직접 연결된 고정 순서**다. B-1과 B-2를 위반한다.
- 2차 근거가 부족해도 기록만 하고 진행한다(B-3 위반). 4개 관점 Agent에는 재작업이 없다(B-4, A-3 위반).
- 품질 평가 노드가 없다(D 위반).
- State에 `trace_id`, `step_count`, `node_status`, `last_error`가 없다(C).

### 4. 목표 구조
```
START → supervisor ─(add_conditional_edges)→ technical | trl | market | stakeholder | domain
                                              | verification | synthesis | report | quality_eval | END
각 하위 에이전트 → supervisor   (하위 에이전트 간 엣지 없음)
```
- **`supervisor.py` (신규, 조정 계층)**
  - 노드가 State를 읽어 다음 대상(`directive.target`)과 사유를 결정하고 `step_count`를 1 증가시킨다.
  - 조건부 엣지 함수는 `state["directive"]["target"]`을 반환한다.
- **판단 규칙(위에서부터 처음 맞는 것):**
  1. `step_count >= max_steps`이면 보고서가 없을 때 `report`, 있으면 `END`
  2. 기술 근거가 부족하고 재시도가 남았으면 `technical` 재작업
     - 판단: 기존 `nodes/evidence.py:first_evidence_check` 로직 재사용
     - 재시도 상한: 기존 `retry_count`/`max_retries`
  3. 아직 결과가 없는 관점이 있으면 그 관점 Agent로
  4. 관점 근거가 부족하고 재작업이 남았으면 그 Agent에 부족 기준만 재작업 요청
     - 판단: 기존 `second_evidence_check`의 기준 커버리지 로직 재사용
     - 상한: `rework_counts[agent] < 1`
     - 상한에 닿으면 `missing_evidence`에 기록하고 진행
  5. 반대 근거·Conflict가 없으면 `verification`
  6. 종합이 없으면 `synthesis`
  7. 보고서가 없으면 `report`. 1~6을 통과해야만 도달하므로 B-3이 보장된다.
  8. 품질 평가가 없으면 `quality_eval`
  9. 품질이 미달이고 `rework_counts["quality"] < 1`이면 재작업
     - 커버리지 실패: 해당 관점 Agent로
     - 그 외 실패: `report` 재작성. verdict 사유를 보고서 작성 입력에 전달
  10. 그 외에는 `END`
- **재작업 전달(B-4):** Supervisor가 `directive = {target, reason, missing_items}`를 쓴다. 하위 에이전트는 `missing_items`만 다시 조사하고 기존 결과에 합친다.
  - `technical`: 재작업이면 기존 `query_rewrite`를 내부에서 호출한 뒤 재검색한다. `query_rewrite` 별도 노드는 제거한다.
  - `trl`/`market`/`stakeholder`: `agents/common.py:web_sources`에 검색할 기준 목록 인자를 추가하고, 재작업 시 대체 검색어(별칭 쪽)를 우선 사용한다.
  - `domain`: 부족 기준만 RAG로 다시 검색한다.
- **`verification`:** 기존 `counter_evidence_node`와 `conflict_node`를 차례로 호출하는 노드 하나.
- **노드 래퍼(`graph.py` bind):** 예외를 잡아 `node_status[name]="failed"`와 `last_error`를 기록하고 Supervisor로 돌아간다. Supervisor는 상한 안이면 재시도하고, 아니면 한계로 기록하고 진행한다.

### 5. State Schema (`state.py`)
기존 페이로드 필드는 유지하고, 제어 필드만 추가한다.
- **페이로드(유지):** `technologies`, `domain`, `technical_evidence`, `trl_analysis`, `market_analysis`, `stakeholder_analysis`, `domain_analysis`, `counter_evidence`, `conflicts`, `synthesis`, `final_report`, `references`. 신규 `eval_result`(품질 verdict).
- **제어(추가·정리):** `trace_id`, `step_count`, `max_steps`, `directive`, `node_status`, `rework_counts`, `last_error`. 유지: `retry_count`, `max_retries`, `missing_evidence`, `search_queries`.

| 항목 | 설계 근거 (README에 그대로 기록) |
|---|---|
| 제어 vs 페이로드 | 위 두 그룹으로 나눈다. Supervisor는 제어 필드와 각 분석 결과의 존재·부족 여부만 읽는다. |
| 관측성 위치 | 결정과 사유는 State에 쌓지 않는다. `SUPERVISOR_DECISION \| step \| next \| reason` 로그와 LangSmith 트레이스로 남긴다. State에는 현재 `directive.reason`만 둔다. |
| 지속성 비용 | 원문 청크와 웹 본문은 State에 넣지 않는다(기존 유지). 결정 로그는 누적하지 않고, `directive`·`search_queries`·`missing_evidence`는 덮어쓴다. PDF와 로그는 `outputs/`에 저장한다. |
| 상관 | `trace_id = run_id`. 로그 접두어, LangSmith `metadata`, `outputs/` 폴더명에 같은 값을 쓴다. |
| 재개/복구 | `node_status`, `last_error`, `retry_count`, `rework_counts`로 어디까지 진행했고 무엇이 실패했는지 State에서 판단할 수 있다. |
| 동시 처리 | Supervisor가 한 번에 하나의 에이전트만 실행하므로 동시 쓰기가 없다. 그래서 Reducer를 두지 않는다. |
| 종료 보장 | `max_steps`(Supervisor 결정 상한), 에이전트별 재작업 상한, 품질 Loop 상한, 그리고 이 값으로 계산한 `recursion_limit`. 상한에 닿으면 보고서를 만들고 종료한다. |

### 6. 품질 평가 노드 (`nodes/quality_eval.py`, 3안 Hybrid 최소형으로 확정)
- **선정 이유:**
  - 근거 연결은 기존 코드(`report.py`의 `cited`, `valid_ids`)가 이미 대부분 검증한다. 그래서 규칙 부분은 짧게 끝난다.
  - 규칙 검사는 결정적이라 재평가 Loop가 실행마다 들쭉날쭉하지 않다.
  - 우열 판정, 편향 같은 내용 판단은 LLM Judge 1회 호출이 맡는다.
  - 2안(LLM만)보다 코드가 약 20줄 늘어나는 수준이다.
- **통과 조건:** 4개 항목 각각 규칙 통과 **그리고** LLM 판정 통과.

| 항목 | 규칙 검사 | LLM Judge (1회 호출, `judge=True`) |
|---|---|---|
| Groundedness | REFERENCE가 있고, 본문 인용 번호가 모두 REFERENCE에 있다. | 주장이 인용 근거로 뒷받침되는가 |
| 중립성 | 금지 표현("추천", "우월", "더 낫다" 등)이 없다. | 우열 판정이나 추천이 있는가 |
| 편향 통제 | 반대 근거 검색을 수행했고, 출처가 2개 이상이다. | 유리한 근거로 편중되지 않았는가 |
| 관점 커버리지 | 4.1~4.4절이 모두 비어 있지 않다. | 4개 관점을 실질적으로 다뤘는가 |

- 결과는 `eval_result = {passed, items: {name: {passed, reason}}}`로 둔다.
- 미달이면 Supervisor 규칙 9번으로 Loop를 돈다. 상한에 닿으면 verdict를 남기고 종료한다.

### 7. 부수 변경
- **`app.py`:**
  - `initial_state`에 `trace_id=run_id`를 넣는다.
  - 실행 config에 `metadata={"trace_id": run_id}`를 넣는다.
  - `recursion_limit`을 `max_steps`에서 계산한다.
  - LangSmith는 `.env`의 환경변수로 자동 활성화된다.
- **`workflow_logging.py`:** `fan_out`/`fan_in`/`query_rewrite` 전용 로그 분기와 `log_router` 목적지 이름을 새 노드에 맞게 정리하고, Supervisor 결정 로그를 추가한다.
- **`agents/report.py`:** "기술 재검색 N회" 같은 문구는 기존 필드를 그대로 사용한다. 재작업 횟수를 6.3절에 함께 표시한다.
- **README:** 샘플 구성으로 개편한다.
  - Overview: Pattern(Supervisor)과 선정 이유, 동적 처리 설명
  - Features: 확증 편향 방지, 품질 평가
  - Agents, State Schema 7항목, Architecture 이미지
  - Directory Structure: `supervisor.py` 추가 반영
  - Contributors: PM·PL 제외

### 8. 수정 파일
- **신규:** `supervisor.py`, `nodes/quality_eval.py`
- **수정:** `graph.py`, `state.py`, `agents/technical.py`, `agents/common.py`, `agents/domain.py`, `nodes/verification.py`(verification 묶음), `schemas.py`(EvalVerdict), `app.py`, `workflow_logging.py`, `README.md`, `tests/test_workflow.py`, `demo.py`(품질 평가 응답)
- **재사용:** `nodes/evidence.py`의 `first_evidence_check`·`second_evidence_check`·`query_rewrite`, `nodes/verification.py`의 `counter_evidence_node`·`conflict_node`, `agents/common.py`의 `evaluate`·`web_sources`

---

## Verification
1. **단위 테스트(`.venv/bin/python -m unittest discover -s tests`, demo 서비스, 외부 API 없음)**
   - 그래프 구조: 하위 에이전트의 나가는 엣지는 `supervisor`뿐이고, `supervisor`의 분기는 조건부 엣지 하나다.
   - 근거 부족 scenario: 해당 Agent로 재작업이 라우팅되고, 충분해진 뒤에만 `report`로 간다.
   - 품질 미달 scenario: Loop가 1회 돌고 종료한다.
   - `max_steps`를 작게 주면 보고서를 생성하고 종료한다.
   - 고정 흐름을 전제로 한 기존 테스트(병렬 fan-out 등)는 새 구조에 맞게 바꾼다.
2. `.venv/bin/python app.py --demo`로 흐름을 확인하고, `--show-graph`로 hub-and-spoke 구조를 확인한다.
3. `.venv/bin/python app.py --run`으로 실제 실행한다.
   - `report.pdf`가 생성되고 10장 이하인지 본다.
   - 로그에 `SUPERVISOR_DECISION`과 재작업이 1회 이상 있는지 본다.
   - LangSmith 프로젝트 `skala-RAG-supervisor`에 같은 `trace_id`의 트레이스가 남았는지 본다.
