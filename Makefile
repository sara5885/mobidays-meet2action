# 맥/리눅스에서 python3 기본. 다른 인터프리터: make run PYTHON=python
PYTHON  ?= python3
PROVIDER ?= ollama

.PHONY: install run dashboard ingest ingest-all synth progress test gemini-check eval eval-diar stt clean

install:
	$(PYTHON) -m pip install -r requirements.txt

## run: 한 줄 데모 — DB 초기화 → 전체 회의 적재 → 진행상황 시뮬 → 대시보드 실행
##      키/설치 없이 동작하도록 mock LLM 고정(외부 전송 0). 실제 LLM은 make ingest-all 사용.
run: clean
	LLM_PROVIDER=mock PYTHONPATH=src $(PYTHON) -m meeting_ai.pipeline --all
	LLM_PROVIDER=mock PYTHONPATH=src $(PYTHON) scripts/simulate_progress.py
	PYTHONPATH=src $(PYTHON) -m streamlit run dashboard/app.py

## dashboard: (이미 적재된 상태에서) 대시보드만 다시 실행
dashboard:
	PYTHONPATH=src $(PYTHON) -m streamlit run dashboard/app.py

## ingest: 제공 실데이터 1건만 적재 (.env의 LLM_PROVIDER 사용)
ingest:
	PYTHONPATH=src $(PYTHON) -m meeting_ai.pipeline data/raw/ko_meeting_3speakers.json

## ingest-all: 실데이터 + 합성 회의 전체 적재 (.env의 LLM_PROVIDER 사용)
ingest-all:
	PYTHONPATH=src $(PYTHON) -m meeting_ai.pipeline --all

## progress: 진행상황(상태) 시뮬레이션 — 완료율/추이 위젯용
progress:
	PYTHONPATH=src $(PYTHON) scripts/simulate_progress.py

## synth: 합성 회의 transcript 재생성
synth:
	PYTHONPATH=src $(PYTHON) scripts/gen_synthetic.py

## test: 멱등성·스키마·상태보존 테스트 3종 (항상 mock으로 결정적)
test:
	PYTHONPATH=src $(PYTHON) -m pytest -q

## gemini-check: Gemini 연결 점검 (.env 설정 후)
gemini-check:
	PYTHONPATH=src $(PYTHON) scripts/check_gemini.py

## eval: 추출 품질 precision/recall/F1 (gold vs 실제 LLM). make eval PROVIDER=ollama|gemini
eval:
	LLM_PROVIDER=$(PROVIDER) PYTHONPATH=src $(PYTHON) scripts/eval_extraction.py --provider $(PROVIDER)

## eval-diar: 화자 매핑 정확도 평가 (문장 단위 vs 조각 단위)
eval-diar:
	LLM_PROVIDER=$(PROVIDER) PYTHONPATH=src $(PYTHON) scripts/eval_diarization.py --provider $(PROVIDER)

## stt: 로컬 Whisper STT + (LLM 보조) 화자 정규화 → transcript JSON (가산점)
##      기본 샘플: make stt   /   다른 파일: make stt FILE=data/raw/회의.mp3
stt:
	PYTHONPATH=src $(PYTHON) scripts/run_local_stt.py $(FILE)

## clean: DB·생성 산출물 제거
clean:
	rm -f data/db/meeting.duckdb data/db/meeting.duckdb.wal data/slack_payload_*.json
