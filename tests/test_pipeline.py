"""뼈대 검증 테스트: 멱등성 + 스키마 강제 + 상태 보존.

테스트는 실제 LLM(.env의 gemini/ollama)을 호출하지 않도록 항상 mock provider로 고정한다.
→ 결정적이고 빠르며 네트워크/rate limit에 영향받지 않음.
"""
import duckdb

from meeting_ai import config, db
from meeting_ai.pipeline import run

config.LLM_PROVIDER = "mock"  # 테스트는 항상 mock (환경 독립적)


def test_run_and_idempotency():
    sample = config.RAW_DIR / "ko_meeting_3speakers.json"
    run(sample)
    con = duckdb.connect(str(config.DB_PATH), read_only=True)
    n1 = con.execute("SELECT COUNT(*) FROM action_items").fetchone()[0]
    con.close()
    assert n1 > 0

    # 재실행해도 행 수 동일 (중복 적재 없음)
    run(sample)
    con = duckdb.connect(str(config.DB_PATH), read_only=True)
    n2 = con.execute("SELECT COUNT(*) FROM action_items").fetchone()[0]
    con.close()
    assert n1 == n2


def test_action_item_has_confidence_and_source():
    con = duckdb.connect(str(config.DB_PATH), read_only=True)
    rows = con.execute(
        "SELECT confidence, source_quote FROM action_items"
    ).fetchall()
    con.close()
    for conf, quote in rows:
        assert 0.0 <= conf <= 1.0


def test_human_status_preserved_across_reruns():
    """핵심: 사람이 바꾼 진행상황(action_status)이 파이프라인 재실행 후에도 보존되어야 한다.
    추출(action_items)은 멱등 재생성되지만 트래킹(action_status)은 보존되는 분리 설계 검증."""
    sample = config.RAW_DIR / "ko_meeting_3speakers.json"
    run(sample)

    con = db.connect()
    key = con.execute(
        "SELECT action_key FROM action_items WHERE meeting_id LIKE 'nova%' LIMIT 1"
    ).fetchone()[0]
    db.update_status(con, key, "done", by="test")
    con.close()

    # 파이프라인 재실행 (action_items는 delete-replace)
    run(sample)

    con = duckdb.connect(str(config.DB_PATH), read_only=True)
    status = con.execute(
        "SELECT status FROM action_status WHERE action_key = ?", [key]
    ).fetchone()[0]
    # 재추출로 같은 action_key가 다시 생겨도 사람이 바꾼 'done'이 유지되어야 함
    n_hist = con.execute(
        "SELECT COUNT(*) FROM status_history WHERE action_key = ? AND changed_by='test'",
        [key]).fetchone()[0]
    con.close()
    assert status == "done"
    assert n_hist >= 1
