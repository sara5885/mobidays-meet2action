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
# AI 추출 액션아이템 (삭제 표시된 것 제외) + 사람이 수동 추가한 액션아이템
ai_items = con.execute("""
    SELECT a.meeting_id, a.action_id, a.action_key,
           COALESCE(s.due_override, a.due) AS due, a.due AS due_ai,
           a.confidence, a.source_quote, FALSE AS manual,
           COALESCE(s.title_override, a.title) AS title, a.title AS title_ai,
           COALESCE(s.owner_override, a.owner_role) AS owner_role, a.owner_role AS owner_ai,
           COALESCE(s.status, 'open') AS status, s.delay_reason
    FROM action_items a LEFT JOIN action_status s USING (action_key)
    WHERE NOT COALESCE(s.deleted, FALSE)
""").pl()
man_items = con.execute("""
    SELECT m.meeting_id, 100000 AS action_id, m.action_key,
           COALESCE(s.due_override, m.due) AS due, m.due AS due_ai,
           1.0 AS confidence, '(수동 추가)' AS source_quote, TRUE AS manual,
           COALESCE(s.title_override, m.title) AS title, m.title AS title_ai,
           COALESCE(s.owner_override, m.owner_role) AS owner_role, m.owner_role AS owner_ai,
           COALESCE(s.status, 'open') AS status, s.delay_reason
    FROM manual_items m LEFT JOIN action_status s USING (action_key)
    WHERE NOT COALESCE(s.deleted, FALSE)
""").pl()
items = pl.concat([ai_items, man_items], how="vertical_relaxed") if man_items.height else ai_items
utt = con.execute(
    "SELECT meeting_id, seg_id, speaker_role, text FROM utterances ORDER BY meeting_id, seg_id").pl()
con_total = con.execute("SELECT COUNT(*) FROM action_items").fetchone()[0]
# 직원 마스터 (담당자 선택 드롭다운용, 임시 화자 제외)
employees = [{"employee_id": i, "name": n, "role": r} for i, n, r in con.execute(
    "SELECT employee_id, name, role FROM employees WHERE is_adhoc=FALSE ORDER BY employee_id"
).fetchall()]
emp_by_id = {e["employee_id"]: e for e in employees}
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

# 상태 표기 (전역 — 여러 위젯이 공유)
STATUS_OPTS = ["open", "in_progress", "done", "blocked"]
STATUS_KR = {"open": "⬜ 대기", "in_progress": "🔵 진행중", "done": "✅ 완료", "blocked": "⛔ 지연/막힘"}

# 담당자(owner) 표시: 역할은 여러 명이 맡을 수 있으므로 '이름(직원id)·역할'로 해석.
# (회의별 참석자 명단에서 role→직원 매핑. 같은 역할 다수면 이름들 나열, 매칭 없으면 역할 그대로)
def _owner_display(meeting_id: str, owner: str) -> str:
    if not owner:
        return "담당자 미정"
    # 사람이 드롭다운으로 고른 직원 id면 직접 해석
    if owner in emp_by_id:
        e = emp_by_id[owner]
        return f"{e['name']}({e['employee_id']}) · {e['role']}"
    # AI가 뽑은 역할 문자열이면 회의 참석자 중 그 역할자로 해석
    matched = [p for p in participants.get(meeting_id, []) if p["role"] == owner]
    if not matched:
        return owner  # 자유 텍스트 등 → 그대로
    who = ", ".join(f"{p['name']}({p['employee_id']})" for p in matched)
    return f"{who} · {owner}"

items = items.with_columns(
    pl.struct(["meeting_id", "owner_role"])
      .map_elements(lambda s: _owner_display(s["meeting_id"], s["owner_role"]),
                    return_dtype=pl.String).alias("owner_disp"))

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


# ── KPI ── (진척 중심: 완료/미완료/완료율. 주 단위 델타는 단발 데이터엔 부자연스러워 제거)
total_ai = i_view.height
done_cnt = i_view.filter(pl.col("status") == "done").height
open_cnt = total_ai - done_cnt
rate = round(100 * done_cnt / total_ai) if total_ai else 0


def kpi(col, label, value, sub=""):
    sub_html = f'<div class="delta-flat">{sub}</div>' if sub else ""
    col.markdown(f'<div class="kpi"><div class="label">{label}</div>'
                 f'<div class="value">{value}</div>{sub_html}</div>',
                 unsafe_allow_html=True)


k1, k2, k3 = st.columns(3)
kpi(k1, "회의 수", m_view.height)
kpi(k2, "액션아이템", total_ai)
kpi(k3, "진행 (완료/전체)", f"{done_cnt}/{total_ai}", f"완료율 {rate}%")
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

        edited_badge = " <span style='color:#16a34a;font-size:12px'>✎ 수정됨</span>" if summ.get("edited") else ""
        st.markdown(f"### {mrow['title'] or sel}{edited_badge}", unsafe_allow_html=True)
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

        # 회의록 직접 수정 (override 보존)
        with st.expander("✏️ 회의록 수정"):
            e_sum = st.text_area("요약", value=summ["summary"], key=f"es_{sel}", height=80)
            e_ag = st.text_area("안건 (한 줄에 하나)", value="\n".join(summ["agenda"]),
                                key=f"ea_{sel}", height=100)
            e_de = st.text_area("결정 사항 (한 줄에 하나)", value="\n".join(summ["decisions"]),
                                key=f"ed_{sel}", height=100)
            if st.button("💾 회의록 저장", key=f"esave_{sel}"):
                wcon = db.connect()
                db.save_summary_override(
                    wcon, sel, e_sum.strip(),
                    [x.strip() for x in e_ag.splitlines() if x.strip()],
                    [x.strip() for x in e_de.splitlines() if x.strip()])
                wcon.close()
                st.success("회의록을 저장했습니다.")
                st.rerun()

        st.markdown("**④ 액션 아이템 (담당자 / 기한 / 할 일)**")
        ai = i_view.filter(pl.col("meeting_id") == sel).select(
            ["owner_disp", "due", "title", "status", "confidence"]).rename(
            {"owner_disp": "담당자", "due": "기한", "title": "할 일",
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
        st.markdown('<div class="wtitle">주차별 회의 & 액션아이템 추이</div>'
                    f'<div class="wsub">최근 {TREND_WEEKS}주 · 막대=발생 건수, 선=완료율</div>',
                    unsafe_allow_html=True)
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
        st.markdown('<div class="wtitle">담당자별 미완료 Top N</div>', unsafe_allow_html=True)
        open_items = i_view.filter(pl.col("status") != "done")
        by_owner = (open_items.with_columns(pl.col("owner_disp").fill_null("담당자 미정"))
                    .group_by("owner_disp").agg(pl.len().alias("cnt"))
                    .sort("cnt", descending=True))
        if by_owner.height:
            p = by_owner.to_pandas()
            colors = ["#dc2626", "#ea580c", "#f59e0b", "#3b82f6", "#22a06b", "#9ca3af"]
            xmax = int(p["cnt"].max())
            # 라벨 2줄: "이름(id)" 윗줄, "역할" 아랫줄
            ylab = [s.replace(" · ", "<br>") for s in p["owner_disp"]]
            fig = go.Figure(go.Bar(x=p["cnt"], y=ylab, orientation="h",
                                   marker_color=colors[:len(p)], width=0.6,  # 막대 두께 고정
                                   text=[f"{v}건" for v in p["cnt"]], textposition="outside"))
            # 담당자 수와 무관하게 행 높이 일정(통일성). x축도 정수 눈금 고정.
            fig.update_layout(height=70 * len(p) + 70, margin=dict(t=10, b=10, l=10, r=30),
                              yaxis=dict(autorange="reversed"), plot_bgcolor="white",
                              bargap=0.4,
                              xaxis=dict(title="미완료 건수", rangemode="tozero",
                                         range=[0, xmax + 1], dtick=1))
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.success("미완료 항목이 없습니다.")

# ── 위젯 3: 반복 키워드 (전체 폭 1-column) ──
if True:
    with st.container(border=True):
        st.markdown('<div class="wtitle">반복 이슈 키워드</div>', unsafe_allow_html=True)
        docs = {r["meeting_id"]: r["text"]
                for r in u_view.group_by("meeting_id").agg(
                    pl.col("text").str.join(" ").alias("text")).iter_rows(named=True)}
        kws = top_keywords(docs, top_n=14)
        if kws:
            # 중요도(score) 높을수록 글자 크게 + 진하게. 순서도 점수순.
            smax = max(k["score"] for k in kws) or 1
            chips = []
            for k in kws:
                sz = 13 + round((k["score"] / smax) * 13)   # 13~26px
                shade = 900 if k["score"] / smax > 0.66 else (800 if k["score"] / smax > 0.33 else 600)
                chips.append(
                    f'<span style="display:inline-block;margin:3px 7px;font-size:{sz}px;'
                    f'font-weight:500;color:var(--ink,#1f2430);opacity:{0.55 + 0.45*k["score"]/smax:.2f}">'
                    f'{k["keyword"]}</span>')
            st.markdown("<div style='line-height:2.1'>" + "".join(chips) + "</div>",
                        unsafe_allow_html=True)
        else:
            st.info("키워드를 추출할 발화가 없습니다.")

# ── 위젯 4: Confidence 분포(좌) + 검수 대상 안내(우, 진단만) ──
if True:
    with st.container(border=True):
        avg_conf = i_view["confidence"].mean() if i_view.height else 0
        st.markdown('<div class="wtitle">LLM Confidence 분포</div>'
                    f'<div class="wsub">평균 {avg_conf:.2f} · 신뢰도 낮은 항목은 아래 ‘상태 관리’에서 검수·수정</div>',
                    unsafe_allow_html=True)
        gL, gR = st.columns([1.1, 1])
        with gL:
            bands = [("0.9 이상", 0.9, 1.01, "#22a06b"), ("0.7–0.9", 0.7, 0.9, "#84cc16"),
                     ("0.5–0.7", 0.5, 0.7, "#f59e0b"), ("0.5 미만", 0.0, 0.5, "#dc2626")]
            total = i_view.height or 1
            labels, vals, cols = [], [], []
            for name, lo, hi, color in bands:
                c = i_view.filter((pl.col("confidence") >= lo) & (pl.col("confidence") < hi)).height
                labels.append(name); vals.append(round(100 * c / total)); cols.append(color)
            fig = go.Figure(go.Bar(x=vals, y=labels, orientation="h", marker_color=cols, width=0.6,
                                   text=[f"{v}%" for v in vals], textposition="outside"))
            fig.update_layout(height=230, margin=dict(t=6, b=6, l=10, r=30),
                              yaxis=dict(autorange="reversed"), plot_bgcolor="white",
                              xaxis=dict(range=[0, 100]))
            st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
        with gR:
            # 진단만: 어느 회의·어느 액션인지 안내 (수정은 상태관리에서)
            low = (i_view.filter(pl.col("confidence") < 0.6)
                   .with_columns(pl.col("meeting_id").replace_strict(adv_map, default="(미상)").alias("adv"))
                   .sort("confidence"))
            st.markdown(f"**검수 권장 · {low.height}건** <span style='color:#888;font-size:12px'>"
                        f"(아래 상태 관리에서 처리)</span>", unsafe_allow_html=True)
            if low.height:
                box = st.container(height=190)
                for row in low.iter_rows(named=True):
                    box.markdown(
                        f'<div class="quote"><span>{row["title"]}<br>'
                        f'<span style="color:#888;font-size:12px">{row["adv"]}</span></span>'
                        f'<span class="conf-tag">{row["confidence"]:.2f}</span></div>',
                        unsafe_allow_html=True)
            else:
                st.success("검수가 필요한 항목이 없습니다.")

# ── 액션아이템 상태 관리 (진행상황 업데이트 루프) ──
st.write("")
with st.container(border=True):
    st.markdown('<div class="wtitle">✏️ 액션아이템 상태 관리</div>', unsafe_allow_html=True)

    STATUS_OPTS = ["open", "in_progress", "done", "blocked"]
    STATUS_KR = {"open": "⬜ 대기", "in_progress": "🔵 진행중",
                 "done": "✅ 완료", "blocked": "⛔ 지연/막힘"}
    NONE = "__none__"
    OWNER_OPTS = [NONE] + [e["employee_id"] for e in employees]

    def _owner_label(eid):
        if eid == NONE:
            return "담당자 미정"
        e = emp_by_id[eid]
        return f"{e['name']} · {e['role']} ({eid})"

    def _cur_empid(meeting_id, owner):
        # 현재 owner(직원id/역할/자유텍스트)를 드롭다운 기본값(직원id)으로 환산
        if owner in emp_by_id:
            return owner
        for p in participants.get(meeting_id, []):
            if p["role"] == owner:
                return p["employee_id"]
        return NONE

    rows = i_view.to_dicts()
    st.caption("담당자·제목·상태를 수정하고 저장하세요. 🗑로 잘못된 항목을 삭제, ➕로 직접 추가할 수 있습니다 "
               "(추가·삭제·수정 모두 재실행에도 보존).")

    def _render_item(r):
        ck = r["action_key"]
        # row 위 왼쪽: 이 액션아이템(행 전체)의 신뢰도 — 낮으면 빨강
        if r["manual"]:
            st.markdown("<span style='color:#aaa;font-size:11px'>✍ 수동 추가 항목</span>",
                        unsafe_allow_html=True)
        else:
            low_conf = r["confidence"] < 0.6
            color, warn = ("#dc2626", " ⚠ 검수 권장") if low_conf else ("#aaa", "")
            st.markdown(f"<span style='color:{color};font-size:11px'>신뢰도 {r['confidence']:.2f}{warn}</span>",
                        unsafe_allow_html=True)
        c1, c2, c3, c4, c5 = st.columns([1.6, 2.6, 1.0, 1.1, 0.4])
        cur_eid = _cur_empid(r["meeting_id"], r["owner_role"])
        c1.selectbox("담당자", OWNER_OPTS,
                     index=OWNER_OPTS.index(cur_eid) if cur_eid in OWNER_OPTS else 0,
                     format_func=_owner_label, key=f"ow_{ck}", label_visibility="collapsed")
        c2.text_input("할 일", value=r["title"], key=f"ti_{ck}", label_visibility="collapsed")
        c3.text_input("기한", value=r["due"] or "", key=f"du_{ck}",
                      label_visibility="collapsed", placeholder="기한")
        cur = r["status"] if r["status"] in STATUS_OPTS else "open"
        new = c4.selectbox("상태", STATUS_OPTS, index=STATUS_OPTS.index(cur),
                           format_func=lambda s: STATUS_KR[s],
                           key=f"st_{ck}", label_visibility="collapsed")
        if c5.button("🗑", key=f"del_{ck}", help="이 액션아이템 삭제"):
            wcon = db.connect(); db.set_deleted(wcon, ck, True); wcon.close()
            st.rerun()
        # 지연(blocked)일 때만 사유칸 — '할 일' 열 아래에 라벨과 함께 (빈 액션처럼 안 보이게)
        if new == "blocked":
            rc1, rc2 = st.columns([1.6, 4.5])
            rc1.markdown("<span style='color:#dc2626;font-size:12px'>⛔ 지연 사유</span>",
                         unsafe_allow_html=True)
            rc2.text_input("지연 사유", value=r.get("delay_reason") or "",
                           key=f"rs_{ck}", label_visibility="collapsed", placeholder="예: 광고주 컨펌 지연")
        st.markdown("<div style='margin-bottom:6px'></div>", unsafe_allow_html=True)

    # 광고주 필터(i_view)에 해당하는 회의만 표시. 회의별 묶음 + 추가 폼
    for mid in m_view.sort("date")["meeting_id"].to_list():
        mrows = [r for r in rows if r["meeting_id"] == mid]
        mr = m_view.filter(pl.col("meeting_id") == mid).to_dicts()[0]
        st.markdown(f"##### 🗂 {mr['advertiser'] or mid} · {mr['date']}　"
                    f"<span style='color:#888;font-size:13px'>{mr['title'] or ''}</span>",
                    unsafe_allow_html=True)
        # 미정(맨 아래) 정렬: owner 환산 후 정렬
        for r in sorted(mrows, key=lambda x: (_cur_empid(mid, x["owner_role"]) == NONE,
                                              _owner_label(_cur_empid(mid, x["owner_role"])),
                                              x["action_id"])):
            _render_item(r)
        with st.expander("➕ 액션아이템 추가"):
            a1, a2, a3 = st.columns([1.7, 3.0, 1.2])
            add_owner = a1.selectbox("담당자", OWNER_OPTS, format_func=_owner_label,
                                     key=f"add_ow_{mid}", label_visibility="collapsed")
            add_title = a2.text_input("할 일", key=f"add_ti_{mid}", label_visibility="collapsed",
                                      placeholder="새 액션아이템")
            add_due = a3.text_input("기한", key=f"add_due_{mid}", label_visibility="collapsed",
                                    placeholder="기한")
            if st.button("추가", key=f"add_btn_{mid}"):
                if add_title.strip():
                    wcon = db.connect()
                    db.add_manual_item(wcon, mid, add_title,
                                       None if add_owner == NONE else add_owner, add_due)
                    wcon.close()
                    st.rerun()
                else:
                    st.warning("할 일을 입력하세요.")
        st.markdown("---")

    if st.button("💾 변경사항 저장"):
        wcon = db.connect()
        n = 0
        for r in rows:
            ck = r["action_key"]
            new = st.session_state.get(f"st_{ck}", r["status"])
            reason = st.session_state.get(f"rs_{ck}", "") if new == "blocked" else ""
            title_edit = st.session_state.get(f"ti_{ck}", r["title"]).strip()
            title_val = "" if title_edit == r["title_ai"] else title_edit
            due_edit = st.session_state.get(f"du_{ck}", r["due"] or "").strip()
            due_val = "" if due_edit == (r["due_ai"] or "") else due_edit
            owner_sel = st.session_state.get(f"ow_{ck}", NONE)
            owner_val = "" if owner_sel == NONE else owner_sel
            cur_owner_eid = _cur_empid(r["meeting_id"], r["owner_role"])
            changed = (new != r["status"] or title_edit != r["title"]
                       or due_edit != (r["due"] or "")
                       or owner_sel != cur_owner_eid
                       or (new == "blocked" and reason != (r.get("delay_reason") or "")))
            if changed:
                db.update_status(wcon, ck, new, reason or None,
                                 owner=owner_val, title=title_val, due=due_val, by="dashboard")
                n += 1
        wcon.close()
        st.success(f"{n}건 저장했습니다." if n else "변경된 항목이 없습니다.")
        st.rerun()

st.caption(f"DB: {config.DB_PATH.name} · 회의 {meetings.height}건 · 액션아이템 {con_total}건")
