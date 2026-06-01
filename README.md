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

# 3. 파이프라인 실행 (transcript → DuckDB 적재 → 액션아이템)
make run        # 제공된 실데이터 1건 적재
make run-all    # 실데이터 + 합성 회의 전체 적재
make demo       # run-all + 진행상황 시뮬레이션 (대시보드용 권장)

# 4. 대시보드 (먼저 make demo 권장)
make dashboard          # streamlit run dashboard/app.py
#   대시보드에서 액션아이템 상태(대기/진행중/완료/지연)를 직접 변경·저장 가능
#   → action_status에 저장되어 파이프라인 재실행에도 보존됨

# (스키마가 바뀐 버전으로 처음 실행할 때는 기존 DB 제거 후 다시 적재)
make clean && make demo

# 테스트 (멱등성·스키마 검증)
make test
```

> **데이터 적재 절차**: `make run` 이 `data/raw/ko_meeting_3speakers.json`(제공 실데이터)을 읽어
> 정제 → 청크 → DuckDB(`data/db/meeting.duckdb`) 적재 → 액션아이템 추출까지 수행합니다.
> 대시보드의 추이·키워드 위젯은 회의가 여러 건이어야 의미가 있어, `make run-all` 로
> 합성 회의(`make synth` 로 생성)까지 함께 적재합니다.
> 다른 회의는 `PYTHONPATH=src python -m meeting_ai.pipeline <경로>` 로 개별 적재합니다.

## 대시보드 위젯 (3.4)

`make run-all` 후 `make dashboard` 로 실행. "의사결정 흐름"으로 구성:

1. **주차별 회의·액션아이템 추이** — 워크로드가 몰리는 주 파악 → 리소스 사전 배분
2. **담당자별 미완료 Top N** — 누가 병목인가 → 업무 재분배
3. **반복 이슈 키워드(BoW)** — 여러 회의에 반복 등장하는 주제(예: 전환 추적/픽셀) → 근본 원인 과제 식별
4. **confidence 분포 + 낮은 항목 드릴다운** — 낮은 신뢰도 항목만 사람이 검수

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
ActionItem[] ──► DuckDB (action_items)            [멱등 적재, 재실행마다 교체]
   │
   ├─► slack_payload.py  →  data/slack_payload_sample.json
   └─► dashboard/app.py  →  Streamlit 대시보드 (위젯 4개 + 상태 편집)
                              │
                              └─► action_status / status_history  (사람이 관리, 보존)
```

**모듈 분리 기준**: I/O(loader/db) · 순수 변환(preprocess) · LLM 경계(llm/, extract) ·
표현(dashboard)을 분리해, LLM provider 교체나 스키마 변경이 다른 층에 번지지 않게 했습니다.

### 추출/트래킹 레이어 분리 (스키마 설계 핵심)

| 테이블 | 소유 | 갱신 정책 |
|---|---|---|
| `action_items` | AI 추출 | 파이프라인 재실행마다 meeting_id 단위 delete-replace (멱등) |
| `action_status` | 사람(담당자) | 상태(open/in_progress/done/blocked)+지연사유. 파이프라인이 건드리지 않아 **재실행에도 보존** |
| `status_history` | 시스템 | 상태 변경 이력(감사 추적) |

두 레이어는 안정 식별자 `action_key`(= meeting_id + 정규화 제목 해시)로 연결됩니다.
LLM 출력은 비결정적이라 제목이 흔들릴 수 있어, **회의(meeting_id)를 멱등 단위로 삼아
추출을 통째 교체하되, 사람이 바꾼 진행상황은 action_key로 따로 보존**합니다.
이로써 "AI 추출은 멱등하게 재생성 + 사람의 상태 관리는 생존"이 동시에 성립합니다.
(가산점 항목: 액션아이템 진행상황 업데이트 루프 — `scripts/simulate_progress.py`가 mock 루프)

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

- **transcript 포맷**: loader가 두 포맷을 모두 흡수(방어적 파싱) — 제공 실데이터
  (`speaker`=이름, `role` 내장, 타임스탬프 없음, `speakers` 리스트)와 일반 STT/diarization
  포맷(`speakers={code:role}`, 타임스탬프). Whisper 출력으로 교체해도 동일 로더로 처리됩니다.
- **합성 데이터**: 제공된 실제 회의는 `nova-2026-05-28` 1건뿐입니다. 대시보드의 추이/반복
  키워드 위젯이 의미를 가지려면 여러 회의가 필요해, `scripts/gen_synthetic.py` 로 동일 구조의
  가상 회의 3건(다른 주차·광고주, '픽셀/전환 추적' 이슈 반복)을 생성해 함께 적재합니다.
- **외부 API 금지 제약**: Gemini는 외부 API이므로, 본 PoC는 *가상 광고주(노바드림)* 시나리오
  검증에만 사용합니다. 실서비스에선 사내 LLM/온프레미스로 대체하는 것을 전제로 합니다.
  기본값(`LLM_PROVIDER=mock`)은 외부 전송 없이 전체 흐름을 시연합니다.
- **이슈 키워드**: 형태소 분석기 의존을 피하려 정규식 토큰화+조사 제거+불용어 기반 BoW로 구현.
  데이터가 누적되면 임베딩 클러스터링으로 고도화 가능(향후 확장).
- 음성(STT)은 동봉 transcript JSON으로 대체. 로컬 Whisper 적용은 가산점 항목으로 별도 추가 예정.

## 현재 상태

✅ 핵심 파이프라인: transcript → 정제 → DuckDB 멱등 적재 → mock 추출(스키마 강제/검증/재시도/환각필터) → 대시보드
✅ 대시보드 위젯 4개 (추이 / 담당자별 미완료 / 반복 키워드 / confidence 분포+드릴다운)
✅ 다회의 적재(`make run-all`), 멱등성·스키마 테스트 통과

⬜ 남은 작업: 실제 Gemini 호출 PoC / 기획안 5p / 대시보드 화면 녹화 / (가산점) Whisper STT·precision-recall
