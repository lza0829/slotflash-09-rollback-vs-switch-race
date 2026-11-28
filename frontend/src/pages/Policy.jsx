import React, { useEffect, useState } from "react";
import { api } from "../api";

export default function Policy() {
  const [mode, setMode] = useState("失败硬退");
  const [hold, setHold] = useState(20);
  const [arm, setArm] = useState(null);
  const load = () =>
    api.policy().then((p) => {
      setMode(p.mode);
      setHold(p.hold_sec);
    });
  useEffect(() => {
    load();
    api.yieldRule().then(setArm);
  }, []);

  return (
    <div className="stack">
      <section className="panel">
        <h1>回退拨</h1>
        <p className="muted">
          硬退与切槽的让路胜负已全局钉死为「硬退吞切」，冲突条不再问人。拨杆只调争用窗时长
          hold_sec（同一窗内两路只裁一次）。
        </p>
        <div className="row">
          <select value={mode} disabled>
            <option value="失败硬退">失败硬退</option>
          </select>
          <input type="number" value={hold} onChange={(e) => setHold(Number(e.target.value))} />
          <span className="muted mono">hold_sec · 争用窗时长</span>
          <button className="btn" onClick={() => api.setPolicy({ mode, hold_sec: hold }).then(load)}>
            保存拨杆
          </button>
        </div>
      </section>
      {arm && (
        <section className="panel">
          <h2>已认领让路臂</h2>
          <p className="mono">
            {arm.让路臂}：{arm.说明}
          </p>
          <p className="mono muted">
            {arm.双成禁止} · {arm.缺口}
          </p>
        </section>
      )}
    </div>
  );
}
