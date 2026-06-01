"""회의 액션아이템 분석 대시보드 (Streamlit, custom-styled).

구성 원칙: 단순 차트 나열이 아니라 '의사결정 흐름'.
  ① 추이      → 워크로드/완료율 추세 파악
  ② 미완료 Top → 누가 병목인가, 업무 재분배
  ③ 반복 키워드 → 근본 원인 과제 식별
  ④ confidence → 낮은 항목만 사람이 검수

실행: streamlit run dashboard/app.py   (먼저 `make demo` 로 데이터 적재 + 진행상황)
"""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import duckdb
import plotly.graph_objects as go
import polars as pl
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from meeting_ai import config  # noqa: E402
from meeting_ai.keywords import top_keywords  # noqa: E402

TREND_WEEKS = 12  # 추이 차트에 표시할 주차 수 (현재 주 포함, 과거로)

st.set_page_config(page_title="회의 분석 대시보드", layout="wide", page_icon="📋")

st.markdown("""
<style>
  .block-container {padding-top: 2rem; max-width: 1240px;}
  .kpi {background:#fff; border:1px solid #ececf0; border-radius:14px; padding:16px 18px;}
  .kpi .label {font-size:13px; color:#6b7280;}
  .kpi .value {font-size:30px; font-weight:700; color:#111827; line-height:1.3;}
  .kpi .delta-up {font-size:12px; color:#16a34a;}
  .kpi .delta-down {font-size:12px; color:#dc2626;}
  .kpi .delta-flat {font-size:12px; color:#9ca3af;}
  .wtitle {font-size:16px; font-weight:700; color:#111827; margin-bottom:2px;}
  .wsub {color:#6b7280; font-size:13px; margin-bottom:6px;}
  .chip {display:inline-block; padding:7px 13px; margin:4px; border-radius:9px;
         font-size:14px; font-weight:600;}
  .chip-hi {background:#fdecec; color:#c0392b;}
  .chip-mid {background:#eaf1fb; color:#2563eb;}
  .chip-lo {background:#eef6ee; color:#3b8a3b;}
  .quote {background:#fafafa; border:1px solid #eee; border-radius:8px;
          padding:8px 12px; margin:6px 0; font-size:13px; color:#374151;
          display:flex; justify-content:space-between; gap:10px;}
  .conf-tag {color:#c0392b; font-weight:600; white-space:nowrap;}
</style>
""", unsafe_allow_html=True)

if not config.DB_PATH.exists():
    st.warning("DB가 없습니다. 터미널에서 `make demo` 를 먼저 실행하세요.")
    st.stop()

con = duckdb.connect(str(config.DB_PATH), read_only=True)
meetings = con.execute("SELECT * FROM meetings ORDER BY date").pl()
items = con.execute("SELECT * FROM action_items").pl()
utt = con.execute("SELECT meeting_id, text FROM utterances").pl()
con_total = con.execute("SELECT COUNT(*) FROM action_items").fetchone()[0]
con.close()

if items.height == 0:
    st.warning("적재된 액션아이템이 없습니다. `make demo` 를 실행하세요.")
    st.stop()

adv_map = dict(zip(meetings["meeting_id"].to_list(), meetings["advertiser"].to_list()))
items = items.with_columns(
    pl.col("meeting_id").replace_strict(adv_map, default="(미상)").alias("advertiser"))

# ── 헤더 + 필터 ──
hl, hr = st.columns([3, 1])
hl.markdown("### 📋 회의 분석 대시보드")
hl.caption("모비데이즈 AI Lab · 회의 transcript → 액션아이템 자동 추출")
advertisers = ["(전체)"] + sorted([a for a in meetings["advertiser"].unique().to_list() if a])
sel_adv = hr.selectbox("광고주", advertisers, label_visibility="collapsed")

m_view = meetings if sel_adv == "(전체)" else meetings.filter(pl.col("advertiser") == sel_adv)
mids = m_view["meeting_id"].to_list()
i_view = items.filter(pl.col("meeting_id").is_in(mids))
u_view = utt.filter(pl.col("meeting_id").is_in(mids))


# ── 최근 N주 고정 축 (데이터 없는 주는 빈칸) ──
def recent_week_labels(anchor: dt.date, n: int) -> list[str]:
    monday = anchor - dt.timedelta(days=anchor.weekday())
    return [(monday - dt.timedelta(weeks=i)).strftime("%Y-W%V") for i in range(n - 1, -1, -1)]


has_dates = m_view.height and m_view["date"].null_count() < m_view.height
if has_dates:
    latest = m_view["date"].cast(pl.Date).max()
    anchor = max(dt.date.today(), latest)  # 현재 주 기준, 데이터가 미래면 데이터 끝 기준
    axis = pl.DataFrame({"week": recent_week_labels(anchor, TREND_WEEKS)})

    mi = (m_view.join(i_view.group_by("meeting_id").agg(pl.len().alias("n_items")),
                      on="meeting_id", how="left").with_columns(pl.col("n_items").fill_null(0)))
    done_m = (i_view.filter(pl.col("status") == "done")
              .group_by("meeting_id").agg(pl.len().alias("n_done")))
    mi = mi.join(done_m, on="meeting_id", how="left").with_columns(pl.col("n_done").fill_null(0))
    mi = mi.with_columns(pl.col("date").cast(pl.Date).dt.strftime("%Y-W%V").alias("week"))
    agg = mi.group_by("week").agg(pl.len().alias("meetings"),
                                  pl.col("n_items").sum().alias("items"),
                                  pl.col("n_done").sum().alias("done"))
    wk = (axis.join(agg, on="week", how="left")
          .with_columns(pl.col("meetings").fill_null(0), pl.col("items").fill_null(0),
                        pl.col("done").fill_null(0))
          # 데이터 없는 주는 완료율 null → 라인 끊김
          .with_columns(pl.when(pl.col("items") > 0)
                        .then((100 * pl.col("done") / pl.col("items")).round(0))
                        .otherwise(None).alias("rate")))
else:
    wk = pl.DataFrame()


def _delta(cur, prev, unit=""):
    if prev is None:
        return '<span class="delta-flat">—</span>'
    d = cur - prev
    if d > 0:
        return f'<span class="delta-up">▲ {d}{unit} 지난주 대비</span>'
    if d < 0:
        return f'<span class="delta-down">▼ {abs(d)}{unit} 지난주 대비</span>'
    return '<span class="delta-flat">— 지난주와 동일</span>'


# KPI 델타: 데이터가 있는 마지막 두 주 비교
nonzero = wk.filter(pl.col("items") > 0) if wk.height else pl.DataFrame()
last_items = int(nonzero["items"][-1]) if nonzero.height >= 1 else None
prev_items = int(nonzero["items"][-2]) if nonzero.height >= 2 else None

# ── KPI ──
open_cnt = i_view.filter(pl.col("status") != "done").height
avg_conf = i_view["confidence"].mean()


def kpi(col, label, value, delta_html=""):
    col.markdown(f'<div class="kpi"><div class="label">{label}</div>'
                 f'<div class="value">{value}</div>{delta_html}</div>',
                 unsafe_allow_html=True)


k1, k2, k3, k4 = st.columns(4)
kpi(k1, "회의 수", m_view.height)
kpi(k2, "신규 액션아이템", i_view.height, _delta(last_items, prev_items, "건"))
kpi(k3, "미완료", open_cnt)
kpi(k4, "평균 Confidence", f"{avg_conf:.2f}")
st.write("")

# ── 위젯 1 + 2 ──
c1, c2 = st.columns([1.3, 1])

with c1:
    with st.container(border=True):
        st.markdown('<div class="wtitle">위젯 1 · 주차별 회의 & 액션아이템 추이</div>'
                    f'<div class="wsub">최근 {TREND_WEEKS}주 · 막대=발생 건수, 선=완료율 '
                    '(데이터 없는 주는 빈칸)</div>', unsafe_allow_html=True)
        if wk.height:
            p = wk.to_pandas()
            fig = go.Figure()
            fig.add_bar(x=p["week"], y=p["meetings"], name="회의 수", marker_color="#3b82f6")
            fig.add_bar(x=p["week"], y=p["items"], name="액션아이템", marker_color="#ef4444")
            fig.add_trace(go.Scatter(x=p["week"], y=p["rate"], name="완료율(%)", yaxis="y2",
                                     mode="lines+markers", line_color="#22a06b",
                                     connectgaps=False))
            fig.update_layout(barmode="group", height=340, bargap=0.25,
                              margin=dict(t=10, b=10, l=10, r=10),
                              yaxis=dict(title="건수"),
                              yaxis2=dict(title="완료율(%)", overlaying="y", side="right",
                                          range=[0, 105], showgrid=False),
                              legend=dict(orientation="h", y=1.15), plot_bgcolor="white")
            fig.update_xaxes(tickangle=-45)
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("날짜 정보가 있는 회의가 없어 추이를 표시할 수 없습니다.")

with c2:
    with st.container(border=True):
        st.markdown('<div class="wtitle">위젯 2 · 담당자별 미완료 Top N</div>'
                    '<div class="wsub">과부하 담당자 식별 → 업무 재분배 결정</div>',
                    unsafe_allow_html=True)
        open_items = i_view.filter(pl.col("status") != "done")
        by_owner = (open_items.with_columns(pl.col("owner_role").fill_null("(담당자 미정)"))
                    .group_by("owner_role").agg(pl.len().alias("cnt"))
                    .sort("cnt", descending=True))
        if by_owner.height:
            p = by_owner.to_pandas()
            colors = ["#dc2626", "#ea580c", "#f59e0b", "#3b82f6", "#22a06b", "#9ca3af"]
            fig = go.Figure(go.Bar(x=p["cnt"], y=p["owner_role"], orientation="h",
                                   marker_color=colors[:len(p)],
                                   text=[f"{v}건" for v in p["cnt"]], textposition="outside"))
            fig.update_layout(height=340, margin=dict(t=10, b=10, l=10, r=30),
                              yaxis=dict(autorange="reversed"), plot_bgcolor="white",
                              xaxis=dict(title="미완료 건수", rangemode="tozero"))
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.success("미완료 항목이 없습니다.")

# ── 위젯 3 + 4 ──
c3, c4 = st.columns([1, 1])

with c3:
    with st.container(border=True):
        st.markdown('<div class="wtitle">위젯 3 · 반복 이슈 키워드 (BoW)</div>'
                    '<div class="wsub">여러 회의 반복 등장 = 구조적 과제 (빨강=반복 多)</div>',
                    unsafe_allow_html=True)
        docs = {r["meeting_id"]: r["text"]
                for r in u_view.group_by("meeting_id").agg(
                    pl.col("text").str.join(" ").alias("text")).iter_rows(named=True)}
        kws = top_keywords(docs, top_n=12)
        if kws:
            chips = []
            for k in kws:
                cls = "chip-hi" if k["df"] >= 3 else ("chip-mid" if k["df"] == 2 else "chip-lo")
                chips.append(f'<span class="chip {cls}">{k["keyword"]}</span>')
            st.markdown("<div>" + "".join(chips) + "</div>", unsafe_allow_html=True)
            rep = [k["keyword"] for k in kws if k["df"] >= 3]
            if rep:
                st.caption("🔁 반복 이슈: " + ", ".join(rep) + " → 근본 원인 과제 후보")
        else:
            st.info("키워드를 추출할 발화가 없습니다.")

with c4:
    with st.container(border=True):
        st.markdown('<div class="wtitle">위젯 4 · LLM Confidence 분포 + 드릴다운</div>'
                    '<div class="wsub">신뢰도 낮은 항목만 사람이 검수 → 검증 비용 최소화</div>',
                    unsafe_allow_html=True)
        bands = [("0.9 이상", 0.9, 1.01, "#22a06b"), ("0.7–0.9", 0.7, 0.9, "#84cc16"),
                 ("0.5–0.7", 0.5, 0.7, "#f59e0b"), ("0.5 미만", 0.0, 0.5, "#dc2626")]
        total = i_view.height
        labels, vals, cols = [], [], []
        for name, lo, hi, color in bands:
            c = i_view.filter((pl.col("confidence") >= lo) & (pl.col("confidence") < hi)).height
            labels.append(name); vals.append(round(100 * c / total)); cols.append(color)
        fig = go.Figure(go.Bar(x=vals, y=labels, orientation="h", marker_color=cols,
                               text=[f"{v}%" for v in vals], textposition="outside"))
        fig.update_layout(height=200, margin=dict(t=6, b=6, l=10, r=30),
                          yaxis=dict(autorange="reversed"), plot_bgcolor="white",
                          xaxis=dict(range=[0, 100]))
        st.plotly_chart(fig, use_container_width=True)
        low = (i_view.filter(pl.col("confidence") < 0.6)
               .sort("confidence").select(["source_quote", "confidence"]))
        st.markdown("**검수 필요 (confidence < 0.6)**")
        if low.height:
            for row in low.iter_rows(named=True):
                q = (row["source_quote"] or "")[:48]
                st.markdown(f'<div class="quote"><span>"{q}…"</span>'
                            f'<span class="conf-tag">conf {row["confidence"]:.2f}</span></div>',
                            unsafe_allow_html=True)
        else:
            st.success("검수가 필요한 낮은 신뢰도 항목이 없습니다.")

st.caption(f"DB: {config.DB_PATH.name} · 회의 {meetings.height}건 · 액션아이템 {con_total}건")
