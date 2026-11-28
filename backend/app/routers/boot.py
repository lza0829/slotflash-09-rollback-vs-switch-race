from __future__ import annotations
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from app.db import db, tx
from app.services import arbitration
from app.services.common import now_iso

router = APIRouter(tags=["启动链"])


class BootBody(BaseModel):
    board_id: int
    target_slot: str = Field(pattern="^[AB]$")


class BootColorBody(BaseModel):
    color: str
    fail_streak: int = 0


class RollbackBody(BaseModel):
    board_id: int
    reason: str = ""

    model_config = {"extra": "ignore"}


@router.get("/启动链")
def list_boot():
    with db() as conn:
        rows = conn.execute(
            """
            SELECT c.board_id, b.name AS board_name, b.active_slot, c.pending_slot,
                   c.color, c.fail_streak, c.updated_at,
                   w.id AS window_id, w.state AS window_state, w.winner AS window_winner,
                   k.switch_success, k.rollback_success,
                   (SELECT COUNT(*) FROM slots s WHERE s.board_id=b.id AND s.role='现运行')
                     AS running_count
            FROM boot_chain c
            JOIN boards b ON b.id = c.board_id
            LEFT JOIN arbitration_windows w
              ON w.id = (SELECT MAX(id) FROM arbitration_windows WHERE board_id=b.id)
            LEFT JOIN contention_bucket k
              ON k.board_id=w.board_id AND k.window_id=w.id
            ORDER BY c.board_id
            """
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["让路臂"] = arbitration.YIELD_RULE
            d["双成"] = bool((d.get("switch_success") or 0) + (d.get("rollback_success") or 0) > 1)
            out.append(d)
        return out


@router.get("/裁决口径")
def yield_rule():
    with db() as conn:
        return arbitration.arm_summary(conn)


@router.get("/启动链/{board_id}/重放")
def replay(board_id: int):
    with db() as conn:
        return arbitration.board_replay(conn, board_id)


@router.post("/启动链/切槽")
def switch_slot(body: BootBody):
    # 切槽回执与硬退写口在同一 BEGIN IMMEDIATE 里裁决，先到先落、后到让路。
    with tx() as conn:
        return arbitration.switch_slot(conn, body.board_id, body.target_slot)


@router.post("/启动链/硬退")
def hard_rollback(body: RollbackBody):
    with tx() as conn:
        return arbitration.hard_rollback(conn, body.board_id, body.reason)


@router.post("/启动链/{board_id}/探针色")
def set_boot_color(board_id: int, body: BootColorBody):
    with tx() as conn:
        row = conn.execute("SELECT * FROM boot_chain WHERE board_id=?", (board_id,)).fetchone()
        if not row:
            raise HTTPException(404, "启动链不存在")
        ts = now_iso()
        conn.execute(
            "UPDATE boot_chain SET color=?, fail_streak=?, updated_at=? WHERE board_id=?",
            (body.color, body.fail_streak, ts, board_id),
        )
        return dict(conn.execute("SELECT * FROM boot_chain WHERE board_id=?", (board_id,)).fetchone())
