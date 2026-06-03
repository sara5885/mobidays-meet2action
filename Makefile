# 맥/리눅스에서 python3 기본. 다른 인터프리터 쓰려면: make run PYTHON=python
PYTHON ?= python3
PROVIDER ?= ollama

.PHONY: install run run-all synth progress demo demo-all dashboard clean test gemini-check stt eval eval-diar

install:
	$(PYTHON) -m pip install -r requirements.txt

# Gemini 연결 점검 (.env 설정 후)
gemini-check:
	PYTHONPATH=src $(PYTHON) scripts/check_gemini.py

# 한 줄 실행: 제공된 실데이터 1건 적재
run:
	PYTHONPATH=src $(PYTHON) -m meeting_ai.pipeline data/raw/ko_meeting_3speakers.json

# 모든 회의(실데이터 + 합성) 적재 — 대시보드 추이/키워드 위젯용
run-all:
	PYTHONPATH=src $(PYTHON) -m meeting_ai.pipeline --all

# 합성 회의 재생성
synth:
	PYTHONPATH=src $(PYTHON) scripts/gen_synthetic.py

# 진행상황 업데이트 루프(mock): 완료율 추이용 상태 시뮬레이션
progress:
	PYTHONPATH=src $(PYTHON) scripts/simulate_progress.py

# 대시보드용 준비: 제공 실데이터 1건만 적재 + 진행상황 (빠름)
demo: run progress

# 대시보드 추이/키워드 위젯까지 풍성하게: 합성 회의 포함 전체 적재 (느림 - LLM 호출 多)
demo-all: run-all progress

dashboard:
	PYTHONPATH=src $(PYTHON) -m streamlit run dashboard/app.py

test:
	PYTHONPATH=src $(PYTHON) -m pytest -q

# 추출 품질 평가 (precision/recall/F1) — gold(mock) vs 실제 LLM
#   make eval PROVIDER=ollama   (또는 gemini)
eval:
	LLM_PROVIDER=$(PROVIDER) PYTHONPATH=src $(PYTHON) scripts/eval_extraction.py --provider $(PROVIDER)

# 화자 매핑 정확도 평가 (문장 단위 vs 조각 단위)
eval-diar:
	LLM_PROVIDER=$(PROVIDER) PYTHONPATH=src $(PYTHON) scripts/eval_diarization.py --provider $(PROVIDER)

clean:
	rm -f data/db/meeting.duckdb data/slack_payload_sample.json

# 로컬 Whisper STT + (LLM 보조) 화자 정규화 → transcript JSON 생성
#   기본 샘플:  make stt
#   다른 파일:  make stt FILE=data/raw/다른회의.mp3
stt:
	PYTHONPATH=src $(PYTHON) scripts/run_local_stt.py $(FILE)