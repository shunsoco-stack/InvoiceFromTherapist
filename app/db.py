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
  name TEXT NOT NULL,
  price INTEGER NOT NULL, -- yen
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
  price_override INTEGER,        -- nullable
  commission_type_override TEXT, -- nullable
  commission_value_override INTEGER,
  notes TEXT,
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

CREATE INDEX IF NOT EXISTS idx_treatments_date ON treatments(service_date);
CREATE INDEX IF NOT EXISTS idx_treatments_therapist ON treatments(therapist_id);
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
        conn.commit()
    finally:
        conn.close()


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

