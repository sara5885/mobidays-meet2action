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

from meeting_ai import config

TODAY = dt.date(2026, 6, 1)  # 데모 기준일 (env 고정으로 재현성 확보)


def _statuses(n: int, weeks_ago: float) -> list[str]:
    """경과 주차에 따라 n개 액션아이템의 상태 배분."""
    if weeks_ago >= 3:
        done_ratio, prog_ratio = 1.0, 0.0
    elif weeks_ago >= 2:
        done_ratio, prog_ratio = 0.6, 0.2
    elif weeks_ago >= 1:
        done_ratio, prog_ratio = 0.3, 0.3
    else:
        done_ratio, prog_ratio = 0.0, 0.1
    n_done = round(n * done_ratio)
    n_prog = round(n * prog_ratio)
    out = ["done"] * n_done + ["in_progress"] * n_prog
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
        ids = [r[0] for r in con.execute(
            "SELECT action_id FROM action_items WHERE meeting_id=? ORDER BY action_id",
            [mid]).fetchall()]
        statuses = _statuses(len(ids), weeks_ago)
        for aid, sts in zip(ids, statuses):
            con.execute(
                "UPDATE action_items SET status=? WHERE meeting_id=? AND action_id=?",
                [sts, mid, aid])
        total += len(ids)
    con.close()
    print(f"✅ 진행상황 시뮬레이션 완료: {len(meetings)}개 회의, {total}개 액션아이템 상태 갱신")


if __name__ == "__main__":
    main()
