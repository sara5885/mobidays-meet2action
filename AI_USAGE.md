# AI 활용 내역 (AI_USAGE.md)

본 과제는 AI 도구를 적극 활용하되, **결과물을 비판적으로 판단·수정**하는 데 초점을 뒀습니다.

## 사용한 AI 도구
- **Claude (Cowork)** — 아키텍처 설계 토론, 코드 스캐폴딩, 프롬프트 초안, 디버깅, 문서 작성.
- **Gemini 2.5 Flash** — 액션아이템 추출 LLM 중 하나 (`LLM_PROVIDER=gemini`).
- **Ollama (qwen2.5:7b, 로컬)** — 무료·무제한·온프레미스 LLM (`LLM_PROVIDER=ollama`).
- **Whisper (로컬)** — 음성 → 텍스트 STT.

## 의사결정 과정 / 컨텍스트
- **walking skeleton 전략**: 시간 제약 하에 end-to-end 한 줄기를 mock으로 먼저 완성하고,
  배점 큰 순서(LLM 신뢰성 → 정제 → 대시보드 → 가산점)로 두껍게 확장.
- **provider 추상화**: mock / gemini / ollama 를 환경변수 하나로 토글. 외부 유출 금지 제약,
  rate limit, 시연 안정성을 동시에 만족시키기 위한 설계.
- **DuckDB 채택**: 100명 PoC 규모에 Postgres는 과하고, 분석 쿼리엔 SQLite보다 적합.

## AI 결과물을 직접 수정/판단한 사례
실제로 작업하며 AI 초안을 비판적으로 고친 사례들:

1. **confidence 후처리 추가** — AI 초안은 LLM이 준 confidence를 그대로 신뢰했음.
   담당자(owner)가 불명확하거나 인용 근거(source_seg_ids)가 입력에 없으면(환각) confidence
   상한을 거는 `_postprocess`를 직접 추가. *이유: 신뢰 불가 출력을 그대로 데이터화하면 안 됨.*

2. **추출/트래킹 레이어 분리** — 초기 설계는 `action_items`에 status를 같이 두었으나,
   파이프라인 재실행(delete-replace) 시 사람이 바꾼 상태가 날아가는 문제를 발견.
   → `action_status`/`status_history`로 분리하고 안정 식별자 `action_key`로 연결해
   "추출은 멱등 재생성 + 사람 상태는 보존"이 양립하도록 재설계.

3. **빈 액션아이템 적재 크래시 수정** — Gemini 429로 한 회의가 0건이 됐을 때
   DuckDB `executemany`가 빈 리스트로 죽는 버그를 실행 중 발견 → 빈 리스트 가드 추가.

4. **429 rate-limit 백오프** — 무료 티어 분당 5회 제한에 걸려 추출이 끊기는 걸 보고,
   스키마 재시도와 별개로 권장 대기시간만큼 쉬고 재시도하는 로직을 추가.

5. **로더 방어적 파싱** — 제공 transcript의 실제 구조(speaker=이름, role 내장, timestamp 없음,
   speakers 리스트)가 AI 가정과 달라, 두 포맷을 모두 흡수하도록 수정.

6. **BoW 키워드 잡음 제거** — 초기 키워드에 "제가/내가/채린님이" 같은 대명사·호칭이 섞여,
   조사 제거 + 불용어 + 화자 이름 필터를 직접 보강.

## 프롬프트 예시
- "한국어 광고 회의의 암묵적 R&R 표현('그건 제가 챙길게요')을 담당자로 매핑하는 few-shot 프롬프트를 설계해줘"
- "LLM JSON 출력을 pydantic으로 검증하고 실패 시 강화 프롬프트로 재시도하는 패턴을 짜줘"
- "재실행 시 사람이 바꾼 상태는 보존하면서 추출 결과는 멱등하게 교체하는 스키마를 설계해줘"
