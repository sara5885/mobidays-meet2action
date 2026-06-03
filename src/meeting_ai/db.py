"""DuckDB 적재 레이어. 멱등성(idempotency)과 '추출/트래킹 분리'가 핵심.

레이어 분리:
  - action_items: AI 추출 결과. 파이프라인 재실행마다 delete-replace (멱등 재생성).
  - action_status: 사람이 관리하는 진행상황(open/in_progress/done/blocked + 지연사유).
    파이프라인이 절대 건드리지 않음 → 재실행해도 사람의 상태 변경이 보존된다.
  - status_history: 상태 변경 이력(감사 추적).

두 레이어는 안정 식별자 action_key(= meeting_id + 정규화 제목 해시)로 연결된다.
LLM 출력은 비결정적이라 제목이 미세하게 흔들릴 수 있어, meeting_id를 멱등 단위로
삼고 회의 단위로 추출을 통째 교체하되, 상태는 action_key로 따로 보존한다.
"""
from __future__ import annotations

import hashlib
import re

import duckdb

from . import config
from .schemas import ActionItem, Chunk, Utterance

VALID_STATUSES = ("open", "in_progress", "done", "blocked")

SCHEMA = """
CREATE TABLE IF NOT EXISTS meetings (
    meeting_id   VARCHAR PRIMARY KEY,
    title        VARCHAR,
    advertiser   VARCHAR,
    date         DATE,
    duration_sec INTEGER
);
CREATE TABLE IF NOT EXISTS utterances (
    meeting_id   VARCHAR,
    seg_id       INTEGER,
    speaker_code VARCHAR,
    speaker_role VARCHAR,
    start_sec    DOUBLE,
    end_sec      DOUBLE,
    text         VARCHAR,
    PRIMARY KEY (meeting_id, seg_id)
);
CREATE TABLE IF NOT EXISTS chunks (
    meeting_id VARCHAR,
    chunk_id   INTEGER,
    seg_ids    VARCHAR,
    text       VARCHAR,
    PRIMARY KEY (meeting_id, chunk_id)
);
-- 직원 마스터 (정규화: 직원 정보는 여기 한 곳에만)
-- is_adhoc: STT 미상 화자 등 임시로 생성된 행 표시 → 정식 직원과 구분(리포트에서 제외 가능)
CREATE TABLE IF NOT EXISTS employees (
    employee_id VARCHAR PRIMARY KEY,
    name        VARCHAR,
    role        VARCHAR,
    is_adhoc    BOOLEAN DEFAULT FALSE
);
-- 회의 참석자 (회의↔직원 다대다 연결, FK만 저장). 담당자 매핑의 '정답 후보'.
CREATE TABLE IF NOT EXISTS meeting_participants (
    meeting_id  VARCHAR,
    employee_id VARCHAR,
    PRIMARY KEY (meeting_id, employee_id)
);
-- 회의록 정리 (요약·안건·결정사항). 재실행마다 갱신.
CREATE TABLE IF NOT EXISTS meeting_summary (
    meeting_id VARCHAR PRIMARY KEY,
    summary    VARCHAR,
    agenda     VARCHAR,   -- JSON 배열 문자열
    decisions  VARCHAR    -- JSON 배열 문자열
);
-- 사람이 수정한 회의록(요약·안건·결정). 재실행에도 보존(파이프라인이 안 건드림).
CREATE TABLE IF NOT EXISTS meeting_summary_override (
    meeting_id VARCHAR PRIMARY KEY,
    summary    VARCHAR,
    agenda     VARCHAR,
    decisions  VARCHAR,
    updated_at TIMESTAMP
);
-- 추출 레이어 (재실행마다 갱신)
CREATE TABLE IF NOT EXISTS action_items (
    meeting_id    VARCHAR,
    action_id     INTEGER,
    action_key    VARCHAR,
    title         VARCHAR,
    owner_role    VARCHAR,
    due           VARCHAR,
    confidence    DOUBLE,
    source_seg_ids VARCHAR,
    source_quote  VARCHAR,
    PRIMARY KEY (meeting_id, action_id)
);
-- 트래킹 레이어 (사람 소유, 재실행에도 보존)
CREATE TABLE IF NOT EXISTS action_status (
    action_key     VARCHAR PRIMARY KEY,
    meeting_id     VARCHAR,
    status         VARCHAR DEFAULT 'open',
    delay_reason   VARCHAR,
    owner_override VARCHAR,   -- 사람이 직접 고친 담당자 (재실행에도 보존)
    title_override VARCHAR,   -- 사람이 직접 고친 액션아이템 제목 (재실행에도 보존)
    due_override   VARCHAR,   -- 사람이 직접 고친 기한(문자열) (재실행에도 보존)
    deleted        BOOLEAN DEFAULT FALSE,  -- 사람이 삭제(숨김) 표시. 재실행해도 유지
    updated_at     TIMESTAMP,
    updated_by     VARCHAR
);
-- 사람이 직접 추가한 액션아이템 (AI 추출과 별개라 재실행에도 보존)
CREATE TABLE IF NOT EXISTS manual_items (
    action_key  VARCHAR PRIMARY KEY,
    meeting_id  VARCHAR,
    title       VARCHAR,
    owner_role  VARCHAR,
    due         VARCHAR,
    created_at  TIMESTAMP,
    created_by  VARCHAR
);
CREATE SEQUENCE IF NOT EXISTS seq_status_history START 1;
CREATE TABLE IF NOT EXISTS status_history (
    id         INTEGER DEFAULT nextval('seq_status_history'),
    action_key VARCHAR,
    old_status VARCHAR,
    new_status VARCHAR,
    reason     VARCHAR,
    changed_at TIMESTAMP,
    changed_by VARCHAR
);
"""


def make_action_key(meeting_id: str, title: str) -> str:
    """제목 정규화 후 해시 → 재추출 시에도 같은 액션아이템을 같은 키로 식별."""
    norm = re.sub(r"\s+", "", title.lower())
    h = hashlib.md5(f"{meeting_id}|{norm}".encode("utf-8")).hexdigest()[:16]
    return h


# 직원 마스터 시드 (가상 광고대행사 조직도). 실서비스에선 HR 시스템 연동으로 대체.
SEED_EMPLOYEES = [
    ("E01", "지훈", "마케팅 팀장"),
    ("E02", "수아", "퍼포먼스 마케터"),
    ("E03", "채린", "콘텐츠 디자이너"),
    ("E04", "민준", "퍼포먼스 마케터"),
    ("E05", "서연", "콘텐츠 디자이너"),
    ("E06", "도윤", "데이터 분석가"),
    ("E07", "지아", "광고기획자(AE)"),
    ("E08", "현우", "미디어 플래너"),
    ("E09", "재현", "마케팅 팀장"),
    ("E10", "유나", "퍼포먼스 마케터"),
    ("E11", "태오", "콘텐츠 디자이너"),
]


def seed_employees(con) -> None:
    for eid, name, role in SEED_EMPLOYEES:
        con.execute(
            "INSERT INTO employees VALUES (?, ?, ?, FALSE) ON CONFLICT (employee_id) DO UPDATE "
            "SET name=excluded.name, role=excluded.role, is_adhoc=FALSE", [eid, name, role])


def connect() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(str(config.DB_PATH))
    con.execute(SCHEMA)
    # 기존 DB 호환: 컬럼이 없으면 추가 (스키마 진화)
    con.execute("ALTER TABLE action_status ADD COLUMN IF NOT EXISTS owner_override VARCHAR")
    con.execute("ALTER TABLE action_status ADD COLUMN IF NOT EXISTS title_override VARCHAR")
    con.execute("ALTER TABLE action_status ADD COLUMN IF NOT EXISTS deleted BOOLEAN DEFAULT FALSE")
    con.execute("ALTER TABLE action_status ADD COLUMN IF NOT EXISTS due_override VARCHAR")
    seed_employees(con)
    return con


def _replace(con, table: str, meeting_id: str) -> None:
    """멱등성: 같은 meeting_id를 지우고 새로 넣는다."""
    con.execute(f"DELETE FROM {table} WHERE meeting_id = ?", [meeting_id])


def upsert_meeting(con, meta: dict) -> None:
    _replace(con, "meetings", meta["meeting_id"])
    con.execute(
        "INSERT INTO meetings VALUES (?, ?, ?, ?, ?)",
        [meta["meeting_id"], meta.get("title", ""), meta.get("advertiser", ""),
         meta.get("date") or None, int(meta.get("duration_sec") or 0)],
    )


def _resolve_employee(con, name: str, role: str) -> str:
    """이름으로 직원 마스터에서 employee_id를 찾고, 없으면 ad-hoc 직원을 생성.
    (STT로 잡힌 '화자1' 등 명단에 없는 화자도 일관되게 식별)."""
    row = con.execute("SELECT employee_id FROM employees WHERE name=?", [name]).fetchone()
    if row:
        return row[0]
    eid = "AD" + hashlib.md5(name.encode("utf-8")).hexdigest()[:6]  # 이름 기준 결정적 id
    con.execute(
        "INSERT INTO employees VALUES (?, ?, ?, TRUE) ON CONFLICT (employee_id) DO NOTHING",
        [eid, name, role or "참석자"])  # is_adhoc=TRUE → 임시 화자로 표시(마스터 오염 방지)
    return eid


def upsert_participants(con, meeting_id: str, roster: list[dict]) -> None:
    """회의 참석자를 직원 FK로 연결 적재(멱등). roster: [{"name","role"}]."""
    _replace(con, "meeting_participants", meeting_id)
    seen = set()
    for p in roster or []:
        if not p:
            continue
        name = str(p.get("name") or p.get("role") or "참석자")
        role = str(p.get("role") or p.get("name") or "참석자")
        eid = _resolve_employee(con, name, role)
        if eid in seen:
            continue
        seen.add(eid)
        con.execute(
            "INSERT INTO meeting_participants VALUES (?, ?) ON CONFLICT DO NOTHING",
            [meeting_id, eid])


def get_participants(con, meeting_id: str) -> list[dict]:
    """회의↔직원 조인으로 명단 복원(직원id·이름·역할)."""
    rows = con.execute(
        "SELECT e.employee_id, e.name, e.role FROM meeting_participants mp "
        "JOIN employees e USING (employee_id) WHERE mp.meeting_id=? ORDER BY e.name",
        [meeting_id]).fetchall()
    return [{"employee_id": i, "name": n, "role": r} for i, n, r in rows]


def upsert_summary(con, meeting_id: str, summary) -> None:
    """회의록(요약·안건·결정) 멱등 적재. summary: MeetingSummary."""
    import json as _json
    _replace(con, "meeting_summary", meeting_id)
    con.execute(
        "INSERT INTO meeting_summary VALUES (?, ?, ?, ?)",
        [meeting_id, summary.summary,
         _json.dumps(summary.agenda, ensure_ascii=False),
         _json.dumps(summary.decisions, ensure_ascii=False)])


def get_summary(con, meeting_id: str) -> dict:
    """회의록 반환. 사람이 수정한 override가 있으면 그 필드를 우선."""
    import json as _json
    base = con.execute(
        "SELECT summary, agenda, decisions FROM meeting_summary WHERE meeting_id=?",
        [meeting_id]).fetchone() or (None, None, None)
    ov = con.execute(
        "SELECT summary, agenda, decisions FROM meeting_summary_override WHERE meeting_id=?",
        [meeting_id]).fetchone() or (None, None, None)
    summ = ov[0] if ov[0] is not None else (base[0] or "")
    agenda = _json.loads(ov[1]) if ov[1] is not None else _json.loads(base[1] or "[]")
    decisions = _json.loads(ov[2]) if ov[2] is not None else _json.loads(base[2] or "[]")
    return {"summary": summ, "agenda": agenda, "decisions": decisions,
            "edited": ov[0] is not None or ov[1] is not None or ov[2] is not None}


def save_summary_override(con, meeting_id: str, summary: str,
                          agenda: list[str], decisions: list[str]) -> None:
    """사람이 수정한 회의록 저장(override). 재실행에도 보존."""
    import json as _json
    con.execute(
        "INSERT INTO meeting_summary_override VALUES (?, ?, ?, ?, now()) "
        "ON CONFLICT (meeting_id) DO UPDATE SET summary=excluded.summary, "
        "agenda=excluded.agenda, decisions=excluded.decisions, updated_at=now()",
        [meeting_id, summary,
         _json.dumps(agenda, ensure_ascii=False), _json.dumps(decisions, ensure_ascii=False)])


def upsert_utterances(con, meeting_id: str, items: list[Utterance]) -> None:
    _replace(con, "utterances", meeting_id)
    if not items:
        return
    con.executemany(
        "INSERT INTO utterances VALUES (?, ?, ?, ?, ?, ?, ?)",
        [[u.meeting_id, u.seg_id, u.speaker_code, u.speaker_role,
          u.start, u.end, u.text] for u in items],
    )


def upsert_chunks(con, meeting_id: str, items: list[Chunk]) -> None:
    _replace(con, "chunks", meeting_id)
    if not items:
        return
    con.executemany(
        "INSERT INTO chunks VALUES (?, ?, ?, ?)",
        [[c.meeting_id, c.chunk_id, ",".join(map(str, c.seg_ids)), c.text]
         for c in items],
    )


def upsert_action_items(con, meeting_id: str, items: list[ActionItem]) -> None:
    """추출 결과를 멱등 교체하고, 신규 action_key에 대해서만 상태행을 초기화한다.
    기존 action_status(사람 수정분)는 보존된다."""
    _replace(con, "action_items", meeting_id)
    rows = []
    keys = []
    for idx, a in enumerate(items):
        key = make_action_key(meeting_id, a.title)
        keys.append((key, a.status.value))
        rows.append([
            a.meeting_id, idx, key, a.title, a.owner_role, a.due,
            a.confidence, ",".join(map(str, a.source_seg_ids)), a.source_quote,
        ])
    if rows:
        con.executemany(
            "INSERT INTO action_items VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", rows
        )
    # 신규 액션아이템만 상태행 생성 (기존 사람 수정분은 건드리지 않음 → 보존)
    for key, init_status in keys:
        exists = con.execute(
            "SELECT 1 FROM action_status WHERE action_key = ?", [key]).fetchone()
        if not exists:
            con.execute(
                "INSERT INTO action_status (action_key, meeting_id, status, updated_at, updated_by)"
                " VALUES (?, ?, ?, now(), 'pipeline')",
                [key, meeting_id, init_status])


_KEEP = object()  # owner 인자 미지정 표시 (None=담당자 비우기와 구분)


def update_status(con, action_key: str, new_status: str, reason: str | None = None,
                  owner=_KEEP, title=_KEEP, due=_KEEP, by: str = "dashboard") -> None:
    """상태 변경 + (선택)담당자/제목/기한 override + 이력 기록. blocked일 때만 delay_reason 저장.

    owner/title/due: 미지정(_KEEP)이면 기존값 유지, 문자열이면 덮어씀, ''/None이면 AI값으로 되돌림.
    """
    if new_status not in VALID_STATUSES:
        raise ValueError(f"허용되지 않는 상태: {new_status}")
    row = con.execute(
        "SELECT status, owner_override, title_override, due_override FROM action_status WHERE action_key=?",
        [action_key]).fetchone()
    old = row[0] if row else None
    reason = reason if new_status == "blocked" else None

    def _resolve(val, cur):
        if val is _KEEP:
            return cur
        return None if val in (None, "") else str(val).strip()

    owner_v = _resolve(owner, row[1] if row else None)
    title_v = _resolve(title, row[2] if row else None)
    due_v = _resolve(due, row[3] if row else None)

    con.execute(
        "INSERT INTO action_status "
        "(action_key, meeting_id, status, delay_reason, owner_override, title_override, due_override, updated_at, updated_by) "
        "VALUES (?, NULL, ?, ?, ?, ?, ?, now(), ?) "
        "ON CONFLICT (action_key) DO UPDATE SET status=excluded.status, "
        "delay_reason=excluded.delay_reason, owner_override=excluded.owner_override, "
        "title_override=excluded.title_override, due_override=excluded.due_override, "
        "updated_at=now(), updated_by=excluded.updated_by",
        [action_key, new_status, reason, owner_v, title_v, due_v, by])
    con.execute(
        "INSERT INTO status_history (action_key, old_status, new_status, reason, changed_at, changed_by)"
        " VALUES (?, ?, ?, ?, now(), ?)", [action_key, old, new_status, reason, by])


def set_deleted(con, action_key: str, deleted: bool = True, by: str = "dashboard") -> None:
    """액션아이템 삭제(숨김) 표시. 진짜 지우지 않아 재실행에도 유지."""
    con.execute(
        "INSERT INTO action_status (action_key, status, deleted, updated_at, updated_by) "
        "VALUES (?, 'open', ?, now(), ?) "
        "ON CONFLICT (action_key) DO UPDATE SET deleted=excluded.deleted, "
        "updated_at=now(), updated_by=excluded.updated_by",
        [action_key, deleted, by])


def add_manual_item(con, meeting_id: str, title: str, owner_role: str | None,
                    due: str | None, by: str = "dashboard") -> str:
    """사람이 액션아이템 직접 추가. action_key='manual-<uuid>'. 재실행에도 보존."""
    import uuid
    key = "manual-" + uuid.uuid4().hex[:12]
    con.execute(
        "INSERT INTO manual_items VALUES (?, ?, ?, ?, ?, now(), ?)",
        [key, meeting_id, title.strip(), (owner_role or None), (due or None), by])
    # 트래킹 상태행도 생성(기본 open)
    con.execute(
        "INSERT INTO action_status (action_key, meeting_id, status, updated_at, updated_by) "
        "VALUES (?, ?, 'open', now(), ?) ON CONFLICT (action_key) DO NOTHING",
        [key, meeting_id, by])
    return key


def get_manual_items(con) -> list[dict]:
    rows = con.execute(
        "SELECT action_key, meeting_id, title, owner_role, due FROM manual_items").fetchall()
    return [{"action_key": k, "meeting_id": m, "title": t, "owner_role": o, "due": d}
            for k, m, t, o, d in rows]
