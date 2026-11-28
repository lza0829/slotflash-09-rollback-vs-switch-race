import os
import sys
import tempfile
from pathlib import Path

import pytest

# 必须在 import app.* 之前钉住库路径：seed_if_empty() 在 import app.main 时就会跑。
_TMP = Path(tempfile.mkdtemp(prefix="slotflash-test-"))
os.environ["SLOTFLASH_DB"] = str(_TMP / "test.db")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from app import seed  # noqa: E402
from app.config import DB_PATH  # noqa: E402
from app.main import app  # noqa: E402


def _drop_db() -> None:
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(DB_PATH) + suffix)
        if p.exists():
            p.unlink()


@pytest.fixture()
def client():
    _drop_db()
    seed.seed_if_empty()
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def sql():
    """直连测试库，用于摆出半截态 / 快进争用窗。"""
    from app.db import connect

    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


def expire_windows(board_id: int | None = None) -> None:
    """模拟时间流逝：把开着的争用窗推到过去，下一笔即另开新窗。"""
    from app.db import connect

    sql = "UPDATE arbitration_windows SET opened_epoch = opened_epoch - 86400 WHERE state='开'"
    args: tuple = ()
    if board_id is not None:
        sql += " AND board_id=?"
        args = (board_id,)
    conn = connect()
    try:
        conn.execute(sql, args)
    finally:
        conn.close()


def snapshot(client) -> dict:
    r = client.get("/api/校验")
    assert r.status_code == 200, r.text
    return r.json()


def board_replay(client, board_id: int) -> dict:
    r = client.get(f"/api/启动链/{board_id}/重放")
    assert r.status_code == 200, r.text
    return r.json()


def boot_row(client, board_id: int) -> dict:
    rows = client.get("/api/启动链").json()
    return next(r for r in rows if r["board_id"] == board_id)


def assert_consistent(client, board_id: int) -> dict:
    """互证：现运行格、硬退争用桶、切槽回执、硬退写口四件必须同口径。"""
    rep = board_replay(client, board_id)
    assert rep["可重放"], rep
    assert rep["单现运行"], rep
    assert rep["桶不分叉"], rep
    assert rep["回执相符"], rep
    assert rep["自洽"], rep
    assert rep["现运行格"] == [rep["板现运行"]], rep
    bucket = rep["最新桶"]
    if bucket:
        assert bucket["switch_success"] + bucket["rollback_success"] <= 1, rep
    snap = snapshot(client)
    assert snap["自洽"], snap
    return rep
