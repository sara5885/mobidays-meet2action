# 회의록 자동 정리 · 액션아이템 추출 · 분석 대시보드 (PoC)

회의 transcript(또는 음성)에서 **회의록 정제 → 액션아이템 자동 추출 → 분석 대시보드**까지
한 흐름으로 동작하는 작은 데이터·AI 시스템입니다.

## 실행 방법

```bash
make install     # 의존성 설치 (또는: pip install -r requirements.txt)
make run         # ⭐ 한 줄 데모: DB 초기화 → 전체 회의 적재 → 진행상황 시뮬 → 대시보드 실행
```

`make run` 한 줄이면 키·설치 없이 전체 흐름이 돕니다 (mock LLM 고정 → **외부 전송 0**,
결정적 출력). 데이터를 적재하고 대시보드(Streamlit)까지 자동으로 띄웁니다.

```bash
# (선택) 실제 LLM로 추출해 보기 — .env 설정 후
cp .env.example .env
#   LLM_PROVIDER:
#     mock   — 결정적 응답(기본). 키/설치 불필요
#     gemini — Gemini 2.5 Flash. GEMINI_API_KEY 필요 (무료 티어 분당 5회). 외부 API라 가상 광고주만
#     ollama — 로컬 LLM(무료·무제한·온프레미스). `ollama serve` + `ollama pull qwen2.5:7b`
make ingest-all  # 전체 회의 적재 (.env의 provider 사용) → 이후 make dashboard
make ingest      # 제공 실데이터 1건만 적재

make dashboard   # (이미 적재된 상태에서) 대시보드만 다시 실행
make test        # 멱등성·스키마·상태보존 테스트 3종 (항상 mock으로 결정적)

# (가산점) 로컬 Whisper STT — mp3 → 화자분리 transcript JSON
make stt                              # 제공 샘플 mp3
make stt FILE=data/raw/다른회의.mp3    # 임의 음성 파일
```

> **적재 절차**: 파이프라인이 `data/raw/*.json`(제공 실데이터 1건 + 합성 회의 3건)을 읽어
> 정제 → 청크 → DuckDB(`data/db/meeting.duckdb`) 적재 → 회의록 정리 + 액션아이템 추출까지 수행.
> 제공된 실제 회의는 `nova-2026-05-28` 1건뿐이라, 추이·반복키워드 위젯이 의미를 가지도록
> `scripts/gen_synthetic.py`로 만든 가상 회의 3건을 함께 적재합니다.

## 대시보드 (3.4) — "의사결정 흐름"으로 구성

`make run` 으로 실행. 단순 차트 나열이 아니라 회의록 열람 → 진척 파악 → 검수 → 상태 관리 순:

- **📄 회의록 (자동 정리)** — 회의 목록 → 클릭 → 요약·안건·결정사항 + 액션아이템 + 원문
  transcript 대조. 회의록은 화면에서 직접 수정 가능(override 보존).
- **KPI** — 회의 수, 액션아이템(완료/전체)·완료율.
- **주차별 회의·액션아이템 추이** — 워크로드가 몰리는 주 파악 → 리소스 사전 배분.
- **담당자별 미완료 Top N** — 누가 병목인가 → 업무 재분배.
- **반복 이슈 키워드(BoW)** — 여러 회의에 반복 등장하는 주제(전환 추적/픽셀 등) → 근본 원인 과제.
- **LLM Confidence 분포 + 검수 권장** — 신뢰도 낮은 항목만 사람이 검수.
- **✏️ 액션아이템 상태 관리** — 담당자·제목·기한·상태 편집, 추가(➕)·삭제(🗑)를 모두
  **"변경사항 검토 및 저장"** 한 흐름으로 확인 후 반영. 회의별·담당자별로 묶여 표시되고,
  사람이 바꾼 값은 파이프라인 재실행에도 보존됩니다(아래 멱등성 참고).

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
   ├─► slack_payload.py  →  data/slack_payload_<meeting_id>.json (회의별)
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
| LLM | **mock / Gemini 2.5 Flash / Ollama** | provider 추상화로 3종 토글. Gemini=무료 native JSON, Ollama=무료·무제한·온프레미스(외부 유출 금지 충족), mock=결정적 시연 |
| STT | **로컬 Whisper** | 외부 유출 금지라 클라우드 STT 불가. 비용 0·온프레미스. 화자분리는 LLM 보조 |
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
- **STT**: 제공 transcript JSON으로도 동작하고, 로컬 Whisper로 mp3에서 직접 생성도 지원(`make stt`).
  Whisper는 받아쓰기만 하므로 화자 분리는 LLM 보조로 처리(정식 diarization은 향후 pyannote 등으로 대체).
  **실측 STT 품질**: 제공 mp3를 Whisper로 받아쓴 결과 한국어·광고 약어에서 오인식 다수
  ("GA→GAS", "이중 집계→이중 집게" 등, `docs/STT_품질검증.md`). 받아쓰기 품질이 파이프라인
  품질의 상한이므로 실서비스엔 고성능 STT가 필수이며, 약어 사전·LLM·사람 검수로 완충한다.

## 현재 상태

✅ 핵심 파이프라인: transcript → 정제 → DuckDB 멱등 적재 → 회의록 정리 + LLM 추출(스키마 강제/검증/재시도/환각필터) → 대시보드
✅ 실제 LLM 호출 PoC — **Gemini 2.5 Flash** 및 **Ollama(로컬)** 동작 확인 (provider 추상화)
✅ 대시보드 — 회의록 자동정리 열람·수정 + 위젯 + 액션아이템 상태 관리(검토→저장, 추가·삭제·수정)
✅ 로컬 Whisper STT + 화자 분리 (임의 mp3 → transcript JSON)
✅ 멱등성·스키마·상태보존 테스트 3종 통과 + 추출 품질 eval(F1 0.81)

## 알려진 한계 & 향후 계획

시간 제약상 구현하지 못했지만 방향이 정리된 것들 (상세: `docs/기획안.md` "현재 한계" / "향후 확장"):

- **회의 식별 멱등성** — 현재 `meeting_id`가 입력 JSON id(없으면 파일명)에 의존.
  → 업로드 시 **(광고주+날짜+시간) 복합키**로 결정하면 사람 개입 없이 회의 단위 멱등성 보장. *(우선순위 높음)*
- **액션아이템 항목 식별** — `action_key`가 정규화 제목 해시라 LLM 재서술(문구 변경)엔 취약(고아 발생).
  → temperature 고정 + **제목 임베딩 유사도 매칭**으로 보완.
- **STT 품질** — 한국어·광고 약어 오인식이 파이프라인 품질의 상한(`docs/STT_품질검증.md`).
  → 고성능 STT + 약어 사전·사람 검수로 완충. 정식 화자분리는 pyannote 등 음성 기반으로 대체.
- **기한 정규화** — due가 자연어 문자열("다음주 금요일"). → 절대 날짜 파싱으로 마감 임박순 정렬·D-day 알림.
- **평가의 한계** — gold가 1인 수기 기준 + 무료 티어라 LLM-as-judge 미적용. → 다수 평가자·judge 도입 시 절대 점수화.
- **KPI 주간 델타 / 지연(blocked) 사유 입력 UX** — 데이터 누적·UX 확정 후 추가.
