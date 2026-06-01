.PHONY: install run dashboard clean test

install:
	pip install -r requirements.txt

# 한 줄 실행: 파이프라인 전체 (transcript -> DuckDB -> 액션아이템)
run:
	PYTHONPATH=src python -m meeting_ai.pipeline data/raw/ko_meeting_3speakers.json

dashboard:
	PYTHONPATH=src streamlit run dashboard/app.py

test:
	PYTHONPATH=src python -m pytest -q

clean:
	rm -f data/db/meeting.duckdb data/slack_payload_sample.json
