"""Streamlit 대시보드 (1단계: 액션아이템 표 + confidence). 2단계에서 위젯 4개로 확장.

실행: streamlit run dashboard/app.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import duckdb
import streamlit as st

# src 경로 추가
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from meeting_ai import config  # noqa: E402

st.set_page_config(page_title="회의 액션아이템 대시보드", layout="wide")
st.title("📋 회의록 · 액션아이템 대시보드")

if not config.DB_PATH.exists():
    st.warning("DB가 없습니다. 먼저 `make run` 으로 파이프라인을 실행하세요.")
    st.stop()

con = duckdb.connect(str(config.DB_PATH), read_only=True)
items = con.execute("""
    SELECT meeting_id, title, owner_role, due, status, confidence, source_quote
    FROM action_items ORDER BY confidence DESC
""").pl()

st.subheader("액션아이템")
st.dataframe(items, use_container_width=True)

c1, c2 = st.columns(2)
c1.metric("총 액션아이템", len(items))
if len(items):
    c2.metric("평균 confidence", f"{items['confidence'].mean():.2f}")

st.caption("1단계 뼈대 — 2단계에서 추이/담당자별 미완료/이슈 키워드/confidence 분포 위젯 추가 예정")
