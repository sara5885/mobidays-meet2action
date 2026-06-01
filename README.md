# 회의록 자동 정리 · 액션아이템 추출 · 분석 대시보드 (PoC)

회의 transcript(또는 음성)에서 **회의록 정제 → 액션아이템 자동 추출 → 분석 대시보드**까지
한 흐름으로 동작하는 작은 데이터·AI 시스템입니다.

## 실행 방법

```bash
# 1. 설치
make install            # 또는: pip install -r requirements.txt

# 2. (선택) LLM 설정 — 기본은 mock이라 키 없이도 동작
cp .env.example .env
#   실제 Gemini 호출 시 .env 에서 LLM_PROVIDER=gemini, GEMINI_API_KEY=... 설정

# 3. 파이프라인 한 줄 실행 (transcript → DuckDB 적재 → 액션아이템)
make run

# 4. 대시보드
make dashboard          # streamlit run dashboard/app.py

# 테스트 (멱등성·스키마 검증)
make test
```

> **데이터 적재 절차**: `make run` 이 `data/raw/sample_transcript.json` 을 읽어
> 정제 → 청크 → DuckDB(`data/db/meeting.duckdb`) 적재 → 액션아이템 추출까지 수행합니다.
> 다른 회의는 `PYTHONPATH=src python -m meeting_ai.pipeline <경로>` 로 적재합니다.

## 아키텍처 및 데이터 흐름

```
transcript.json
   │  transcript_loader.py   (화자 정규화: SPK_1 → 역할명)
   ▼
Utterance[]
   │  preprocess.py          (머뭇거림 제거 · 약어 사전 · 의미단위 청크)
   ▼
Chunk[]  ──► DuckDB (utterances / chunks)        [멱등 적재]
   │
   │  extract.py + llm/*     (프롬프트 → JSON 강제 → 검증 → 재시도 → 환각필터)
   ▼
ActionItem[] ──► DuckDB (action_items)            [멱등 적재]
   │
   ├─► slack_payload.py  →  data/slack_payload_sample.json
   └─► dashboard/app.py  →  Streamlit 대시보드
```

**모듈 분리 기준**: I/O(loader/db) · 순수 변환(preprocess) · LLM 경계(llm/, extract) ·
표현(dashboard)을 분리해, LLM provider 교체나 스키마 변경이 다른 층에 번지지 않게 했습니다.

## 기술 스택 선택 근거 (trade-off)

| 영역 | 선택 | 이유 / trade-off |
|---|---|---|
| 저장소 | **DuckDB** | 파일 1개로 끝나는 임베디드 OLAP. 대시보드 집계 쿼리에 SQLite보다 강함. 100명 규모 PoC엔 Postgres 운영비가 과함 |
| 검증 | **pydantic** | LLM 출력을 타입·범위·필수값으로 강제 → 신뢰 불가 출력을 적재 전 차단 |
| LLM | **Gemini 2.0 Flash (+ mock)** | 무료 티어 넉넉 + native JSON output. provider 추상화로 mock↔실제 토글 |
| 데이터 처리 | **polars** | 대시보드 조회 성능, 표현력 |
| 대시보드 | **Streamlit** | Python 단일 스택, 빠른 PoC |

## 프롬프트 설계 근거

`src/meeting_ai/prompts.py` 참고.
- **role 지정**: 광고대행사 회의록 분석 전문 어시스턴트
- **도메인 컨텍스트**: CPM/ROAS/CTA/A/B 약어 정의, 한국어 암묵 R&R 매핑 규칙
  ("그건 제가 챙길게요" → 화자가 담당자)
- **few-shot**: 암묵 표현 → 담당자 매핑 예시 1개
- **스키마 강제**: `response_mime_type=application/json` + pydantic 재검증(이중 방어)
- **검증/재시도**: JSON 파싱·스키마 위반 시 강화 프롬프트로 재시도(`MAX_LLM_RETRIES`)
- **환각 방지**: `source_seg_ids` 가 실제 입력 발화에 존재하는지 검사, 없으면 confidence 하향

## 가정 사항

- **제공 transcript의 정확한 필드명 미확정** → loader가 흔한 키(`segments/utterances`,
  `speaker/spk` 등)를 모두 흡수하도록 방어적 파싱. 실데이터 수령 후 키만 맞추면 됩니다.
- **외부 API 금지 제약**: Gemini는 외부 API이므로, 본 PoC는 *가상 광고주(노바드림)* 시나리오
  검증에만 사용합니다. 실서비스에선 사내 LLM/온프레미스로 대체하는 것을 전제로 합니다.
  기본값(`LLM_PROVIDER=mock`)은 외부 전송 없이 전체 흐름을 시연합니다.
- 음성(STT)은 동봉 transcript JSON으로 대체. 로컬 Whisper 적용은 가산점 항목으로 별도 추가 예정.

## 현재 상태

✅ 1단계(walking skeleton): transcript → DuckDB → mock 추출 → 대시보드 end-to-end 동작

⬜ 2단계: 실제 Gemini 호출 / 정제 강화 / 대시보드 위젯 4개 / 기획안·녹화
