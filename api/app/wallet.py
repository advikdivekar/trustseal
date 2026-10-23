"""The TrustSeal wallet: pre-loaded money. The buyer tops it up, pays from it at the door, the seller is paid out into theirs.
Demo money only. Every movement is a row, so the balance can always be explained."""
import secrets
import sqlite3

from .ledger import now_iso

PIN = "2580"                      # demo wallet PIN for every account that has a wallet
START = {"acc-consumer": 1000.0, "acc-seller": 0.0}
LINKED = ["HDFC Bank ••4321", "SBI ••9087", "UPI advik@okbank"]
KIND_LABEL = {"TOPUP": "Money added", "RESERVE": "Reserved for delivery", "REVERSAL": "Returned to wallet", "PAYOUT": "Payout received", "DUE": "Charged after review",
              "REFUND": "Refunded", "CLAWBACK": "Refunded to the buyer"}


def ensure(conn: sqlite3.Connection) -> None:
    for acc, bal in START.items():
        conn.execute("INSERT OR IGNORE INTO wallets (account_id, balance, pin) VALUES (?,?,?)", (acc, bal, PIN))


def reset(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM wallet_txns")
    conn.execute("DELETE FROM wallets")
    ensure(conn)


def balance(conn, acc: str) -> float:
    r = conn.execute("SELECT balance FROM wallets WHERE account_id=?", (acc,)).fetchone()
    return float(r["balance"]) if r else 0.0


def pin_ok(conn, acc: str, pin: str) -> bool:
    r = conn.execute("SELECT pin FROM wallets WHERE account_id=?", (acc,)).fetchone()
    return bool(r) and secrets.compare_digest(r["pin"], (pin or "").strip())


def move(conn, acc: str, kind: str, amount: float, note: str = "", sid: str = "") -> str:
    """amount is signed: positive credits, negative debits. Returns the reference shown on receipts."""
    ensure(conn)
    after = balance(conn, acc) + amount
    conn.execute("UPDATE wallets SET balance=? WHERE account_id=?", (after, acc))
    ref = {"TOPUP": "ADD", "RESERVE": "RSV", "REVERSAL": "REV", "PAYOUT": "PAY", "DUE": "DUE", "REFUND": "RFD", "CLAWBACK": "CLB"}[kind] + f"{secrets.randbelow(10**9):09d}"
    conn.execute("INSERT INTO wallet_txns (account_id, kind, amount, balance_after, reference, shipment_id, note, created_at) VALUES (?,?,?,?,?,?,?,?)",
                 (acc, kind, amount, after, ref, sid, note, now_iso()))
    return ref


def history(conn, acc: str, limit: int = 30) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM wallet_txns WHERE account_id=? ORDER BY id DESC LIMIT ?", (acc, limit))]


def for_shipment(conn, sid: str) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT id, account_id, kind, amount, reference, note, created_at FROM wallet_txns WHERE shipment_id=? ORDER BY id", (sid,))]
