import React, { useEffect, useState } from "react";
import { api } from "../api";

function lamp(role, color) {
  const cls = role === "现运行" ? "run" : color === "绿" ? "ok" : color === "红" ? "bad" : "";
  return (
    <span className={`slot-lamp ${cls}`}>
      <i />
      {role}/{color}
    </span>
  );
}

function bootLamp(color) {
  const cls = color === "绿" ? "ok" : color === "红" ? "bad" : color === "黄" ? "run" : "";
  return (
    <span className={`slot-lamp ${cls}`}>
      <i />
      {color}
    </span>
  );
}

function YesNo({ ok, yes = "是", no = "否" }) {
  return <span className={ok ? "cyan" : "coral"}>{ok ? yes : no}</span>;
}

export default function Align() {
  const [data, setData] = useState(null);
  const load = () => api.align().then(setData);
  useEffect(() => {
    load();
  }, []);
  if (!data) return <p className="muted">加载中…</p>;

  const slots = data.闪槽投影 || [];
  const boots = data.启动链投影 || [];
  const orders = data.写闪近单 || [];
  const policy = data.回退拨 || {};
  const arm = data.裁决口径 || {};
  const replays = data.重放 || [];

  return (
    <div className="stack">
      <section className="panel">
        <h1>校验屏</h1>
        <p className="muted">
          把闪槽角色、启动链色、近单状态、裁决流水拉到同一屏对读：读侧现运行须可沿流水重放。
        </p>
        <p className="mono">
          回退拨：{policy.mode || "—"} / {policy.hold_sec ?? "—"}s · 让路臂：
          <span className="cyan">{arm.让路臂 || "—"}</span> · 自洽：
          <YesNo ok={data.自洽} />
        </p>
        <p className="mono muted">
          {arm.双成禁止} · {arm.缺口}
        </p>
        <button className="btn" onClick={load}>
          重新对读
        </button>
      </section>

      <section className="panel">
        <h2>四件互证</h2>
        <p className="muted">硬退写口、切槽回执、现运行格、硬退争用桶须同一口径。</p>
        <table className="table">
          <thead>
            <tr>
              <th>板卡</th>
              <th>硬退写口</th>
              <th>切槽回执</th>
              <th>现运行格</th>
              <th>硬退争用桶</th>
            </tr>
          </thead>
          <tbody>
            {replays.length === 0 ? (
              <tr>
                <td colSpan={5} className="muted">
                  暂无裁决底账
                </td>
              </tr>
            ) : (
              replays.map((r) => (
                <tr key={`mutual-${r.board_id}`}>
                  <td>{r.board_name}</td>
                  <td className="mono">
                    {r.最新硬退写口
                      ? `${r.最新硬退写口.verdict} ${r.最新硬退写口.from_slot || "—"}→${
                          r.最新硬退写口.to_slot || "—"
                        }`
                      : "—"}
                  </td>
                  <td className="mono">
                    {r.最新切槽回执
                      ? `${r.最新切槽回执.verdict} ${r.最新切槽回执.from_slot}→${r.最新切槽回执.to_slot}`
                      : "—"}
                  </td>
                  <td className="mono">
                    {r.现运行格.length === 1 ? r.现运行格[0] : r.现运行格.join("+") || "—"}
                  </td>
                  <td className="mono">
                    {r.最新桶
                      ? `${r.最新桶.winner || "未定"} · 切${r.最新桶.switch_success}/退${r.最新桶.rollback_success}`
                      : "—"}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </section>

      <section className="panel">
        <h2>硬退 × 切槽 互证</h2>
        <table className="table">
          <thead>
            <tr>
              <th>板卡</th>
              <th>基槽</th>
              <th>重放现运行</th>
              <th>板现运行</th>
              <th>现运行格</th>
              <th>最新窗</th>
              <th>争用桶</th>
              <th>可重放</th>
              <th>桶不分叉</th>
              <th>自洽</th>
            </tr>
          </thead>
          <tbody>
            {replays.length === 0 ? (
              <tr>
                <td colSpan={10} className="muted">
                  暂无裁决底账
                </td>
              </tr>
            ) : (
              replays.map((r) => (
                <tr key={r.board_id}>
                  <td>{r.board_name}</td>
                  <td className="mono">{r.基槽}</td>
                  <td className="cyan">{r.重放现运行}</td>
                  <td className="cyan">{r.板现运行}</td>
                  <td className="mono">
                    {r.现运行格.length === 1 ? r.现运行格[0] : r.现运行格.join("+") || "—"}
                  </td>
                  <td className="mono">
                    {r.最新窗
                      ? `#${r.最新窗.id} ${r.最新窗.state}${r.最新窗.winner ? `/${r.最新窗.winner}` : ""}`
                      : "—"}
                  </td>
                  <td className="mono">
                    {r.最新桶
                      ? `${r.最新桶.winner || "未定"} 切${r.最新桶.switch_success}/退${r.最新桶.rollback_success}`
                      : "—"}
                  </td>
                  <td>
                    <YesNo ok={r.可重放} />
                  </td>
                  <td>
                    <YesNo ok={r.桶不分叉} />
                  </td>
                  <td>
                    <YesNo ok={r.自洽} />
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </section>

      <section className="panel">
        <h2>裁决流水</h2>
        <table className="table">
          <thead>
            <tr>
              <th>板</th>
              <th>序</th>
              <th>类</th>
              <th>胜方</th>
              <th>落点现运行</th>
              <th>窗</th>
              <th>详情</th>
            </tr>
          </thead>
          <tbody>
            {replays.flatMap((r) =>
              r.裁序.map((d) => (
                <tr key={`${r.board_id}-${d.seq}`}>
                  <td>{r.board_name}</td>
                  <td>{d.seq}</td>
                  <td>{d.kind}</td>
                  <td className={d.winner === "硬退" ? "coral" : "cyan"}>{d.winner}</td>
                  <td>{d.active_after}</td>
                  <td>{d.window_id ?? "—"}</td>
                  <td className="mono">{d.detail}</td>
                </tr>
              )),
            )}
            {replays.every((r) => r.裁序.length === 0) && (
              <tr>
                <td colSpan={7} className="muted">
                  暂无裁决流水
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </section>

      <section className="panel">
        <h2>闪槽投影</h2>
        <table className="table">
          <thead>
            <tr>
              <th>板卡</th>
              <th>槽</th>
              <th>角色灯</th>
              <th>写态</th>
              <th>板现运行</th>
            </tr>
          </thead>
          <tbody>
            {slots.map((r) => (
              <tr key={`${r.board_id}-${r.slot_key}`}>
                <td>{r.board_name}</td>
                <td>{r.slot_key}</td>
                <td>{lamp(r.role, r.boot_color)}</td>
                <td>{r.write_state}</td>
                <td className="cyan">{r.active_slot}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section className="panel">
        <h2>启动链投影</h2>
        <table className="table">
          <thead>
            <tr>
              <th>板卡</th>
              <th>启动色</th>
              <th>连败</th>
              <th>在途槽</th>
            </tr>
          </thead>
          <tbody>
            {boots.map((r) => (
              <tr key={r.board_id}>
                <td>#{r.board_id}</td>
                <td>{bootLamp(r.color)}</td>
                <td>{r.fail_streak}</td>
                <td>{r.pending_slot || "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section className="panel">
        <h2>写闪近单</h2>
        <table className="table">
          <thead>
            <tr>
              <th>ID</th>
              <th>板卡</th>
              <th>目标槽</th>
              <th>状态</th>
            </tr>
          </thead>
          <tbody>
            {orders.length === 0 ? (
              <tr>
                <td colSpan={4} className="muted">
                  暂无近单
                </td>
              </tr>
            ) : (
              orders.map((r) => (
                <tr key={r.id}>
                  <td>{r.id}</td>
                  <td>#{r.board_id}</td>
                  <td>{r.target_slot}</td>
                  <td>{r.status}</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </section>
    </div>
  );
}
