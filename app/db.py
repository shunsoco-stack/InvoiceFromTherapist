import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS therapists (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  commission_type TEXT NOT NULL DEFAULT 'percent', -- 'percent' | 'fixed'
  commission_value INTEGER NOT NULL DEFAULT 50,    -- percent: 0-100, fixed: yen per treatment item
  is_active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS menus (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  display_id INTEGER,
  name TEXT NOT NULL,
  price INTEGER NOT NULL, -- yen
  coupon_discount INTEGER NOT NULL DEFAULT 0, -- yen per treatment item
  commission_type TEXT,   -- nullable: fallback to therapist
  commission_value INTEGER,
  is_active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS treatments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  service_date TEXT NOT NULL, -- YYYY-MM-DD
  therapist_id INTEGER NOT NULL,
  menu_id INTEGER NOT NULL,
  quantity INTEGER NOT NULL DEFAULT 1,
  hpb INTEGER NOT NULL DEFAULT 0, -- numeric input (e.g. HPB)
  p INTEGER NOT NULL DEFAULT 0,   -- numeric input (P)
  r INTEGER NOT NULL DEFAULT 0,   -- dropdown input (R) e.g. 500/1000
  price_override INTEGER,        -- nullable
  commission_type_override TEXT, -- nullable
  commission_value_override INTEGER,
  notes TEXT,
  count_as_customer INTEGER NOT NULL DEFAULT 1, -- 0 = kiosk menu slot 2 (add-on), not counted as guest/customer
  created_at TEXT NOT NULL,
  FOREIGN KEY (therapist_id) REFERENCES therapists(id),
  FOREIGN KEY (menu_id) REFERENCES menus(id)
);

CREATE TABLE IF NOT EXISTS payouts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  service_date TEXT NOT NULL, -- YYYY-MM-DD
  therapist_id INTEGER NOT NULL,
  paid_amount INTEGER NOT NULL, -- yen
  paid_at TEXT NOT NULL,
  method TEXT,
  notes TEXT,
  FOREIGN KEY (therapist_id) REFERENCES therapists(id),
  UNIQUE(service_date, therapist_id)
);

CREATE TABLE IF NOT EXISTS supplies (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  is_active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS supply_alerts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  supply_id INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'open', -- open | ack
  created_at TEXT NOT NULL,
  acknowledged_at TEXT,
  FOREIGN KEY (supply_id) REFERENCES supplies(id)
);

CREATE INDEX IF NOT EXISTS idx_treatments_date ON treatments(service_date);
CREATE INDEX IF NOT EXISTS idx_treatments_therapist ON treatments(therapist_id);
CREATE INDEX IF NOT EXISTS idx_supply_alerts_supply ON supply_alerts(supply_id);
"""


def _utc_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def db_path() -> str:
    env = os.getenv("SALON_DB_PATH")
    if env:
        return env
    Path("data").mkdir(parents=True, exist_ok=True)
    return str(Path("data") / "salon.sqlite3")


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_db() -> None:
    conn = connect()
    try:
        conn.executescript(SCHEMA_SQL)
        # Lightweight migrations for existing DBs
        _ensure_column(conn, table="treatments", column="hpb", col_def="INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, table="treatments", column="p", col_def="INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, table="treatments", column="r", col_def="INTEGER NOT NULL DEFAULT 0")
        _ensure_count_as_customer_column(conn)
        _ensure_menu_display_id_column(conn)
        _ensure_column(conn, table="menus", column="coupon_discount", col_def="INTEGER NOT NULL DEFAULT 0")
        conn.commit()
    finally:
        conn.close()


def _ensure_count_as_customer_column(conn: sqlite3.Connection) -> None:
    cols = q(conn, "PRAGMA table_info(treatments)")
    existing = {str(r["name"]) for r in cols}
    if "count_as_customer" in existing:
        return
    conn.execute(
        "ALTER TABLE treatments ADD COLUMN count_as_customer INTEGER NOT NULL DEFAULT 1",
    )
    conn.execute(
        "UPDATE treatments SET count_as_customer = 1 WHERE count_as_customer IS NULL",
    )


def _ensure_menu_display_id_column(conn: sqlite3.Connection) -> None:
    cols = q(conn, "PRAGMA table_info(menus)")
    existing = {str(r["name"]) for r in cols}
    if "display_id" not in existing:
        conn.execute("ALTER TABLE menus ADD COLUMN display_id INTEGER")
    conn.execute("UPDATE menus SET display_id = id WHERE display_id IS NULL")


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, col_def: str) -> None:
    cols = q(conn, f"PRAGMA table_info({table})")
    existing = {str(r["name"]) for r in cols}
    if column in existing:
        return
    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_def}")
    # Backfill existing rows if needed (DEFAULT handles new inserts; old rows become NULL on add in SQLite)
    conn.execute(f"UPDATE {table} SET {column} = 0 WHERE {column} IS NULL")


def q(conn: sqlite3.Connection, sql: str, args: Tuple[Any, ...] = ()) -> List[sqlite3.Row]:
    cur = conn.execute(sql, args)
    return list(cur.fetchall())


def q1(conn: sqlite3.Connection, sql: str, args: Tuple[Any, ...] = ()) -> Optional[sqlite3.Row]:
    cur = conn.execute(sql, args)
    return cur.fetchone()


def exec1(conn: sqlite3.Connection, sql: str, args: Tuple[Any, ...] = ()) -> int:
    cur = conn.execute(sql, args)
    conn.commit()
    return int(cur.lastrowid)


def now_iso() -> str:
    return _utc_iso()
