from __future__ import annotations
from fastapi import APIRouter
from pydantic import BaseModel
from app.db import db, tx
from app.services import arbitration
from app.services.common import now_iso

router = APIRouter(tags=["回退拨"])


class PolicyBody(BaseModel):
    mode: str = "失败硬退"
    hold_sec: int = 20


@router.get("/回退拨")
def get_policy():
    with db() as conn:
        row = conn.execute("SELECT mode, hold_sec FROM rollback_policy WHERE id=1").fetchone()
        out = dict(row)
        out["让路臂"] = arbitration.YIELD_RULE
        return out


@router.post("/回退拨")
def set_policy(body: PolicyBody):
    # 让路胜负已全局认领「硬退吞切」，mode 不接受改口；拨杆只调争用窗时长。
    with tx() as conn:
        conn.execute(
            "UPDATE rollback_policy SET mode=?, hold_sec=? WHERE id=1",
            ("失败硬退", max(0, body.hold_sec)),
        )
        conn.execute(
            "INSERT INTO events(kind, ref, detail, at) VALUES (?,?,?,?)",
            ("回退拨", "policy", f"争用窗 hold_sec={body.hold_sec}（让路臂={arbitration.YIELD_RULE}）",
             now_iso()),
        )
        out = dict(conn.execute("SELECT mode, hold_sec FROM rollback_policy WHERE id=1").fetchone())
        out["让路臂"] = arbitration.YIELD_RULE
        return out
