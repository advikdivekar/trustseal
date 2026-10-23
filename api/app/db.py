import os
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

DATA = Path(os.environ.get("TRUSTSEAL_DATA") or Path(__file__).resolve().parent.parent / "data")
STORAGE = DATA / "storage"
DB_PATH = DATA / "trustseal.db"
WRITE_LOCK = threading.Lock()  # not an RLock: FastAPI may release a dependency on another thread

SCHEMA = """
CREATE TABLE IF NOT EXISTS participants (
  id TEXT PRIMARY KEY, role TEXT NOT NULL, name TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS accounts (
  id TEXT PRIMARY KEY, role TEXT NOT NULL, name TEXT NOT NULL, org TEXT NOT NULL, pin TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sessions (token TEXT PRIMARY KEY, participant_id TEXT NOT NULL, device_id TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS shipments (
  id TEXT PRIMARY KEY, order_ref TEXT NOT NULL, product_name TEXT NOT NULL, category TEXT NOT NULL,
  declared_value REAL NOT NULL, expected_colour TEXT, has_unique_id INTEGER NOT NULL,
  payment_mode TEXT NOT NULL, risk_tier TEXT NOT NULL, state TEXT NOT NULL, created_at TEXT NOT NULL,
  identity TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS checkpoints (
  id TEXT PRIMARY KEY, shipment_id TEXT NOT NULL, sequence INTEGER NOT NULL, role TEXT NOT NULL,
  actor_id TEXT, state TEXT NOT NULL, seal_scanned INTEGER NOT NULL DEFAULT 0,
  locked_at TEXT, lock_event_id TEXT);
CREATE TABLE IF NOT EXISTS seals (
  seal_code TEXT PRIMARY KEY, shipment_id TEXT, signed_token TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS evidence (
  id TEXT PRIMARY KEY, shipment_id TEXT NOT NULL, checkpoint_id TEXT NOT NULL, kind TEXT NOT NULL,
  purpose TEXT NOT NULL, sha256 TEXT NOT NULL, captured_at TEXT NOT NULL, actor_id TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS ai_checks (
  id TEXT PRIMARY KEY, shipment_id TEXT NOT NULL, checkpoint_id TEXT NOT NULL, evidence_id TEXT,
  purpose TEXT NOT NULL, check_type TEXT NOT NULL, status TEXT NOT NULL, score REAL, threshold REAL,
  explanation TEXT NOT NULL, model_name TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS payments (
  shipment_id TEXT PRIMARY KEY, mode TEXT NOT NULL, amount REAL NOT NULL, state TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS wallets (account_id TEXT PRIMARY KEY, balance REAL NOT NULL, pin TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS wallet_txns (
  id INTEGER PRIMARY KEY AUTOINCREMENT, account_id TEXT NOT NULL, kind TEXT NOT NULL, amount REAL NOT NULL, balance_after REAL NOT NULL,
  reference TEXT NOT NULL, shipment_id TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS handovers (
  id TEXT PRIMARY KEY, shipment_id TEXT NOT NULL, state TEXT NOT NULL, agent_id TEXT NOT NULL, opened_at TEXT NOT NULL, expires_at TEXT NOT NULL,
  code TEXT NOT NULL DEFAULT '', attempts INTEGER NOT NULL DEFAULT 0, reserve_expires_at TEXT NOT NULL DEFAULT '', short_by REAL NOT NULL DEFAULT 0,
  override INTEGER NOT NULL DEFAULT 0, override_note TEXT NOT NULL DEFAULT '', counted INTEGER NOT NULL DEFAULT 1, note TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS payment_txns (
  id TEXT PRIMARY KEY, shipment_id TEXT NOT NULL, kind TEXT NOT NULL, method TEXT NOT NULL, amount REAL NOT NULL,
  status TEXT NOT NULL, reference TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS notifications (
  id INTEGER PRIMARY KEY AUTOINCREMENT, shipment_id TEXT NOT NULL, to_id TEXT NOT NULL, to_name TEXT NOT NULL, to_role TEXT NOT NULL,
  channel TEXT NOT NULL, severity TEXT NOT NULL, subject TEXT NOT NULL, body TEXT NOT NULL, why TEXT NOT NULL,
  event_type TEXT NOT NULL, created_at TEXT NOT NULL, read_at TEXT);
CREATE TABLE IF NOT EXISTS reads (
  sha256 TEXT PRIMARY KEY, kind TEXT NOT NULL, data TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS events (
  event_id TEXT PRIMARY KEY, shipment_id TEXT NOT NULL, sequence_no INTEGER NOT NULL,
  actor_id TEXT NOT NULL, actor_role TEXT NOT NULL, event_type TEXT NOT NULL,
  payload TEXT NOT NULL, evidence_refs TEXT NOT NULL, timestamp_server TEXT NOT NULL,
  prev_hash TEXT NOT NULL, event_hash TEXT NOT NULL, server_signature TEXT NOT NULL,
  UNIQUE (shipment_id, sequence_no));
"""

GUARDS = """
CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events
BEGIN SELECT RAISE(ABORT, 'events are append-only'); END;
CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events
BEGIN SELECT RAISE(ABORT, 'events are append-only'); END;
"""


def connect() -> sqlite3.Connection:
    DATA.mkdir(parents=True, exist_ok=True)
    STORAGE.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


PHONES = {"acc-seller": "+91 98200 00001", "acc-warehouse": "+91 98200 00002", "acc-delivery": "+91 98200 00003",
          "acc-consumer": "+91 98200 00004", "acc-reviewer": "+91 98200 00005", "acc-admin": "+91 98200 00000"}
DEFAULT_ACCOUNTS = [
    ("acc-seller", "SELLER", "Riya Sharma", "Sharma Home Store", "1111"),
    ("acc-warehouse", "WAREHOUSE", "Karan Mehta", "Pune Sorting Hub", "2222"),
    ("acc-delivery", "DELIVERY", "Imran Shaikh", "QuickRoute Delivery", "3333"),
    ("acc-consumer", "CONSUMER", "Advik Divekar", "Customer", "4444"),
    ("acc-reviewer", "REVIEWER", "Meera Iyer", "QuickRoute Support", "5555"),
    ("acc-admin", "ADMIN", "Presenter", "TrustSeal", "0000"),
]


def init() -> None:
    with WRITE_LOCK:
        conn = connect()
        conn.executescript(SCHEMA + GUARDS)
        try:  # older databases: seals gained a status so a replaced seal can be voided
            conn.execute("ALTER TABLE seals ADD COLUMN status TEXT NOT NULL DEFAULT 'ACTIVE'")
        except sqlite3.OperationalError:
            pass
        try:  # a reviewer can override a failed check; the check stays on record, flagged
            conn.execute("ALTER TABLE ai_checks ADD COLUMN overridden INTEGER NOT NULL DEFAULT 0")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE accounts ADD COLUMN phone TEXT NOT NULL DEFAULT ''")
        except sqlite3.OperationalError:
            pass
        for a in DEFAULT_ACCOUNTS:
            conn.execute("INSERT OR IGNORE INTO accounts (id, role, name, org, pin) VALUES (?,?,?,?,?)", a)
            conn.execute("INSERT OR IGNORE INTO participants VALUES (?,?,?,?)", (a[0], a[1], a[2], "2026-01-01T00:00:00.000+00:00"))
        for acc, ph in PHONES.items():
            conn.execute("UPDATE accounts SET phone=? WHERE id=?", (ph, acc))
        conn.commit()
        conn.close()


@contextmanager
def unguarded(conn: sqlite3.Connection):
    """Demo-only: lift the append-only guard (reset, simulated DB tampering)."""
    conn.execute("DROP TRIGGER IF EXISTS events_no_update")
    conn.execute("DROP TRIGGER IF EXISTS events_no_delete")
    try:
        yield
    finally:
        conn.executescript(GUARDS)


def get_setting(conn, key: str, default: str) -> str:
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(conn, key: str, value: str) -> None:
    conn.execute("INSERT INTO settings (key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                 (key, value))
