from __future__ import annotations
import sqlite3
from contextlib import contextmanager
from app.config import DB_PATH


def connect() -> sqlite3.Connection:
    # isolation_level=None：事务由 tx()/snapshot() 显式掌控，写口统一 BEGIN IMMEDIATE。
    conn = sqlite3.connect(DB_PATH, timeout=30.0, check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    # WAL + NORMAL：提交仍原子，只是省掉逐笔 fsync；裁决一致性靠 BEGIN IMMEDIATE。
    conn.execute("PRAGMA synchronous = NORMAL")
    # WAL 只需落一次；已处于 wal 时不再申请改模式，避免并发连接间抢锁。
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    if str(mode).lower() != "wal":
        try:
            conn.execute("PRAGMA journal_mode = WAL")
        except sqlite3.OperationalError:
            # 另一连接正在切模式；下一次连接再落即可。
            pass
    return conn


@contextmanager
def db():
    """只读连接。多语句对读请用 snapshot() 取一致快照。"""
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def tx():
    """写口：BEGIN IMMEDIATE 串行化同板争用，整笔裁决同进同出。"""
    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.execute("COMMIT")
    except BaseException:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise
    finally:
        conn.close()


@contextmanager
def snapshot():
    """读口：同一事务内多表对读，读侧可重放不漂移。"""
    conn = connect()
    try:
        conn.execute("BEGIN")
        yield conn
    except BaseException:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise
    else:
        conn.execute("COMMIT")
    finally:
        conn.close()


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS firmwares (
          id INTEGER PRIMARY KEY,
          name TEXT NOT NULL,
          version TEXT NOT NULL,
          digest TEXT NOT NULL,
          verify_state TEXT NOT NULL,
          size_kb INTEGER NOT NULL DEFAULT 0,
          ready_at TEXT
        );
        CREATE TABLE IF NOT EXISTS boards (
          id INTEGER PRIMARY KEY,
          name TEXT NOT NULL,
          active_slot TEXT NOT NULL,
          watchdog_sec INTEGER NOT NULL DEFAULT 30
        );
        CREATE TABLE IF NOT EXISTS slots (
          id INTEGER PRIMARY KEY,
          board_id INTEGER NOT NULL,
          slot_key TEXT NOT NULL,
          role TEXT NOT NULL,
          firmware_id INTEGER,
          write_state TEXT NOT NULL,
          boot_color TEXT NOT NULL DEFAULT '灰',
          FOREIGN KEY(board_id) REFERENCES boards(id),
          FOREIGN KEY(firmware_id) REFERENCES firmwares(id),
          UNIQUE(board_id, slot_key)
        );
        CREATE TABLE IF NOT EXISTS flash_orders (
          id INTEGER PRIMARY KEY,
          board_id INTEGER NOT NULL,
          target_slot TEXT NOT NULL,
          firmware_id INTEGER NOT NULL,
          status TEXT NOT NULL,
          note TEXT,
          created_at TEXT NOT NULL,
          FOREIGN KEY(board_id) REFERENCES boards(id),
          FOREIGN KEY(firmware_id) REFERENCES firmwares(id)
        );
        CREATE TABLE IF NOT EXISTS boot_chain (
          id INTEGER PRIMARY KEY,
          board_id INTEGER NOT NULL UNIQUE,
          pending_slot TEXT,
          color TEXT NOT NULL,
          fail_streak INTEGER NOT NULL DEFAULT 0,
          updated_at TEXT,
          FOREIGN KEY(board_id) REFERENCES boards(id)
        );
        CREATE TABLE IF NOT EXISTS rollback_policy (
          id INTEGER PRIMARY KEY CHECK (id = 1),
          mode TEXT NOT NULL,
          hold_sec INTEGER NOT NULL DEFAULT 20
        );
        CREATE TABLE IF NOT EXISTS events (
          id INTEGER PRIMARY KEY,
          kind TEXT NOT NULL,
          ref TEXT,
          detail TEXT NOT NULL,
          at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS settings (
          key TEXT PRIMARY KEY,
          value TEXT NOT NULL
        );

        -- 每板裁决底账：重放起点 + 已落序号 + 硬退可退旧槽
        CREATE TABLE IF NOT EXISTS board_arbitration (
          board_id INTEGER PRIMARY KEY,
          base_slot TEXT NOT NULL,
          previous_slot TEXT,
          seq INTEGER NOT NULL DEFAULT 0,
          updated_at TEXT,
          FOREIGN KEY(board_id) REFERENCES boards(id)
        );

        -- 争用窗：同板同窗内硬退与切槽只裁一次
        CREATE TABLE IF NOT EXISTS arbitration_windows (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          board_id INTEGER NOT NULL,
          base_slot TEXT NOT NULL,
          state TEXT NOT NULL DEFAULT '开',
          winner TEXT,
          switch_id INTEGER,
          rollback_id INTEGER,
          opened_at TEXT NOT NULL,
          opened_epoch INTEGER NOT NULL DEFAULT 0,
          settled_at TEXT,
          FOREIGN KEY(board_id) REFERENCES boards(id)
        );
        CREATE INDEX IF NOT EXISTS idx_windows_board_state
          ON arbitration_windows(board_id, state, id);

        -- 切槽回执
        CREATE TABLE IF NOT EXISTS switch_receipts (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          board_id INTEGER NOT NULL,
          window_id INTEGER NOT NULL,
          from_slot TEXT NOT NULL,
          to_slot TEXT NOT NULL,
          verdict TEXT NOT NULL,
          note TEXT,
          at TEXT NOT NULL
        );

        -- 硬退写口流水
        CREATE TABLE IF NOT EXISTS rollback_writes (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          board_id INTEGER NOT NULL,
          window_id INTEGER NOT NULL,
          from_slot TEXT,
          to_slot TEXT,
          verdict TEXT NOT NULL,
          note TEXT,
          at TEXT NOT NULL
        );

        -- 硬退争用桶：主键钉死一窗一胜方，避免计双成
        CREATE TABLE IF NOT EXISTS contention_bucket (
          board_id INTEGER NOT NULL,
          window_id INTEGER NOT NULL,
          state TEXT NOT NULL DEFAULT '开',
          winner TEXT,
          active_slot TEXT NOT NULL,
          switch_success INTEGER NOT NULL DEFAULT 0,
          rollback_success INTEGER NOT NULL DEFAULT 0,
          switch_receipt_id INTEGER,
          rollback_write_id INTEGER,
          updated_at TEXT NOT NULL,
          PRIMARY KEY(board_id, window_id),
          -- 结构上钉死不得计双成：每窗最多一胜。
          CHECK (switch_success + rollback_success <= 1),
          CHECK (switch_success IN (0,1) AND rollback_success IN (0,1))
        );

        -- 裁决流水：每条都记 active_after，读侧可从 base_slot 逐步重放
        CREATE TABLE IF NOT EXISTS arbitration_decisions (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          board_id INTEGER NOT NULL,
          seq INTEGER NOT NULL,
          kind TEXT NOT NULL,
          winner TEXT NOT NULL,
          active_after TEXT NOT NULL,
          window_id INTEGER,
          switch_id INTEGER,
          rollback_id INTEGER,
          detail TEXT,
          at TEXT NOT NULL,
          UNIQUE(board_id, seq)
        );
        """
    )
