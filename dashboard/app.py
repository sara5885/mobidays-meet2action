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
from meeting_ai import config, db  # noqa: E402
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
# 추출(action_items) + 트래킹(action_status) 조인 → 상태는 사람이 관리하는 값
items = con.execute("""
    SELECT a.meeting_id, a.action_id, a.action_key, a.title, a.due,
           a.confidence, a.source_seg_ids, a.source_quote,
           -- 담당자: 사람이 고친 값(owner_override) 우선, 없으면 AI 추출값
           COALESCE(s.owner_override, a.owner_role) AS owner_role,
           a.owner_role AS owner_ai,
           COALESCE(s.status, 'open') AS status, s.delay_reason
    FROM action_items a
    LEFT JOIN action_status s USING (action_key)
""").pl()
utt = con.execute(
    "SELECT meeting_id, seg_id, speaker_role, text FROM utterances ORDER BY meeting_id, seg_id").pl()
con_total = con.execute("SELECT COUNT(*) FROM action_items").fetchone()[0]
# 회의록(요약·안건·결정) + 참석자 명단
summaries = {}
for mid in meetings["meeting_id"].to_list():
    summaries[mid] = db.get_summary(con, mid)
participants = {}
for mid in meetings["meeting_id"].to_list():
    participants[mid] = db.get_participants(con, mid)
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

# ── 📄 회의록 (목록 → 클릭 → 상세). 가독성 위해 기본은 목록만 표시 ──
if "sel_meeting" not in st.session_state:
    st.session_state.sel_meeting = None

# 회의별 액션아이템 집계 (목록 카드용)
_cnt = (i_view.group_by("meeting_id")
        .agg(pl.len().alias("n_items"),
             (pl.col("status") != "done").sum().alias("n_open")))
_cnt_map = {r["meeting_id"]: (r["n_items"], r["n_open"]) for r in _cnt.iter_rows(named=True)}

with st.container(border=True):
    st.markdown('<div class="wtitle">📄 회의록 (자동 정리)</div>'
                '<div class="wsub">회의를 클릭하면 안건·결정사항·액션아이템과 원문을 펼쳐봅니다.</div>',
                unsafe_allow_html=True)

    sel = st.session_state.sel_meeting
    valid_ids = set(m_view["meeting_id"].to_list())
    if sel not in valid_ids:
        sel = None

    if sel is None:
        # ── 목록 뷰 ──
        for r in m_view.sort("date", descending=True).iter_rows(named=True):
            mid = r["meeting_id"]
            n_items, n_open = _cnt_map.get(mid, (0, 0))
            c1_, c2_, c3_ = st.columns([5, 2, 1.2])
            c1_.markdown(f"**{r['title'] or mid}**　"
                         f"<span style='color:#888;font-size:12px'>{r['advertiser'] or '—'} · {r['date']}</span>",
                         unsafe_allow_html=True)
            c2_.markdown(f"<span style='color:#666;font-size:13px'>액션 {n_items} · 미완료 {n_open}</span>",
                         unsafe_allow_html=True)
            if c3_.button("열기 →", key=f"open_{mid}"):
                st.session_state.sel_meeting = mid
                st.rerun()
    else:
        # ── 상세 뷰 (회의록 페이지) ──
        if st.button("← 회의 목록"):
            st.session_state.sel_meeting = None
            st.rerun()
        mrow = m_view.filter(pl.col("meeting_id") == sel).to_dicts()[0]
        summ = summaries.get(sel, {"summary": "", "agenda": [], "decisions": []})
        parts = participants.get(sel, [])
        names = ", ".join(f'{p["name"]}({p["role"]})' for p in parts) or "—"

        st.markdown(f"### {mrow['title'] or sel}")
        st.markdown(f"**① 기본 정보** · 일시 {mrow['date']} · 광고주 {mrow['advertiser'] or '—'}  \n"
                    f"참석자: {names}")
        if summ["summary"]:
            st.caption("요약: " + summ["summary"])

        cL, cR = st.columns(2)
        with cL:
            st.markdown("**② 안건 (Agenda)**")
            st.markdown("\n".join(f"- {a}" for a in summ["agenda"]) if summ["agenda"]
                        else "_추출된 안건 없음_")
        with cR:
            st.markdown("**③ 결정 사항 (Decisions)**")
            st.markdown("\n".join(f"- {d}" for d in summ["decisions"]) if summ["decisions"]
                        else "_확정된 결정사항 없음 (흐릿한 논의는 제외)_")

        st.markdown("**④ 액션 아이템 (담당자 / 기한 / 할 일)**")
        ai = i_view.filter(pl.col("meeting_id") == sel).select(
            ["owner_role", "due", "title", "status", "confidence"]).rename(
            {"owner_role": "담당자", "due": "기한", "title": "할 일",
             "status": "상태", "confidence": "신뢰도"})
        if ai.height:
            st.dataframe(ai.to_pandas(), use_container_width=True, hide_index=True)
        else:
            st.caption("액션아이템 없음")

        with st.expander("🗒️ 원문 transcript 보기 (추출 검증용)"):
            tu = utt.filter(pl.col("meeting_id") == sel).sort("seg_id")
            for r in tu.iter_rows(named=True):
                st.markdown(f"<span style='color:#888'>#{r['seg_id']} "
                            f"[{r['speaker_role']}]</span> {r['text']}", unsafe_allow_html=True)
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

# ── 액션아이템 상태 관리 (진행상황 업데이트 루프) ──
st.write("")
with st.container(border=True):
    st.markdown('<div class="wtitle">✏️ 액션아이템 상태 관리</div>'
                '<div class="wsub">담당자가 진행상황을 갱신 → 추이/미완료 위젯에 반영. '
                '변경은 트래킹 레이어(action_status)에 저장되어 파이프라인 재실행에도 보존됩니다.</div>',
                unsafe_allow_html=True)

    STATUS_OPTS = ["open", "in_progress", "done", "blocked"]
    STATUS_KR = {"open": "⬜ 대기", "in_progress": "🔵 진행중",
                 "done": "✅ 완료", "blocked": "⛔ 지연/막힘"}

    st.caption("담당자는 AI 추출값을 기본으로 채우되, 비어 있거나(예: STT로 화자 미상) 틀리면 "
               "직접 수정할 수 있습니다. 수정값은 보존됩니다.")
    rows = i_view.sort(["meeting_id", "action_id"]).to_dicts()
    with st.form("status_form"):
        edits = {}
        h1, h2, h3, h4 = st.columns([2.6, 1.4, 1.1, 1.7])
        h1.caption("액션아이템"); h2.caption("담당자"); h3.caption("상태"); h4.caption("지연 사유")
        for r in rows:
            ck = r["action_key"]
            col1, col2, col3, col4 = st.columns([2.6, 1.4, 1.1, 1.7])
            col1.markdown(f"**{r['title']}**<br><span style='color:#888;font-size:12px'>"
                          f"{r['advertiser']} · 기한 {r['due'] or '-'}</span>",
                          unsafe_allow_html=True)
            owner = col2.text_input("담당자", value=r["owner_role"] or "",
                                    key=f"ow_{ck}", label_visibility="collapsed",
                                    placeholder="담당자 미정")
            cur = r["status"] if r["status"] in STATUS_OPTS else "open"
            new = col3.selectbox("상태", STATUS_OPTS, index=STATUS_OPTS.index(cur),
                                 format_func=lambda s: STATUS_KR[s],
                                 key=f"st_{ck}", label_visibility="collapsed")
            reason = col4.text_input("지연 사유", value=r.get("delay_reason") or "",
                                     key=f"rs_{ck}", label_visibility="collapsed",
                                     placeholder="지연 사유 (blocked일 때)")
            edits[ck] = (cur, new, reason, owner, r["owner_role"] or "")
        submitted = st.form_submit_button("💾 변경사항 저장")

    if submitted:
        wcon = db.connect()
        n = 0
        for ck, (cur, new, reason, owner, cur_owner) in edits.items():
            cur_reason = next((r.get("delay_reason") or "" for r in rows if r["action_key"] == ck), "")
            changed = (new != cur or (new == "blocked" and reason != cur_reason)
                       or owner.strip() != cur_owner.strip())
            if changed:
                db.update_status(wcon, ck, new, reason or None,
                                 owner=owner.strip(), by="dashboard")
                n += 1
        wcon.close()
        st.success(f"{n}건 저장했습니다." if n else "변경된 항목이 없습니다.")
        st.rerun()

st.caption(f"DB: {config.DB_PATH.name} · 회의 {meetings.height}건 · 액션아이템 {con_total}건")
