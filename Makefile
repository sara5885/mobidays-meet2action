.PHONY: install run run-all synth progress demo dashboard clean test

install:
	pip install -r requirements.txt

# 한 줄 실행: 제공된 실데이터 1건 적재
run:
	PYTHONPATH=src python -m meeting_ai.pipeline data/raw/ko_meeting_3speakers.json

# 모든 회의(실데이터 + 합성) 적재 — 대시보드 추이/키워드 위젯용
run-all:
	PYTHONPATH=src python -m meeting_ai.pipeline --all

# 합성 회의 재생성
synth:
	PYTHONPATH=src python scripts/gen_synthetic.py

# 진행상황 업데이트 루프(mock): 완료율 추이용 상태 시뮬레이션
progress:
	PYTHONPATH=src python scripts/simulate_progress.py

# 대시보드용 전체 준비: 적재 + 진행상황
demo: run-all progress

dashboard:
	PYTHONPATH=src streamlit run dashboard/app.py

test:
	PYTHONPATH=src python -m pytest -q

clean:
	rm -f data/db/meeting.duckdb data/slack_payload_sample.json
