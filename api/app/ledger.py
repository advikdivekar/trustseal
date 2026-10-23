"""Append-only, hash-linked, server-signed event log.

Tamper-evident, not a blockchain: see DECISIONS.md for the honest limits.
"""
import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from nacl.encoding import HexEncoder
from nacl.exceptions import BadSignatureError
from nacl.signing import SigningKey, VerifyKey

GENESIS = "0" * 64
import os
KEY_PATH = Path(os.environ.get("TRUSTSEAL_DATA") or Path(__file__).resolve().parent.parent / "data") / "server.key"


def canonicalize(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_hex(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _load_key() -> SigningKey:
    KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    if KEY_PATH.exists():
        return SigningKey(KEY_PATH.read_bytes(), encoder=HexEncoder)
    key = SigningKey.generate()
    KEY_PATH.write_bytes(key.encode(encoder=HexEncoder))
    return key


_KEY = _load_key()
SERVER_PUBLIC_KEY = _KEY.verify_key.encode(encoder=HexEncoder).decode()


def sign(message: str) -> str:
    return _KEY.sign(message.encode(), encoder=HexEncoder).signature.decode()


def verify_signature(message: str, signature: str) -> bool:
    try:
        VerifyKey(SERVER_PUBLIC_KEY, encoder=HexEncoder).verify(
            message.encode(), bytes.fromhex(signature)
        )
        return True
    except (BadSignatureError, ValueError):
        return False


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


HASHED_FIELDS = (
    "event_id", "shipment_id", "sequence_no", "actor_id", "actor_role", "event_type",
    "payload", "evidence_refs", "timestamp_server", "prev_hash",
)


def _hash_body(ev: dict) -> dict:
    return {k: ev[k] for k in HASHED_FIELDS}


def append_event(db: sqlite3.Connection, shipment_id: str, actor_id: str, actor_role: str,
                 event_type: str, payload: dict | None = None,
                 evidence_refs: list | None = None) -> dict:
    last = db.execute(
        "SELECT sequence_no, event_hash FROM events WHERE shipment_id=? ORDER BY sequence_no DESC LIMIT 1",
        (shipment_id,),
    ).fetchone()
    ev = {
        "event_id": str(uuid.uuid4()),
        "shipment_id": shipment_id,
        "sequence_no": (last["sequence_no"] + 1) if last else 1,
        "actor_id": actor_id,
        "actor_role": actor_role,
        "event_type": event_type,
        "payload": payload or {},
        "evidence_refs": evidence_refs or [],
        "timestamp_server": now_iso(),
        "prev_hash": last["event_hash"] if last else GENESIS,
    }
    ev["event_hash"] = sha256_hex(canonicalize(_hash_body(ev)))
    ev["server_signature"] = sign(ev["event_hash"])
    db.execute(
        "INSERT INTO events (event_id, shipment_id, sequence_no, actor_id, actor_role, event_type,"
        " payload, evidence_refs, timestamp_server, prev_hash, event_hash, server_signature)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (ev["event_id"], shipment_id, ev["sequence_no"], actor_id, actor_role, event_type,
         canonicalize(ev["payload"]), canonicalize(ev["evidence_refs"]), ev["timestamp_server"],
         ev["prev_hash"], ev["event_hash"], ev["server_signature"]),
    )
    return ev


def row_to_event(row: sqlite3.Row) -> dict:
    ev = dict(row)
    ev["payload"] = json.loads(ev["payload"])
    ev["evidence_refs"] = json.loads(ev["evidence_refs"])
    return ev


def verify_chain(db: sqlite3.Connection, shipment_id: str) -> dict:
    """Recompute every hash, link and signature. Report the first event that breaks."""
    rows = db.execute(
        "SELECT * FROM events WHERE shipment_id=? ORDER BY sequence_no", (shipment_id,)
    ).fetchall()
    prev = GENESIS
    for i, row in enumerate(rows, start=1):
        ev = row_to_event(row)
        problem = None
        if ev["sequence_no"] != i:
            problem = "sequence gap: an event is missing"
        elif ev["prev_hash"] != prev:
            problem = "link broken: previous hash does not match"
        elif sha256_hex(canonicalize(_hash_body(ev))) != ev["event_hash"]:
            problem = "content changed: recorded hash does not match the event"
        elif not verify_signature(ev["event_hash"], ev["server_signature"]):
            problem = "signature invalid"
        if problem:
            return {"ok": False, "checked": len(rows), "broken_at": ev["sequence_no"],
                    "event_type": ev["event_type"], "reason": problem}
        prev = ev["event_hash"]
    return {"ok": True, "checked": len(rows), "head": prev}
