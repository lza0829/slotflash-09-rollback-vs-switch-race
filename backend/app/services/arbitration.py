"""硬退 × 切槽 争用裁决（全局只认一臂：硬退吞切）。

规则（唯一口径，冲突条不问人）：
  同一争用窗内，硬退一旦落成，切槽整笔作废：
    - 目标槽角色不得升成现运行；
    - 切槽回执改写为「作废」，留痕可核；
    - 硬退争用桶每窗只记一胜（switch_success + rollback_success <= 1），不得计双成。

争用窗：以板为单位，从本窗第一笔落点起 `hold_sec` 秒内仍可被对向操作掀翻。
  - 窗内切槽先落成，硬退后到 → 掀回窗基槽，切槽回执照样作废（先切后硬退）。
  - 窗内硬退先落成，切槽后到 → 切槽不落，直接作废（先硬退后切）。
  - 窗定盘后的切槽/硬退属于新一笔，另开窗，不被旧账牵连。
  - 硬退未落成（无旧槽可退）不封窗、不计胜，切槽仍可在本窗正常落成（不得挂起）。

所有写点都由 routers 包在 `tx()`（BEGIN IMMEDIATE）里，两路最终落点同笔裁决。
"""
from __future__ import annotations

import time
from typing import Any

from fastapi import HTTPException

from app.services.common import now_iso

YIELD_RULE = "硬退吞切"
YIELD_RULE_NOTE = (
    "全局只贯彻「硬退吞切」一臂：同一争用窗内硬退落成则切槽整笔作废，"
    "新槽不得升现运行，硬退争用桶每窗只计一胜、不得计双成；"
    "冲突条不再问人，裁决由本口径直接给出。"
)


# ---------------------------------------------------------------- 基础读取

def _board(conn, board_id: int):
    row = conn.execute("SELECT * FROM boards WHERE id=?", (board_id,)).fetchone()
    if not row:
        raise HTTPException(404, "板卡不存在")
    return row


def _ledger(conn, board_id: int):
    row = conn.execute(
        "SELECT * FROM board_arbitration WHERE board_id=?", (board_id,)
    ).fetchone()
    if row:
        return row
    active = _board(conn, board_id)["active_slot"]
    ts = now_iso()
    conn.execute(
        "INSERT INTO board_arbitration(board_id, base_slot, previous_slot, seq, updated_at)"
        " VALUES (?,?,?,?,?)",
        (board_id, active, None, 0, ts),
    )
    return conn.execute(
        "SELECT * FROM board_arbitration WHERE board_id=?", (board_id,)
    ).fetchone()


def _ledger_view(conn, board_id: int):
    """只读视图：读侧不许写底账（校验屏可能跑在只读快照里）。"""
    row = conn.execute(
        "SELECT * FROM board_arbitration WHERE board_id=?", (board_id,)
    ).fetchone()
    if row:
        return row
    active = _board(conn, board_id)["active_slot"]
    return {
        "board_id": board_id,
        "base_slot": active,
        "previous_slot": None,
        "seq": 0,
        "updated_at": None,
    }


def _hold_sec(conn) -> int:
    row = conn.execute("SELECT hold_sec FROM rollback_policy WHERE id=1").fetchone()
    return max(0, int(row["hold_sec"])) if row else 20


def running_slots(conn, board_id: int) -> list[str]:
    return [
        r["slot_key"]
        for r in conn.execute(
            "SELECT slot_key FROM slots WHERE board_id=? AND role=? ORDER BY slot_key",
            (board_id, "现运行"),
        )
    ]


def _normalize_roles(conn, board_id: int, active: str) -> None:
    """现运行格收口：无论此前是不是半截双现运行，只留 active 一个现运行。"""
    conn.execute(
        "UPDATE slots SET role=CASE WHEN slot_key=? THEN '现运行' ELSE '空闲' END"
        " WHERE board_id=?",
        (active, board_id),
    )


def _bucket(conn, board_id: int, window_id: int):
    return conn.execute(
        "SELECT * FROM contention_bucket WHERE board_id=? AND window_id=?",
        (board_id, window_id),
    ).fetchone()


def _window(conn, window_id: int):
    return conn.execute(
        "SELECT * FROM arbitration_windows WHERE id=?", (window_id,)
    ).fetchone()


def _latest_window(conn, board_id: int):
    return conn.execute(
        "SELECT * FROM arbitration_windows WHERE board_id=? ORDER BY id DESC LIMIT 1",
        (board_id,),
    ).fetchone()


def _window_view(row, hold: int) -> dict | None:
    if not row:
        return None
    d = dict(row)
    opened = int(d.get("opened_epoch") or 0)
    d["争用窗时长秒"] = hold
    d["剩余秒"] = max(0, opened + hold - int(time.time())) if d.get("state") == "开" else 0
    return d


# ---------------------------------------------------------------- 争用窗

def _settle_window(conn, row, ts: str) -> None:
    conn.execute(
        "UPDATE arbitration_windows SET state='定', settled_at=? WHERE id=?",
        (ts, row["id"]),
    )
    conn.execute(
        "UPDATE contention_bucket SET state='定', updated_at=? WHERE board_id=? AND window_id=?",
        (ts, row["board_id"], row["id"]),
    )


def _get_or_open_window(conn, board_id: int, ts: str, now_e: int):
    """取本板当前争用窗；超时则定盘旧窗，再开新窗。返回 (窗口行, hold_sec)。"""
    hold = _hold_sec(conn)
    row = conn.execute(
        "SELECT * FROM arbitration_windows WHERE board_id=? AND state='开'"
        " ORDER BY id DESC LIMIT 1",
        (board_id,),
    ).fetchone()
    if row and now_e >= int(row["opened_epoch"] or 0) + hold:
        _settle_window(conn, row, ts)
        row = None
    if row is None:
        active = _board(conn, board_id)["active_slot"]
        cur = conn.execute(
            "INSERT INTO arbitration_windows(board_id, base_slot, state, opened_at, opened_epoch)"
            " VALUES (?,?,?,?,?)",
            (board_id, active, "开", ts, now_e),
        )
        wid = cur.lastrowid
        conn.execute(
            "INSERT INTO contention_bucket(board_id, window_id, state, winner, active_slot,"
            " switch_success, rollback_success, updated_at) VALUES (?,?,?,?,?,?,?,?)",
            (board_id, wid, "开", None, active, 0, 0, ts),
        )
        row = _window(conn, wid)
    return row, hold


# ---------------------------------------------------------------- 读侧视图

def _decision(conn, board_id: int, kind: str, winner: str, active_after: str,
              window_id: int, switch_id, rollback_id, detail: str, ts: str) -> None:
    seq = int(_ledger(conn, board_id)["seq"]) + 1
    conn.execute(
        "INSERT INTO arbitration_decisions(board_id, seq, kind, winner, active_after,"
        " window_id, switch_id, rollback_id, detail, at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (board_id, seq, kind, winner, active_after, window_id,
         switch_id, rollback_id, detail, ts),
    )
    conn.execute(
        "UPDATE board_arbitration SET seq=?, updated_at=? WHERE board_id=?",
        (seq, ts, board_id),
    )


def _event(conn, kind: str, ref: str, detail: str, ts: str) -> None:
    conn.execute(
        "INSERT INTO events(kind, ref, detail, at) VALUES (?,?,?,?)",
        (kind, ref, detail, ts),
    )


def _payload(conn, board_id: int, verdict: str, note: str,
             artifact_kind: str | None, artifact, window_row, hold: int) -> dict[str, Any]:
    board = _board(conn, board_id)
    if window_row is not None:
        # 同一笔里窗已被改写，回读最新值，响应不得带旧胜方。
        window_row = _window(conn, window_row["id"])
    win_id = window_row["id"] if window_row else None
    bucket = _bucket(conn, board_id, win_id) if win_id else None
    boot = conn.execute(
        "SELECT color, fail_streak FROM boot_chain WHERE board_id=?", (board_id,)
    ).fetchone()
    return {
        "board_id": board_id,
        "板卡": board["name"],
        "让路臂": YIELD_RULE,
        "裁决": verdict,
        "说明": note,
        "现运行": board["active_slot"],
        "现运行格": running_slots(conn, board_id),
        "窗口": _window_view(window_row, hold),
        "争用桶": dict(bucket) if bucket else None,
        "artifacts": [] if artifact_kind is None else [{"类": artifact_kind, "行": artifact}],
        # 兼容旧响应字段
        "active_slot": board["active_slot"],
        "color": boot["color"] if boot else None,
        "fail_streak": boot["fail_streak"] if boot else None,
    }


# ---------------------------------------------------------------- 切槽写口

def switch_slot(conn, board_id: int, target_slot: str) -> dict[str, Any]:
    board = _board(conn, board_id)
    slot = conn.execute(
        "SELECT * FROM slots WHERE board_id=? AND slot_key=?", (board_id, target_slot)
    ).fetchone()
    if not slot:
        raise HTTPException(400, "槽位不存在")

    ts = now_iso()
    now_e = int(time.time())

    if board["active_slot"] == target_slot:
        # 已在本槽：顺手收口，若前一笔留下半截双现运行则在此愈合。
        _normalize_roles(conn, board_id, board["active_slot"])
        latest = _latest_window(conn, board_id)
        if latest and latest["state"] == "开" and latest["winner"] == "硬退":
            # 本窗硬退已落成，用户仍点了切到硬退落点：记一笔作废回执供对读，
            # 不为这一笔开新窗、不动争用桶（仍只计硬退一胜）。
            cur = conn.execute(
                "INSERT INTO switch_receipts(board_id, window_id, from_slot, to_slot, verdict, note, at)"
                " VALUES (?,?,?,?,?,?,?)",
                (board_id, latest["id"], board["active_slot"], target_slot, "作废",
                 "硬退吞切：目标槽即硬退落点，切槽无可升，回执记作废", ts),
            )
            conn.execute(
                "UPDATE contention_bucket SET"
                " switch_receipt_id=COALESCE(switch_receipt_id, ?), updated_at=?"
                " WHERE board_id=? AND window_id=?",
                (cur.lastrowid, ts, board_id, latest["id"]),
            )
            return _payload(
                conn, board_id, "切槽作废",
                "本窗硬退已落成，目标槽即硬退落点；切槽无可升，只留作废回执",
                "切槽回执",
                dict(conn.execute("SELECT * FROM switch_receipts WHERE id=?", (cur.lastrowid,)).fetchone()),
                latest, _hold_sec(conn),
            )
        return _payload(
            conn, board_id, "已在本槽",
            "目标槽已是现运行，切槽不落空、不计胜",
            None, None, latest, _hold_sec(conn),
        )

    win, hold = _get_or_open_window(conn, board_id, ts, now_e)

    if win["winner"] == "硬退":
        # 硬退吞切：整笔切槽作废，目标槽不得升现运行；现运行留在硬退落点。
        _normalize_roles(conn, board_id, board["active_slot"])
        conn.execute(
            "UPDATE boards SET active_slot=? WHERE id=?", (board["active_slot"], board_id)
        )
        cur = conn.execute(
            "INSERT INTO switch_receipts(board_id, window_id, from_slot, to_slot, verdict, note, at)"
            " VALUES (?,?,?,?,?,?,?)",
            (board_id, win["id"], board["active_slot"], target_slot, "作废",
             "硬退吞切：窗内硬退已落成，切槽整笔作废，目标槽不得升现运行", ts),
        )
        receipt = conn.execute(
            "SELECT * FROM switch_receipts WHERE id=?", (cur.lastrowid,)
        ).fetchone()
        conn.execute(
            "UPDATE contention_bucket SET"
            " switch_receipt_id=COALESCE(switch_receipt_id, ?),"
            " switch_success=0, updated_at=?"
            " WHERE board_id=? AND window_id=?",
            (cur.lastrowid, ts, board_id, win["id"]),
        )
        _event(conn, "切槽作废", f"board-{board_id}",
               f"切 {target_slot} 被硬退吞掉（窗 {win['id']}）", ts)
        return _payload(
            conn, board_id, "切槽作废",
            "本窗硬退已落成，按「硬退吞切」切槽整笔作废，现运行留在旧槽",
            "切槽回执", dict(receipt), win, hold,
        )

    if win["winner"] == "切槽":
        receipt = conn.execute(
            "SELECT * FROM switch_receipts WHERE id=?", (win["switch_id"],)
        ).fetchone()
        if receipt and receipt["to_slot"] == target_slot:
            _normalize_roles(conn, board_id, target_slot)
            conn.execute(
                "UPDATE boards SET active_slot=? WHERE id=?", (target_slot, board_id)
            )
            return _payload(
                conn, board_id, "切槽落成",
                "同窗同目标，回执幂等返回，不重复计胜",
                "切槽回执", dict(receipt), win, hold,
            )
        # 同窗内改切另一槽：旧窗定盘，另开新窗，避免一窗计双成。
        _settle_window(conn, win, ts)
        win, hold = _get_or_open_window(conn, board_id, ts, now_e)
        board = _board(conn, board_id)

    # 正常落成
    old_active = _board(conn, board_id)["active_slot"]
    _normalize_roles(conn, board_id, target_slot)
    conn.execute("UPDATE boards SET active_slot=? WHERE id=?", (target_slot, board_id))
    if old_active != target_slot:
        conn.execute(
            "UPDATE board_arbitration SET previous_slot=?, updated_at=? WHERE board_id=?",
            (old_active, ts, board_id),
        )
    conn.execute(
        "UPDATE boot_chain SET pending_slot=NULL, color=?, fail_streak=0, updated_at=?"
        " WHERE board_id=?",
        ("绿", ts, board_id),
    )
    cur = conn.execute(
        "INSERT INTO switch_receipts(board_id, window_id, from_slot, to_slot, verdict, note, at)"
        " VALUES (?,?,?,?,?,?,?)",
        (board_id, win["id"], old_active, target_slot, "落成",
         f"切槽回执：{old_active} → {target_slot}，目标槽升为现运行", ts),
    )
    rid = cur.lastrowid
    conn.execute(
        "UPDATE arbitration_windows SET winner=?, switch_id=?, state='开' WHERE id=?",
        ("切槽", rid, win["id"]),
    )
    conn.execute(
        "UPDATE contention_bucket SET winner='切槽', switch_success=1, rollback_success=0,"
        " active_slot=?, switch_receipt_id=?, state='开', updated_at=?"
        " WHERE board_id=? AND window_id=?",
        (target_slot, rid, ts, board_id, win["id"]),
    )
    _decision(conn, board_id, "切槽", "切槽", target_slot, win["id"], rid, None,
              f"{old_active} → {target_slot}", ts)
    _event(conn, "切槽", f"board-{board_id}",
           f"切到 {target_slot}（窗 {win['id']}，硬退吞切臂）", ts)

    receipt = conn.execute("SELECT * FROM switch_receipts WHERE id=?", (rid,)).fetchone()
    return _payload(
        conn, board_id, "切槽落成", f"现运行 {old_active} → {target_slot}",
        "切槽回执", dict(receipt), win, hold,
    )


# ---------------------------------------------------------------- 硬退写口

def hard_rollback(conn, board_id: int, reason: str = "") -> dict[str, Any]:
    board = _board(conn, board_id)
    ledger = _ledger(conn, board_id)
    ts = now_iso()
    now_e = int(time.time())
    win, hold = _get_or_open_window(conn, board_id, ts, now_e)

    if win["winner"] == "硬退":
        row = conn.execute(
            "SELECT * FROM rollback_writes WHERE id=?", (win["rollback_id"],)
        ).fetchone()
        _normalize_roles(conn, board_id, board["active_slot"])
        return _payload(
            conn, board_id, "硬退落成",
            "同窗硬退幂等返回，不重复计胜",
            "硬退写口", dict(row) if row else None, win, hold,
        )

    if win["winner"] == "切槽":
        target = win["base_slot"]
        note = "掀回窗内切槽：切槽回执作废，现运行退回窗基槽"
    else:
        target = ledger["previous_slot"]
        note = "退旧槽"

    # 半截双现运行：演进途中两格都标了现运行。先按板现运行收口成单格，
    # 再走正常硬退；绝不让双现运行残留到读侧。
    running = running_slots(conn, board_id)
    healed = len(running) > 1
    if healed:
        _normalize_roles(conn, board_id, board["active_slot"])

    if not target or target == board["active_slot"]:
        why = "无旧槽可退" if not target else "已在本槽"
        cur = conn.execute(
            "INSERT INTO rollback_writes(board_id, window_id, from_slot, to_slot, verdict, note, at)"
            " VALUES (?,?,?,?,?,?,?)",
            (board_id, win["id"], board["active_slot"], None, "未落成",
             f"{why}：硬退不落，争用窗不封，切槽仍可在本窗落成"
             + ("；已收口半截双现运行" if healed else "")
             + (f"；理由={reason}" if reason else ""), ts),
        )
        conn.execute(
            "UPDATE contention_bucket SET rollback_write_id=?, updated_at=?"
            " WHERE board_id=? AND window_id=?",
            (cur.lastrowid, ts, board_id, win["id"]),
        )
        row = conn.execute(
            "SELECT * FROM rollback_writes WHERE id=?", (cur.lastrowid,)
        ).fetchone()
        _event(conn, "硬退未落成", f"board-{board_id}",
               f"{why}（窗 {win['id']}）" + ("；收口双现运行" if healed else ""), ts)
        return _payload(
            conn, board_id, "硬退未落成",
            f"{why}：硬退没有落成，不计胜；争用窗保持开放，切槽不被挂起"
            + ("；半截双现运行已收口为单格" if healed else ""),
            "硬退写口", dict(row), win, hold,
        )

    # 硬退落成
    from_slot = board["active_slot"]
    if healed:
        note += "；半截双现运行收口为单格"
    # 启动链旁读：硬退前的启动色与连败必须留痕，不得因收口而被抹掉。
    boot_before = conn.execute(
        "SELECT color, fail_streak, pending_slot FROM boot_chain WHERE board_id=?", (board_id,)
    ).fetchone()
    if boot_before:
        note += (
            f"；启动链旁读 色={boot_before['color']} 连败={boot_before['fail_streak']}"
            f" 在途={boot_before['pending_slot'] or '—'}"
        )
    _normalize_roles(conn, board_id, target)
    conn.execute("UPDATE boards SET active_slot=? WHERE id=?", (target, board_id))
    conn.execute(
        "UPDATE boot_chain SET pending_slot=NULL, color=?, fail_streak=0, updated_at=?"
        " WHERE board_id=?",
        ("绿", ts, board_id),
    )
    voided_switch = None
    if win["winner"] == "切槽":
        conn.execute(
            "UPDATE switch_receipts SET verdict=?, note=? WHERE id=?",
            ("作废", "硬退吞切：窗内切槽整笔作废，回执不得记落成", win["switch_id"]),
        )
        voided_switch = win["switch_id"]

    cur = conn.execute(
        "INSERT INTO rollback_writes(board_id, window_id, from_slot, to_slot, verdict, note, at)"
        " VALUES (?,?,?,?,?,?,?)",
        (board_id, win["id"], from_slot, target, "落成",
         (note + (f"；理由={reason}" if reason else "")), ts),
    )
    rid = cur.lastrowid
    conn.execute(
        "UPDATE arbitration_windows SET winner=?, rollback_id=?, state='开' WHERE id=?",
        ("硬退", rid, win["id"]),
    )
    conn.execute(
        "UPDATE contention_bucket SET winner='硬退', switch_success=0, rollback_success=1,"
        " active_slot=?, rollback_write_id=?, state='开', updated_at=?"
        " WHERE board_id=? AND window_id=?",
        (target, rid, ts, board_id, win["id"]),
    )
    conn.execute(
        "UPDATE board_arbitration SET previous_slot=NULL, updated_at=? WHERE board_id=?",
        (ts, board_id),
    )
    _decision(conn, board_id, "硬退", "硬退", target, win["id"], voided_switch, rid,
              f"{from_slot} → {target}；切槽胜记清零", ts)
    _event(conn, "硬退", f"board-{board_id}",
           f"{from_slot} → {target}（窗 {win['id']}，硬退吞切臂）", ts)

    row = conn.execute("SELECT * FROM rollback_writes WHERE id=?", (rid,)).fetchone()
    return _payload(
        conn, board_id, "硬退落成",
        f"现运行 {from_slot} → {target}；本窗切槽（若有）整笔作废，争用桶只计硬退一胜",
        "硬退写口", dict(row), win, hold,
    )


# ---------------------------------------------------------------- 读侧重放与互证

def board_replay(conn, board_id: int) -> dict[str, Any]:
    """从基槽沿裁决流水重放，校验现运行格、切槽回执、硬退写口、争用桶是否互证。"""
    board = _board(conn, board_id)
    ledger = _ledger_view(conn, board_id)
    base = ledger["base_slot"]
    decisions = [
        dict(r)
        for r in conn.execute(
            "SELECT seq, kind, winner, active_after, window_id, switch_id, rollback_id, detail, at"
            " FROM arbitration_decisions WHERE board_id=? ORDER BY seq",
            (board_id,),
        )
    ]
    replayed = decisions[-1]["active_after"] if decisions else base
    running = running_slots(conn, board_id)

    win = _latest_window(conn, board_id)
    bucket = _bucket(conn, board_id, win["id"]) if win else None
    latest_switch = conn.execute(
        "SELECT * FROM switch_receipts WHERE board_id=? ORDER BY id DESC LIMIT 1",
        (board_id,),
    ).fetchone()
    latest_rollback = conn.execute(
        "SELECT * FROM rollback_writes WHERE board_id=? ORDER BY id DESC LIMIT 1",
        (board_id,),
    ).fetchone()
    # 互证只认最新窗自己的回执：旧窗落成的切槽不被新窗的硬退牵连。
    win_switch = (
        conn.execute(
            "SELECT * FROM switch_receipts WHERE board_id=? AND window_id=?"
            " ORDER BY id DESC LIMIT 1",
            (board_id, win["id"]),
        ).fetchone()
        if win
        else None
    )
    win_rollback = (
        conn.execute(
            "SELECT * FROM rollback_writes WHERE board_id=? AND window_id=?"
            " ORDER BY id DESC LIMIT 1",
            (board_id, win["id"]),
        ).fetchone()
        if win
        else None
    )

    replay_ok = replayed == board["active_slot"]
    cell_ok = running == [board["active_slot"]]
    bucket_ok = True
    receipt_agree = True
    if win:
        if win["state"] == "开":
            bucket_ok = bool(bucket) and bucket["active_slot"] == board["active_slot"]
            if bucket and bucket["winner"] == "切槽":
                bucket_ok = (
                    bucket_ok
                    and bucket["switch_success"] == 1
                    and bucket["rollback_success"] == 0
                )
            if bucket and bucket["winner"] == "硬退":
                bucket_ok = (
                    bucket_ok
                    and bucket["rollback_success"] == 1
                    and bucket["switch_success"] == 0
                )
        if win["winner"] == "切槽":
            receipt_agree = (
                win_switch is not None
                and win_switch["verdict"] == "落成"
                and win_switch["to_slot"] == board["active_slot"]
            )
        elif win["winner"] == "硬退":
            receipt_agree = (
                win_rollback is not None
                and win_rollback["verdict"] == "落成"
                and win_rollback["to_slot"] == board["active_slot"]
            )
            # 同窗若有切槽回执，必须已作废，不得与硬退写口同时记落成。
            if win_switch is not None:
                receipt_agree = receipt_agree and win_switch["verdict"] == "作废"
        elif win["state"] == "开":
            receipt_agree = board["active_slot"] == win["base_slot"]
        if bucket:
            no_double = (
                int(bucket["switch_success"]) + int(bucket["rollback_success"]) <= 1
            )
            bucket_ok = bucket_ok and no_double

    return {
        "board_id": board_id,
        "board_name": board["name"],
        "基槽": base,
        "裁序": decisions,
        "重放现运行": replayed,
        "板现运行": board["active_slot"],
        "现运行格": running,
        "硬退可退": ledger["previous_slot"],
        "最新窗": _window_view(win, _hold_sec(conn)),
        "最新桶": dict(bucket) if bucket else None,
        "最新切槽回执": dict(latest_switch) if latest_switch else None,
        "最新硬退写口": dict(latest_rollback) if latest_rollback else None,
        "窗内切槽回执": dict(win_switch) if win_switch else None,
        "窗内硬退写口": dict(win_rollback) if win_rollback else None,
        "可重放": replay_ok,
        "单现运行": cell_ok,
        "桶不分叉": bucket_ok,
        "回执相符": receipt_agree,
        "自洽": bool(replay_ok and cell_ok and bucket_ok and receipt_agree),
        # 硬退吞切臂不产生「切成未退」缺口；此字段恒为空，供复现矩阵对读。
        "未执行缺口": [],
    }


def arm_summary(conn) -> dict[str, Any]:
    return {
        "让路臂": YIELD_RULE,
        "说明": YIELD_RULE_NOTE,
        "争用窗时长秒": _hold_sec(conn),
        "双成禁止": "switch_success + rollback_success <= 1（每窗只计一胜）",
        "缺口": "硬退吞切臂不产生切成未退缺口",
    }
