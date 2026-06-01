"""액션아이템 진행상황 업데이트 루프 (mock).

실제로는 Slack/노션 트래킹 시스템에서 담당자가 상태를 바꾸겠지만, PoC에서는
회의 경과 시간 기준으로 상태를 결정적으로 시뮬레이션한다.
  - 오래된 회의일수록 완료율이 높다 (done 비율 ↑)
  - 최근 회의는 대부분 open
→ 대시보드의 '완료율 추이'가 의미를 갖게 한다.

멱등: 같은 기준일에 대해 항상 같은 상태를 부여한다. (파이프라인 재적재 후 다시 실행)
실행: PYTHONPATH=src python scripts/simulate_progress.py
"""
from __future__ import annotations

import datetime as dt

import duckdb

from meeting_ai import config, db

TODAY = dt.date(2026, 6, 1)  # 데모 기준일 (env 고정으로 재현성 확보)


def _statuses(n: int, weeks_ago: float) -> list[str]:
    """경과 주차에 따라 n개 액션아이템의 상태 배분 (done/in_progress/blocked/open)."""
    if weeks_ago >= 3:
        done_r, prog_r, block_r = 1.0, 0.0, 0.0
    elif weeks_ago >= 2:
        done_r, prog_r, block_r = 0.5, 0.25, 0.25  # 일부 지연(blocked)
    elif weeks_ago >= 1:
        done_r, prog_r, block_r = 0.25, 0.5, 0.0
    else:
        done_r, prog_r, block_r = 0.0, 0.1, 0.0
    n_done, n_prog, n_block = round(n * done_r), round(n * prog_r), round(n * block_r)
    out = ["done"] * n_done + ["in_progress"] * n_prog + ["blocked"] * n_block
    out += ["open"] * (n - len(out))
    return out[:n]


def main() -> None:
    con = duckdb.connect(str(config.DB_PATH))
    meetings = con.execute("SELECT meeting_id, date FROM meetings").fetchall()
    total = 0
    for mid, date in meetings:
        if date is None:
            continue
        weeks_ago = (TODAY - date).days / 7
        keys = [r[0] for r in con.execute(
            "SELECT action_key FROM action_items WHERE meeting_id=? ORDER BY action_id",
            [mid]).fetchall()]
        statuses = _statuses(len(keys), weeks_ago)
        # 트래킹 레이어(action_status)에 기록. 가산점 '진행상황 업데이트 루프' mock.
        for key, sts in zip(keys, statuses):
            reason = "광고주 컨펌 지연" if sts == "blocked" else None
            db.update_status(con, key, sts, reason, by="progress-sim")
        total += len(keys)
    con.close()
    print(f"✅ 진행상황 시뮬레이션 완료: {len(meetings)}개 회의, {total}개 액션아이템 상태 갱신")


if __name__ == "__main__":
    main()
