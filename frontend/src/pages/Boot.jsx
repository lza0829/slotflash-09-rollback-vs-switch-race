import React, { useEffect, useState } from "react";
import { api } from "../api";

export default function Boot() {
  const [rows, setRows] = useState([]);
  const [arm, setArm] = useState(null);
  const [last, setLast] = useState(null);
  const load = () => api.boot().then(setRows);
  useEffect(() => {
    load();
    api.yieldRule().then(setArm);
  }, []);

  const act = (p) =>
    p.then((r) => {
      setLast(r);
      return load();
    });

  return (
    <div className="stack">
      <section className="panel">
        <h1>启动链</h1>
        <p className="muted">
          硬退与切槽抢同一块板时，按固定让路臂当场裁决，冲突条不再问人。
        </p>
        {arm && (
          <p className="mono">
            让路臂：<span className="cyan">{arm.让路臂}</span> · 争用窗 {arm.争用窗时长秒}s ·{" "}
            {arm.双成禁止}
          </p>
        )}
        <table className="table">
          <thead>
            <tr>
              <th>板卡</th>
              <th>现运行</th>
              <th>现运行格</th>
              <th>在途槽</th>
              <th>启动色</th>
              <th>连败</th>
              <th>争用桶</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const bucket =
                r.window_winner == null
                  ? "—"
                  : `${r.window_winner} · 切${r.switch_success}/退${r.rollback_success}`;
              const doubleRun = (r.running_count || 0) > 1;
              return (
                <tr key={r.board_id}>
                  <td>{r.board_name}</td>
                  <td className="cyan">{r.active_slot}</td>
                  <td>{r.running_count === 1 ? "单" : doubleRun ? "双现运行!" : "无"}</td>
                  <td>{r.pending_slot || "—"}</td>
                  <td>{r.color}</td>
                  <td>{r.fail_streak}</td>
                  <td className={r.双成 ? "coral" : "mono"}>{r.双成 ? `${bucket} 双成!` : bucket}</td>
                  <td className="row">
                    <button
                      className="btn"
                      onClick={() => act(api.switchSlot({ board_id: r.board_id, target_slot: "A" }))}
                    >
                      切 A
                    </button>
                    <button
                      className="btn"
                      onClick={() => act(api.switchSlot({ board_id: r.board_id, target_slot: "B" }))}
                    >
                      切 B
                    </button>
                    <button
                      className="btn danger"
                      onClick={() => act(api.hardRollback({ board_id: r.board_id, reason: "启动红" }))}
                    >
                      硬退
                    </button>
                    <button
                      className="btn danger"
                      onClick={() =>
                        act(
                          api.setBootColor(r.board_id, {
                            color: "红",
                            fail_streak: r.fail_streak + 1,
                          }),
                        )
                      }
                    >
                      打红
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {last && (
          <p className="mono muted">
            上一笔：{last.板卡} {last.裁决} — {last.说明}
            {last.争用桶
              ? `（窗 ${last.争用桶.window_id}：${last.争用桶.winner || "未定"}，切${last.争用桶.switch_success}/退${last.争用桶.rollback_success}）`
              : ""}
          </p>
        )}
      </section>
    </div>
  );
}
