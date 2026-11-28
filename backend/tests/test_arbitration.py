"""硬退 × 切槽争用：复现矩阵。

全局只认一臂：硬退吞切。每个用例都落到四件互证
（硬退写口 / 切槽回执 / 现运行格 / 硬退争用桶）与读侧重放。
"""

from __future__ import annotations

import threading

from conftest import (
    assert_consistent,
    board_replay,
    boot_row,
    expire_windows,
    snapshot,
)


def switch(client, board_id, slot):
    r = client.post("/api/启动链/切槽", json={"board_id": board_id, "target_slot": slot})
    assert r.status_code == 200, r.text
    return r.json()


def rollback(client, board_id, reason=""):
    r = client.post("/api/启动链/硬退", json={"board_id": board_id, "reason": reason})
    assert r.status_code == 200, r.text
    return r.json()


def test_arm_is_claimed_globally(client):
    arm = client.get("/api/裁决口径").json()
    assert arm["让路臂"] == "硬退吞切"
    assert "<= 1" in arm["双成禁止"]
    assert "缺口" in arm["缺口"]
    # 回退拨不接受改口，只调争用窗时长。
    p = client.post("/api/回退拨", json={"mode": "挂缺口可核", "hold_sec": 5}).json()
    assert p["mode"] == "失败硬退"
    assert p["hold_sec"] == 5
    assert p["让路臂"] == "硬退吞切"


def test_先硬退后切_切槽整笔作废(client):
    # 先让本板有旧槽可退：切到 B 后窗定盘，再切回 A 留出 previous_slot=B。
    switch(client, 1, "B")
    expire_windows()
    switch(client, 1, "A")
    expire_windows()
    assert boot_row(client, 1)["active_slot"] == "A"

    # 硬退落成：现运行 A → B（旧槽）
    r1 = rollback(client, 1, "启动红")
    assert r1["裁决"] == "硬退落成"
    assert r1["现运行"] == "B"

    # 同一争用窗内切 A：被硬退吞掉，A 不得升现运行。
    r2 = switch(client, 1, "A")
    assert r2["裁决"] == "切槽作废"
    assert r2["现运行"] == "B"
    assert r2["现运行格"] == ["B"]
    assert r2["争用桶"]["winner"] == "硬退"
    assert r2["争用桶"]["switch_success"] == 0
    assert r2["争用桶"]["rollback_success"] == 1
    assert r2["artifacts"][0]["行"]["verdict"] == "作废"

    rep = assert_consistent(client, 1)
    assert rep["最新切槽回执"]["verdict"] == "作废"
    assert rep["硬退可退"] is None


def test_先切后硬退_回执作废且现运行退回窗基槽(client):
    r1 = switch(client, 1, "B")
    assert r1["裁决"] == "切槽落成"
    assert r1["现运行"] == "B"
    assert r1["争用桶"]["winner"] == "切槽"
    assert r1["争用桶"]["switch_success"] == 1
    assert r1["争用桶"]["rollback_success"] == 0

    r2 = rollback(client, 1, "启动红")
    assert r2["裁决"] == "硬退落成"
    assert r2["现运行"] == "A"  # 窗基槽
    assert r2["争用桶"]["winner"] == "硬退"
    assert r2["争用桶"]["switch_success"] == 0
    assert r2["争用桶"]["rollback_success"] == 1

    rep = assert_consistent(client, 1)
    # 切槽回执被改写作废，不得与硬退写口同时记落成。
    assert rep["窗内切槽回执"]["verdict"] == "作废"
    assert rep["窗内硬退写口"]["verdict"] == "落成"
    assert rep["窗内硬退写口"]["to_slot"] == "A"
    # 硬退吞切臂没有「切成未退」缺口。
    assert rep["未执行缺口"] == []


def test_双人并发点_每窗只计一胜(client):
    results = []
    lock = threading.Lock()

    def do_switch():
        r = client.post("/api/启动链/切槽", json={"board_id": 1, "target_slot": "B"})
        with lock:
            results.append(("切槽", r.json()))

    def do_rollback():
        r = client.post("/api/启动链/硬退", json={"board_id": 1, "reason": "并发"})
        with lock:
            results.append(("硬退", r.json()))

    threads = [threading.Thread(target=do_switch), threading.Thread(target=do_rollback)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(results) == 2
    rep = assert_consistent(client, 1)
    bucket = rep["最新桶"]
    assert bucket is not None
    # 同窗只裁一胜：无论如何并发，绝不计双成。
    assert bucket["switch_success"] + bucket["rollback_success"] <= 1
    # 硬退吞切：若硬退落成，切槽不得同时记成。
    if bucket["winner"] == "硬退":
        assert bucket["switch_success"] == 0
        assert rep["窗内切槽回执"] is None or rep["窗内切槽回执"]["verdict"] == "作废"
    active = boot_row(client, 1)["active_slot"]
    assert rep["重放现运行"] == active
    assert rep["现运行格"] == [active]


def test_双人并发点多轮_每轮落点自洽且绝不双成(client):
    for round_no in range(12):
        # 每轮前把窗定盘，让两路必然落在同一新窗里抢。
        expire_windows()
        results = []
        lock = threading.Lock()

        def do(kind):
            if kind == "切槽":
                r = client.post("/api/启动链/切槽", json={"board_id": 1, "target_slot": "B"})
            else:
                r = client.post("/api/启动链/硬退", json={"board_id": 1, "reason": f"r{round_no}"})
            with lock:
                results.append((kind, r.status_code, r.json()))

        threads = [threading.Thread(target=do, args=(k,)) for k in ("切槽", "硬退")]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert [r[1] for r in results] == [200, 200], results
        rep = assert_consistent(client, 1)
        bucket = rep["最新桶"]
        assert bucket is not None
        assert bucket["switch_success"] + bucket["rollback_success"] <= 1, (round_no, bucket)
        assert rep["现运行格"] == [rep["板现运行"]]


def test_半截双现运行回滚_收口成单格(client, sql):
    # 摆出半截态：A 仍占现运行，B 也被写到一半升了现运行。
    sql.execute("UPDATE slots SET role='现运行' WHERE board_id=1")
    sql.commit()
    assert len(
        [r for r in sql.execute("SELECT slot_key FROM slots WHERE board_id=1 AND role='现运行'")]
    ) == 2

    r = rollback(client, 1, "半截")
    assert r["现运行格"] == ["A"]
    rep = assert_consistent(client, 1)
    assert rep["单现运行"]
    assert rep["现运行格"] == ["A"]


def test_硬退失败后切槽不被挂起(client):
    # edge-02 种子里无 previous_slot → 硬退未落成，争用窗不得封死切槽。
    r1 = rollback(client, 2, "无旧槽")
    assert r1["裁决"] == "硬退未落成"
    assert r1["争用桶"]["winner"] is None
    assert r1["争用桶"]["rollback_success"] == 0

    r2 = switch(client, 2, "A")
    assert r2["裁决"] == "切槽落成"
    assert r2["现运行"] == "A"
    assert r2["争用桶"]["winner"] == "切槽"
    assert r2["争用桶"]["switch_success"] == 1
    assert_consistent(client, 2)


def test_窗关闭后旧槽可再硬退(client):
    switch(client, 1, "B")
    rollback(client, 1)
    assert switch(client, 1, "B")["裁决"] == "切槽作废"

    # 窗定盘后再硬退：属于新一笔，仍按旧槽口径落成，不被旧窗牵连。
    expire_windows()

    r = switch(client, 1, "B")
    assert r["裁决"] == "切槽落成"
    assert r["现运行"] == "B"
    rep = assert_consistent(client, 1)
    assert rep["硬退可退"] == "A"

    r2 = rollback(client, 1, "窗后排障")
    assert r2["裁决"] == "硬退落成"
    assert r2["现运行"] == "A"
    assert_consistent(client, 1)


def test_读侧可重放_沿裁序逐步推得现运行(client):
    switch(client, 1, "B")
    rollback(client, 1)
    rep = board_replay(client, 1)
    assert [d["kind"] for d in rep["裁序"]] == ["切槽", "硬退"]
    assert rep["裁序"][0]["active_after"] == "B"
    assert rep["裁序"][1]["active_after"] == "A"
    assert rep["重放现运行"] == rep["板现运行"] == "A"
    # 基槽 + 流水末条 = 板现运行；读侧不靠额外状态即可重放。
    assert rep["裁序"][-1]["active_after"] == rep["基槽"]


def test_校验屏对读不分叉(client):
    switch(client, 1, "B")
    rollback(client, 1)
    switch(client, 2, "A")
    snap = snapshot(client)
    assert snap["自洽"]
    for rep in snap["重放"]:
        board = next(b for b in snap["闪槽投影"] if b["board_id"] == rep["board_id"])
        roles = {
            s["slot_key"] for s in snap["闪槽投影"]
            if s["board_id"] == rep["board_id"] and s["role"] == "现运行"
        }
        assert roles == {board["active_slot"]}
        assert rep["板现运行"] == board["active_slot"]
        assert rep["重放现运行"] == board["active_slot"]
        if rep["最新桶"]:
            assert rep["最新桶"]["active_slot"] == board["active_slot"]
