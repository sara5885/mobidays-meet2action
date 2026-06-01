# AI 활용 내역 (AI_USAGE.md)

## 사용한 AI 도구
- **Claude (Cowork)**: 아키텍처 설계 토론, 코드 스캐폴딩, 프롬프트 초안, README 작성.
- (선택) **Gemini 2.0 Flash**: 액션아이템 추출 LLM 본체 (`LLM_PROVIDER=gemini`).

## 의사결정 과정 / 컨텍스트
- 시간 제약(2일) → **walking skeleton 전략**: end-to-end 한 줄기를 mock으로 먼저 완성하고,
  배점 큰 순서(LLM 신뢰성 → 정제 → 대시보드)로 두껍게 확장.
- 저장소로 DuckDB 채택: 100명 PoC 규모에 Postgres는 과하고, 분석 쿼리엔 SQLite보다 적합.
- LLM은 provider 추상화로 mock↔Gemini 토글 → 외부 API 금지 제약과 시연 안정성 양립.

## AI 결과물을 직접 수정한 판단 사례
> (작업하며 실제 사례로 계속 채워넣기 — 예시 템플릿)
- **예) confidence 보정 로직 추가**: AI가 생성한 추출 코드는 LLM이 준 confidence를 그대로
  신뢰했음. → 담당자 미상이거나 인용 근거가 입력에 없으면 confidence 상한을 거는 후처리
  (`_postprocess`)를 직접 추가. 이유: 신뢰 불가 출력을 그대로 데이터 자산화하면 안 되기 때문.
- **예) loader 방어적 파싱**: AI 초안은 transcript 키를 고정했으나, 실데이터 필드명 미확정이라
  여러 키를 흡수하도록 수정.

## 프롬프트 예시
- "한국어 광고 회의의 암묵적 R&R 표현을 담당자로 매핑하는 few-shot 프롬프트를 설계해줘"
- "LLM JSON 출력을 pydantic으로 검증하고 실패 시 재시도하는 패턴을 짜줘"
