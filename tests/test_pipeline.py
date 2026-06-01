"""뼈대 검증 테스트: 멱등성 + 스키마 강제."""
import duckdb

from meeting_ai import config, db
from meeting_ai.pipeline import run


def test_run_and_idempotency():
    sample = config.RAW_DIR / "sample_transcript.json"
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
