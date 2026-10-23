import asyncio
import json
import re
import secrets
import uuid
from contextlib import asynccontextmanager

from contextlib import contextmanager

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from . import db as dbm
from . import ledger, notify, vision, wallet
from .checks import run_checks, run_unbox
from .state_machine import (CHECKPOINT_TRANSITIONS, MAX_ATTEMPTS, PAYMENT_TRANSITIONS, REQUIRED_EVIDENCE, ROLE_ORDER,
                            SHIPMENT_TRANSITIONS, IllegalTransition, check)

PRODUCT_NAME = "TrustSeal"  # one constant so the working name is easy to change


# ---------- realtime ----------
class Hub:
    def __init__(self):
        self.clients: set[WebSocket] = set()
        self.loop: asyncio.AbstractEventLoop | None = None
        self.last_nid = 0

    async def broadcast(self, message: dict):
        dead = []
        for ws in list(self.clients):
            try:
                await ws.send_text(json.dumps(message))
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.clients.discard(ws)

    def notify(self, shipment_id: str | None):
        """Called from worker threads after a commit. Sends a fresh snapshot to every phone."""
        if not self.loop:
            return
        async def go():
            conn = dbm.connect()
            try:
                msg = {"type": "snapshot", "shipment": snapshot(conn, shipment_id) if shipment_id else None,
                       "list": shipment_list(conn)}
                fresh = [dict(r) for r in conn.execute("SELECT * FROM notifications WHERE id>? ORDER BY id", (self.last_nid,))]
                if fresh:
                    self.last_nid = fresh[-1]["id"]
            finally:
                conn.close()
            await self.broadcast(msg)
            if fresh:
                await self.broadcast({"type": "notify", "items": fresh})
        asyncio.run_coroutine_threadsafe(go(), self.loop)


hub = Hub()


@asynccontextmanager
async def lifespan(app: FastAPI):
    dbm.init()
    hub.loop = asyncio.get_running_loop()
    _c = dbm.connect()
    wallet.ensure(_c)
    _c.commit()
    hub.last_nid = _c.execute("SELECT COALESCE(MAX(id), 0) AS m FROM notifications").fetchone()["m"]
    _c.close()

    def sweep() -> set[str]:
        with dbm.WRITE_LOCK:          # waited for in a worker thread: waiting on the event loop would deadlock with requests
            c = dbm.connect()
            try:
                changed = expire_due(c)
                c.commit()
                return changed
            finally:
                c.close()

    async def ticker():  # time rules do not wait for someone to open the app
        while True:
            await asyncio.sleep(3)
            try:
                for sid in await asyncio.get_running_loop().run_in_executor(None, sweep):
                    hub.notify(sid)
            except Exception:
                pass
    task = asyncio.create_task(ticker())
    yield
    task.cancel()


app = FastAPI(title=PRODUCT_NAME, lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
dbm.init()


def sniff(path) -> str:
    head = path.read_bytes()[:16] if path.exists() else b""
    if head[:4] == b"\x1aE\xdf\xa3":
        return "video/webm"
    if head[4:8] == b"ftyp":
        return "video/mp4"
    if head[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if head[:4] == b"\x89PNG":
        return "image/png"
    return "application/octet-stream"


@app.get("/files/{sha}")
def files(sha: str):
    """Evidence is stored under its hash; the type is sniffed so video plays and Range requests work."""
    from fastapi.responses import FileResponse
    path = dbm.STORAGE / sha
    if not sha.isalnum() or not path.exists():
        raise HTTPException(404, "No such file")
    return FileResponse(path, media_type=sniff(path), headers={"cache-control": "public, max-age=31536000, immutable"})


@app.exception_handler(IllegalTransition)
async def illegal(_, exc: IllegalTransition):
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=409, content={"detail": str(exc)})


# ---------- plumbing ----------
def txn():
    with dbm.WRITE_LOCK:
        conn = dbm.connect()
        try:
            yield conn
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()


@contextmanager
def locked():
    """Write lock for code that must do slow work (a model call) before it writes."""
    with dbm.WRITE_LOCK:
        conn = dbm.connect()
        try:
            yield conn
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()


def auth_ro(x_token: str = Header(default="")) -> dict:
    conn = dbm.connect()
    try:
        row = conn.execute("SELECT p.* FROM sessions s JOIN participants p ON p.id=s.participant_id WHERE s.token=?",
                           (x_token,)).fetchone()
    finally:
        conn.close()
    if not row:
        raise HTTPException(401, "Join first")
    return dict(row)


def me(x_token: str = Header(default=""), conn=Depends(txn)) -> dict:
    row = conn.execute("SELECT p.* FROM sessions s JOIN participants p ON p.id=s.participant_id WHERE s.token=?",
                       (x_token,)).fetchone()
    if not row:
        raise HTTPException(401, "Join first")
    return dict(row)


def require(actor: dict, *roles: str):
    if actor["role"] not in roles:
        raise HTTPException(403, f"Only {', '.join(roles)} can do this")


def done(conn, shipment_id: str):
    chain = ledger.verify_chain(conn, shipment_id)
    if not chain["ok"] and not conn.execute("SELECT 1 FROM notifications WHERE shipment_id=? AND event_type='CHAIN_BROKEN'", (shipment_id,)).fetchone():
        notify.on_event(conn, shipment_id, "CHAIN_BROKEN", {"broken_at": chain.get("broken_at"), "reason": chain.get("reason")}, "SYSTEM")
    conn.commit()
    hub.notify(shipment_id)
    return snapshot(conn, shipment_id)


def log(conn, shipment_id, actor, event_type, payload=None, refs=None):
    ev = ledger.append_event(conn, shipment_id, actor["id"], actor["role"], event_type, payload, refs)
    notify.on_event(conn, shipment_id, event_type, payload or {}, actor["role"])  # tell exactly the people who must know
    return ev


def system_actor():
    return {"id": "system", "role": "SYSTEM", "name": "System"}


def set_shipment_state(conn, shipment_id, target):
    cur = conn.execute("SELECT state FROM shipments WHERE id=?", (shipment_id,)).fetchone()["state"]
    if cur != target:
        check(SHIPMENT_TRANSITIONS, cur, target, "Shipment")
        conn.execute("UPDATE shipments SET state=? WHERE id=?", (target, shipment_id))


def set_checkpoint_state(conn, cp_id, target):
    cur = conn.execute("SELECT state FROM checkpoints WHERE id=?", (cp_id,)).fetchone()["state"]
    check(CHECKPOINT_TRANSITIONS, cur, target, "Stage")
    conn.execute("UPDATE checkpoints SET state=? WHERE id=?", (target, cp_id))


def set_payment_state(conn, shipment_id, target, actor, why, ref="", via=""):
    cur = conn.execute("SELECT state FROM payments WHERE shipment_id=?", (shipment_id,)).fetchone()["state"]
    check(PAYMENT_TRANSITIONS, cur, target, "Payment")
    conn.execute("UPDATE payments SET state=? WHERE shipment_id=?", (target, shipment_id))
    log(conn, shipment_id, actor, "PAYMENT_STATE_CHANGED", {"from": cur, "to": target, "why": why, "txn": ref, "via": via})


def pay_row(conn, sid):
    return conn.execute("SELECT * FROM payments WHERE shipment_id=?", (sid,)).fetchone()


def setting_int(conn, key, default):
    return int(dbm.get_setting(conn, key, str(default)))


def iso_in(seconds: int) -> str:
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat(timespec="milliseconds")


def handover_row(conn, sid):
    return conn.execute("SELECT * FROM handovers WHERE shipment_id=? ORDER BY opened_at DESC, rowid DESC LIMIT 1", (sid,)).fetchone()


def live_handover(conn, sid):
    h = handover_row(conn, sid)
    return h if h and h["state"] in ("OPEN", "PAID") else None


def fail_count(conn, sid) -> int:
    return conn.execute("SELECT COUNT(*) AS n FROM handovers WHERE shipment_id=? AND state IN ('FAILED','EXPIRED','CANCELLED') AND counted=1", (sid,)).fetchone()["n"]


MAX_FAILED_HANDOVERS = 3


def reverse_reservation(conn, sid, actor, why):
    """Buyer's money goes straight back to their wallet. The seller is never paid for a parcel nobody confirmed."""
    pay = pay_row(conn, sid)
    if pay["state"] != "RESERVED":
        return
    buyer = "acc-consumer"
    ref = wallet.move(conn, buyer, "REVERSAL", pay["amount"], why, sid)
    set_payment_state(conn, sid, "PENDING", actor, why, ref)
    log(conn, sid, actor, "PAYMENT_REVERSED", {"amount": pay["amount"], "why": why, "balance": wallet.balance(conn, buyer)})


def escalate_handover(conn, sid, reason):
    """Too many handovers failed: stop trying and give the case to a reviewer. Funds are already back in the wallet."""
    last_ok = conn.execute("SELECT role FROM checkpoints WHERE shipment_id=? AND state='LOCKED' ORDER BY sequence DESC", (sid,)).fetchone()
    payload = {"failed_stage": "DELIVERY", "last_verified_stage": "WAREHOUSE", "reason": reason, "handover": True}
    log(conn, sid, system_actor(), "HANDOVER_FAILED", payload)
    set_shipment_state(conn, sid, "ON_HOLD")
    log(conn, sid, system_actor(), "EXCEPTION_REPORTED", {"note": reason, "auto": True, "handover": True})
    set_shipment_state(conn, sid, "UNDER_REVIEW")


def close_handover(conn, h, state, actor, why, event, extra=None):
    conn.execute("UPDATE handovers SET state=?, note=? WHERE id=?", (state, why, h["id"]))
    log(conn, h["shipment_id"], actor, event, {"why": why, **(extra or {})})
    if state in ("FAILED", "EXPIRED", "CANCELLED") and fail_count(conn, h["shipment_id"]) >= MAX_FAILED_HANDOVERS \
            and conn.execute("SELECT state FROM shipments WHERE id=?", (h["shipment_id"],)).fetchone()["state"] == "AWAITING_CONSUMER":
        escalate_handover(conn, h["shipment_id"], f"{MAX_FAILED_HANDOVERS} delivery attempts failed: {why}")


def expire_due(conn) -> set[str]:
    """Time rules, applied lazily and by a background loop: a door session that nobody used ends, and money that was reserved
       but never confirmed with the handover code goes back to the wallet."""
    changed = set()
    now = ledger.now_iso()
    for h in conn.execute("SELECT * FROM handovers WHERE state='PAID' AND reserve_expires_at<>'' AND reserve_expires_at<?", (now,)).fetchall():
        sid = h["shipment_id"]
        reverse_reservation(conn, sid, system_actor(), "Nobody confirmed the handover in time, so you were not charged.")
        if h["expires_at"] > now:
            conn.execute("UPDATE handovers SET state='OPEN', code='', attempts=0, reserve_expires_at='' WHERE id=?", (h["id"],))
        else:
            close_handover(conn, h, "EXPIRED", system_actor(), "The door session ended without a confirmed handover.", "HANDOVER_EXPIRED")
        changed.add(sid)
    for h in conn.execute("SELECT * FROM handovers WHERE state='OPEN' AND expires_at<?", (now,)).fetchall():
        close_handover(conn, h, "EXPIRED", system_actor(), "The door session ended before the buyer paid.", "HANDOVER_EXPIRED")
        changed.add(h["shipment_id"])
    return changed


def tick(conn) -> None:
    """Apply the time rules at the start of a request and commit them on their own, so a later error in the same request
       (a 409, say) can never undo a reversal that the clock already made."""
    changed = expire_due(conn)
    if changed:
        conn.commit()
        for sid in changed:
            hub.notify(sid)


def refund_released(conn, sid, actor, why):
    """The seller was already paid at the door and a complaint was upheld: the money goes back to the buyer from the seller."""
    pay = pay_row(conn, sid)
    back = wallet.move(conn, "acc-consumer", "REFUND", pay["amount"], why, sid)
    wallet.move(conn, "acc-seller", "CLAWBACK", -pay["amount"], why, sid)
    set_payment_state(conn, sid, "REFUNDED", actor, why, back)


def cancel_payment(conn, sid, actor, why):
    """Refund, return or upheld dispute. Before delivery nothing was charged; after delivery the payout is taken back."""
    pay = pay_row(conn, sid)
    if pay["state"] == "RESERVED":
        reverse_reservation(conn, sid, actor, why)
    state = pay_row(conn, sid)["state"]
    if state in ("PENDING", "DISPUTE_HOLD"):
        set_payment_state(conn, sid, "CANCELLED", actor, why)
    elif state == "RELEASED":
        refund_released(conn, sid, actor, why)


def charge_due(conn, sid, actor, why):
    """A dispute was rejected: the parcel was fine, so the buyer pays now. The wallet may go negative; the buyer then owes the balance."""
    pay = pay_row(conn, sid)
    if pay["state"] == "RELEASED":   # the seller was already paid at the door; a rejected complaint changes nothing
        return
    due = wallet.move(conn, "acc-consumer", "DUE", -pay["amount"], why, sid)
    ref = wallet.move(conn, "acc-seller", "PAYOUT", pay["amount"], "Dispute closed in your favour", sid)
    set_payment_state(conn, sid, "RELEASED", actor, why, ref, via="dispute")


# ---------- the seal: read from the parcel, never handed out ----------
def seal_hint(code: str) -> str:
    return "…" + code[-3:]


def active_seal(conn, sid):
    return conn.execute("SELECT seal_code FROM seals WHERE shipment_id=? AND status='ACTIVE'", (sid,)).fetchone()


def register_seal(conn, sid, code, actor, how) -> str | None:
    """Bind the serial read from the physical seal to this parcel. One serial can only ever belong to one parcel.
       Returns why it was refused, or None. The shared history keeps a fingerprint and the last three characters, not the serial."""
    row = conn.execute("SELECT shipment_id, status FROM seals WHERE seal_code=?", (code,)).fetchone()
    if row and row["shipment_id"] == sid and row["status"] == "ACTIVE":
        return None
    if row:
        return ("This seal was already used on " + ("another parcel" if row["shipment_id"] != sid else "this parcel and was voided")
                + ". A seal can only be used once, so this one cannot be registered.")
    old = active_seal(conn, sid)
    if old:
        conn.execute("UPDATE seals SET status='VOID' WHERE shipment_id=? AND status='ACTIVE'", (sid,))
    token = f"{ledger.sha256_hex(code.encode())}.{sid}"
    conn.execute("INSERT INTO seals (seal_code, shipment_id, signed_token, status) VALUES (?,?,?,'ACTIVE')", (code, sid, token + "." + ledger.sign(token)))
    log(conn, sid, actor, "SEAL_BOUND", {"seal_sha256": ledger.sha256_hex(code.encode()), "hint": seal_hint(code), "how": how,
                                         "replaces": seal_hint(old["seal_code"]) if old else None})
    return None


def seal_check(res_status, text):
    return {"check_type": "seal_serial", "status": res_status, "score": 1.0 if res_status == "PASS" else 0.0, "threshold": 1.0,
            "explanation": text, "model_name": "zxing-cpp + RapidOCR"}


def seal_results(conn, cp, actor, serial) -> list[dict]:
    """A photo of the seal: read its serial and either register it (seller, or after a re-seal) or compare it with the parcel's seal."""
    sid, role = cp["shipment_id"], cp["role"]
    if not serial:
        return [seal_check("REVIEW", "No seal serial could be read in this photo. Fill the frame with the seal's QR or its printed serial, close and steady, or use Scan.")]
    code, how = serial["code"], serial["how"]
    active = active_seal(conn, sid)
    resealing = bool(conn.execute("SELECT 1 FROM seals WHERE shipment_id=? AND status='VOID'", (sid,)).fetchone()) and not active
    if role == "SELLER" or resealing:
        err = register_seal(conn, sid, code, actor, how)
        if err:
            return [seal_check("FAIL", err)]
        if role != "SELLER":
            conn.execute("UPDATE checkpoints SET seal_scanned=1 WHERE id=?", (cp["id"],))
        return [seal_check("PASS", f"Seal {seal_hint(code)} was read from the photo ({how}) and registered to this parcel.")]
    if not active:
        return [seal_check("REVIEW", "No seal has been registered for this parcel yet.")]
    ok = code == active["seal_code"]
    log(conn, sid, actor, "SEAL_SCANNED" if role != "CONSUMER" else "CONSUMER_SCANNED",
        {"seal_sha256": ledger.sha256_hex(code.encode()), "hint": seal_hint(code), "match": ok, "how": how})
    if ok:
        conn.execute("UPDATE checkpoints SET seal_scanned=1, actor_id=? WHERE id=?", (actor["id"], cp["id"]))
        return [seal_check("PASS", f"The seal on the parcel ({seal_hint(code)}) is the one registered at packing.")]
    return [seal_check("FAIL", f"The seal in this photo ({seal_hint(code)}) is not this parcel's seal.")]


AUTO_REFUND_LIMIT = 5000.0


def triage(conn, sid, outcome) -> dict:
    """Customer service done by the record: what do we know about the parcel, and is the evidence strong enough to refund at once?"""
    amount = float(pay_row(conn, sid)["amount"])
    chain_ok = ledger.verify_chain(conn, sid)["ok"]
    seals = conn.execute("SELECT role, seal_scanned FROM checkpoints WHERE shipment_id=?", (sid,)).fetchall()
    seal_ok = all(r["seal_scanned"] for r in seals if r["role"] != "SELLER")
    video = bool(conn.execute("SELECT 1 FROM ai_checks WHERE shipment_id=? AND check_type='product_in_video' AND status='PASS' AND model_name='zxing-cpp'", (sid,)).fetchone())
    by = {r["check_type"]: r for r in outcome["results"]}
    idm, pm = by.get("identifier_match"), by.get("product_match")
    strong = bool(idm and idm["status"] == "FAIL" and pm and pm["status"] == "FAIL")
    auto = strong and not video and chain_ok and seal_ok and amount <= AUTO_REFUND_LIMIT
    if auto:
        cause = "The seal was intact at every stage and the barcode and the look of the product both differ from what was registered. The wrong item was packed."
    elif video:
        cause = "The seller's packing video shows the registered barcode being packed, but the buyer's scan differs. A person has to compare the evidence."
    elif not seal_ok or not chain_ok:
        cause = "A seal or the shared record did not check out somewhere along the way. A person has to find where."
    elif amount > AUTO_REFUND_LIMIT:
        cause = f"The order is above the ₹{AUTO_REFUND_LIMIT:,.0f} limit for automatic refunds, so a person confirms it."
    else:
        cause = "Only one signal disagrees, so a person has to look at the photos before anyone is blamed."
    facts = [{"label": k.replace("_", " "), "status": r["status"], "text": r["explanation"]} for k, r in by.items()]
    facts += [{"label": "seal at every stage", "status": "PASS" if seal_ok else "FAIL", "text": "Every handler scanned the same seal." if seal_ok else "A stage did not scan the seal."},
              {"label": "shared record", "status": "PASS" if chain_ok else "FAIL", "text": "The history verified." if chain_ok else "The history failed verification."},
              {"label": "packing video", "status": "PASS" if video else "REVIEW", "text": "It showed the registered barcode being packed." if video else "It did not show the barcode, so it cannot settle this."}]
    return {"auto": auto, "cause": cause, "suggested": "refund" if auto or strong else "review", "facts": facts, "amount": amount, "limit": AUTO_REFUND_LIMIT}


def ticket_for(conn, sid) -> str:
    return "CMP-" + (re.sub(r"\D", "", conn.execute("SELECT order_ref FROM shipments WHERE id=?", (sid,)).fetchone()["order_ref"])[-4:] or "0001")


def file_complaint(conn, sid, actor, *, category, reason, source, tri=None, auto=False):
    """One place where every complaint is opened, so a buyer's report and an automatic one are handled identically.
       If the evidence is strong enough the system also decides it, and the buyer is refunded without waiting for anyone."""
    paid = pay_row(conn, sid)["state"] == "RELEASED"
    resolve = bool(tri and tri["auto"])
    set_shipment_state(conn, sid, "DISPUTED")
    log(conn, sid, actor, "DISPUTE_OPENED", {"reason": reason, "category": category, "ticket": ticket_for(conn, sid), "source": source, "auto": auto,
                                              "auto_resolved": resolve, "paid": paid, "triage": tri,
                                              "callback": "Customer care will call you back if anything more is needed (simulated)."})
    if pay_row(conn, sid)["state"] == "PENDING":
        set_payment_state(conn, sid, "DISPUTE_HOLD", actor, "Dispute opened")
    if resolve:
        why = "Automatic decision: " + tri["cause"]
        set_shipment_state(conn, sid, "UNDER_REVIEW")
        log(conn, sid, system_actor(), "REVIEW_DECISION", {"decision": "refund", "reason": why, "stage": "CONSUMER", "responsibility_suggested": "SELLER", "instruction": "", "auto": True})
        set_shipment_state(conn, sid, "RESOLVED_REFUND")
        cancel_payment(conn, sid, system_actor(), why)


def complaint_info(conn, sid):
    evs = [ledger.row_to_event(r) for r in conn.execute("SELECT * FROM events WHERE shipment_id=? AND event_type IN ('DISPUTE_OPENED','REVIEW_DECISION') ORDER BY sequence_no", (sid,))]
    opened = next((e for e in reversed(evs) if e["event_type"] == "DISPUTE_OPENED"), None)
    if not opened:
        return None
    p = opened["payload"]
    decided = next((e for e in evs if e["event_type"] == "REVIEW_DECISION" and e["sequence_no"] > opened["sequence_no"]), None)
    state = conn.execute("SELECT state FROM shipments WHERE id=?", (sid,)).fetchone()["state"]
    return {"ticket": p.get("ticket", ""), "auto": bool(p.get("auto")), "auto_resolved": bool(p.get("auto_resolved")), "reason": p.get("reason", ""), "category": p.get("category", ""),
            "source": p.get("source", ""), "paid": bool(p.get("paid")), "triage": p.get("triage"), "opened_at": opened["timestamp_server"],
            "status": "OPEN" if state in ("DISPUTED", "UNDER_REVIEW") else "CLOSED",
            "decision": ({"decision": decided["payload"].get("decision"), "reason": decided["payload"].get("reason"), "auto": bool(decided["payload"].get("auto")),
                          "at": decided["timestamp_server"]} if decided else None)}


def get_cp(conn, cp_id) -> dict:
    row = conn.execute("SELECT * FROM checkpoints WHERE id=?", (cp_id,)).fetchone()
    if not row:
        raise HTTPException(404, "No such stage")
    return dict(row)


def own_stage(conn, actor, cp_id) -> dict:
    cp = get_cp(conn, cp_id)
    if cp["role"] != actor["role"]:
        raise HTTPException(403, f"This is the {cp['role'].lower()} stage. Your role is {actor['role'].lower()}.")
    return cp


def require_open(cp):
    if cp["state"] == "LOCKED":
        raise HTTPException(409, "This stage is locked. Evidence cannot be changed.")
    if cp["state"] == "PENDING":
        raise HTTPException(409, "This stage has not started. The previous stage must lock first.")
    if cp["state"] == "FAILED":
        raise HTTPException(409, "This stage failed a check and the shipment is on hold. A reviewer must decide.")


def latest_checks(conn, cp_id) -> dict[str, list[dict]]:
    """Latest check results per purpose (a retake supersedes earlier attempts; the ledger keeps both)."""
    latest = {}
    for ev in conn.execute("SELECT id, purpose FROM evidence WHERE checkpoint_id=? ORDER BY captured_at", (cp_id,)):
        latest[ev["purpose"]] = ev["id"]
    out = {}
    for purpose, evid in latest.items():
        out[purpose] = [dict(r) for r in conn.execute("SELECT * FROM ai_checks WHERE evidence_id=?", (evid,))]
    return out


def fail_checkpoint(conn, shipment_id, cp, reason, hold=True):
    last_ok = conn.execute("SELECT role FROM checkpoints WHERE shipment_id=? AND state='LOCKED' ORDER BY sequence DESC",
                           (shipment_id,)).fetchone()
    payload = {"failed_stage": cp["role"], "last_verified_stage": last_ok["role"] if last_ok else None, "reason": reason}
    if cp["state"] == "FAILED":  # already on hold: another failed try, no new hold
        log(conn, shipment_id, system_actor(), "CHECK_FAILED", {**payload, "again": True})
        return
    log(conn, shipment_id, system_actor(), "CHECK_FAILED", payload)
    if hold:
        set_checkpoint_state(conn, cp["id"], "FAILED")
        set_shipment_state(conn, shipment_id, "ON_HOLD")
        log(conn, shipment_id, system_actor(), "HOLD_PLACED", payload)


def resume_state(cp) -> str:
    return "AWAITING_CONSUMER" if cp["role"] in ("CONSUMER", "DELIVERY") else "IN_TRANSIT"


def owner_attempts(conn, sid, role) -> int:
    """Tries the stage owner has made since the hold was placed (or since a reviewer asked for a recheck)."""
    evs = conn.execute("SELECT event_type, actor_role, payload FROM events WHERE shipment_id=? ORDER BY sequence_no", (sid,)).fetchall()
    start = 0
    for i, e in enumerate(evs):
        if e["event_type"] == "HOLD_PLACED" or (e["event_type"] == "REVIEW_DECISION" and '"recheck"' in e["payload"]):
            start = i + 1
    return sum(1 for e in evs[start:] if e["actor_role"] == role and e["event_type"] in
               ("SEAL_SCANNED", "CONSUMER_SCANNED", "EVIDENCE_CAPTURED"))


def clear_hold(conn, cp, actor, how, note=""):
    set_checkpoint_state(conn, cp["id"], "IN_PROGRESS")
    sid = cp["shipment_id"]
    set_shipment_state(conn, sid, resume_state(cp))
    log(conn, sid, actor, "HOLD_CLEARED", {"stage": cp["role"], "how": how, "note": note})


def escalate(conn, cp, why):
    log(conn, cp["shipment_id"], system_actor(), "EXCEPTION_REPORTED", {"note": why, "auto": True})
    set_shipment_state(conn, cp["shipment_id"], "UNDER_REVIEW")


def settle_hold(conn, cp_id, actor, failed_now):
    """After the stage owner retries during a hold: lift it if everything is now right, escalate if they are out of tries."""
    cp = get_cp(conn, cp_id)
    ship = conn.execute("SELECT state FROM shipments WHERE id=?", (cp["shipment_id"],)).fetchone()["state"]
    if cp["state"] != "FAILED" or ship != "ON_HOLD":
        return
    have = latest_checks(conn, cp_id)
    still_failing = any(c["status"] == "FAIL" and not c["overridden"] for cs in have.values() for c in cs)
    if not still_failing and (cp["role"] == "SELLER" or cp["seal_scanned"]):
        clear_hold(conn, cp, actor, "fixed by the person at this stage")
    elif failed_now and owner_attempts(conn, cp["shipment_id"], cp["role"]) >= MAX_ATTEMPTS:
        escalate(conn, cp, f"Escalated automatically after {MAX_ATTEMPTS} unsuccessful tries.")


def case_info(conn, sid) -> dict:
    ship = conn.execute("SELECT state FROM shipments WHERE id=?", (sid,)).fetchone()["state"]
    if ship not in ("ON_HOLD", "UNDER_REVIEW", "DISPUTED"):
        return {"open": False}
    evs = [ledger.row_to_event(r) for r in conn.execute("SELECT * FROM events WHERE shipment_id=? ORDER BY sequence_no", (sid,))]
    marker = next((e for e in reversed(evs) if e["event_type"] in ("HOLD_PLACED", "DISPUTE_OPENED", "HANDOVER_FAILED")), None)
    if not marker:
        return {"open": False}
    dispute = marker["event_type"] == "DISPUTE_OPENED"
    stage = "CONSUMER" if dispute else marker["payload"].get("failed_stage")
    after = [e for e in evs if e["sequence_no"] > marker["sequence_no"]]
    decisions = [e for e in after if e["event_type"] == "REVIEW_DECISION"]
    recheck = next((e for e in reversed(decisions) if e["payload"].get("decision") == "recheck"), None)
    reported = any(e["event_type"] == "EXCEPTION_REPORTED" for e in after)
    cp = conn.execute("SELECT * FROM checkpoints WHERE shipment_id=? AND role=?", (sid, stage)).fetchone()
    owner = conn.execute("SELECT name, org FROM accounts WHERE id=?", (cp["actor_id"],)).fetchone() if cp and cp["actor_id"] else None
    reviewer = conn.execute("SELECT name, org FROM accounts WHERE role='REVIEWER'").fetchone()
    used = owner_attempts(conn, sid, stage) if ship == "ON_HOLD" else MAX_ATTEMPTS
    waiting = ({"role": stage, "name": owner["name"] if owner else stage.title()} if ship == "ON_HOLD"
               else {"role": "REVIEWER", "name": reviewer["name"] if reviewer else "A reviewer"})
    return {"open": True, "state": ship, "kind": "DISPUTE" if dispute else "HOLD", "handover": bool(marker["payload"].get("handover")), "stage": stage,
            "reason": marker["payload"].get("reason") or marker["payload"].get("note", ""),
            "last_verified": marker["payload"].get("last_verified_stage"),
            "attempts_left": max(0, MAX_ATTEMPTS - used), "reported": reported, "waiting_on": waiting,
            "instruction": recheck["payload"].get("instruction") if recheck else None,
            "opened_at": marker["timestamp_server"],
            "ticket": marker["payload"].get("ticket", ""), "auto": bool(marker["payload"].get("auto")), "triage": marker["payload"].get("triage"),
            "source": marker["payload"].get("source", ""), "paid": bool(marker["payload"].get("paid")),
            "steps": [{"label": "Problem found", "done": True},
                      {"label": "Fix attempt" if not dispute else "Buyer's report", "done": ship != "ON_HOLD" or reported},
                      {"label": "Reviewer decides", "done": False}, {"label": "Back on route" if not dispute else "Resolved", "done": False}]}


# ---------- snapshot ----------
def handover_public(conn, sid):
    """What every phone may know about the door session. The handover code is never in here: only the buyer can fetch it."""
    h = handover_row(conn, sid)
    if not h:
        return None
    agent = conn.execute("SELECT name, phone FROM accounts WHERE id=?", (h["agent_id"],)).fetchone()
    buyer = conn.execute("SELECT name, phone FROM accounts WHERE role='CONSUMER'").fetchone()
    return {"state": h["state"], "agent_name": agent["name"] if agent else "", "agent_phone": agent["phone"] if agent else "",
            "buyer_phone": buyer["phone"] if buyer else "", "opened_at": h["opened_at"], "expires_at": h["expires_at"],
            "reserve_expires_at": h["reserve_expires_at"], "short_by": h["short_by"], "attempts": h["attempts"],
            "failed_sessions": fail_count(conn, sid), "max_failed": MAX_FAILED_HANDOVERS}


def shipment_list(conn):
    return [dict(r) for r in conn.execute(
        "SELECT id, order_ref, product_name, state, created_at FROM shipments ORDER BY created_at DESC")]


def snapshot(conn, shipment_id: str) -> dict:
    s = conn.execute("SELECT * FROM shipments WHERE id=?", (shipment_id,)).fetchone()
    if not s:
        raise HTTPException(404, "No such shipment")
    cps = []
    for cp in conn.execute("SELECT * FROM checkpoints WHERE shipment_id=? ORDER BY sequence", (shipment_id,)):
        cp = dict(cp)
        cp["required"] = REQUIRED_EVIDENCE[cp["role"]]
        cp["evidence"] = [dict(e, url=f"/files/{e['sha256']}") for e in conn.execute(
            "SELECT * FROM evidence WHERE checkpoint_id=? ORDER BY captured_at", (cp["id"],))]
        cp["checks"] = [dict(c) for c in conn.execute(
            "SELECT * FROM ai_checks WHERE checkpoint_id=? ORDER BY created_at", (cp["id"],))]
        cp["latest_checks"] = latest_checks(conn, cp["id"])
        if cp["actor_id"]:
            a = conn.execute("SELECT a.name, a.org FROM accounts a WHERE a.id=?", (cp["actor_id"],)).fetchone()
            if a:
                cp["actor_name"], cp["actor_org"] = a["name"], a["org"]
        cps.append(cp)
    seal = active_seal(conn, shipment_id)
    bound = conn.execute("SELECT payload FROM events WHERE shipment_id=? AND event_type='SEAL_BOUND' ORDER BY sequence_no DESC", (shipment_id,)).fetchone()
    ship = dict(s)
    ship["identity"] = json.loads(ship["identity"] or "{}")
    return {
        "shipment": ship,
        "checkpoints": cps,
        "payment": {**dict(conn.execute("SELECT * FROM payments WHERE shipment_id=?", (shipment_id,)).fetchone()), "txns": wallet.for_shipment(conn, shipment_id)},
        "handover": handover_public(conn, shipment_id),
        "now": ledger.now_iso(),
        "seal": {"registered": bool(seal), "hint": seal_hint(seal["seal_code"]) if seal else "", "how": json.loads(bound["payload"]).get("how", "") if bound and seal else ""},
        "complaint": complaint_info(conn, shipment_id),
        "events": [ledger.row_to_event(r) for r in conn.execute(
            "SELECT * FROM events WHERE shipment_id=? ORDER BY sequence_no", (shipment_id,))],
        "chain": ledger.verify_chain(conn, shipment_id),
        "case": case_info(conn, shipment_id),
        "settings": {"scenario": dbm.get_setting(conn, "scenario", "A"), "ai_mode": ai_mode(conn), "rehearsal": is_rehearsal(conn)},
        "server_public_key": ledger.SERVER_PUBLIC_KEY,
    }


# ---------- auth ----------
def is_rehearsal(conn) -> bool:
    return dbm.get_setting(conn, "rehearsal", "0") == "1"


def ai_mode(conn) -> str:
    return dbm.get_setting(conn, "ai_mode", "live" if vision.available() else "mock")


@app.get("/config")
def config(conn=Depends(txn)):
    return {"rehearsal": is_rehearsal(conn), "ai_mode": ai_mode(conn), "model": vision.MODEL,
            "accounts": [{"id": r["id"], "role": r["role"], "name": r["name"], "org": r["org"],
                          "busy": bool(conn.execute("SELECT 1 FROM sessions WHERE participant_id=?", (r["id"],)).fetchone())}
                         for r in conn.execute("SELECT * FROM accounts WHERE role!='ADMIN' ORDER BY rowid")]}


class JoinBody(BaseModel):
    account_id: str
    pin: str
    device_id: str


@app.post("/auth/join")
def join(body: JoinBody, conn=Depends(txn)):
    acc = conn.execute("SELECT * FROM accounts WHERE id=?", (body.account_id,)).fetchone()
    if not acc or acc["pin"] != body.pin:
        raise HTTPException(403, "Wrong PIN")
    if acc["role"] != "ADMIN" and not is_rehearsal(conn):
        here = conn.execute("SELECT a.name FROM sessions s JOIN accounts a ON a.id=s.participant_id WHERE s.device_id=? AND s.participant_id!=?",
                            (body.device_id, acc["id"])).fetchone()
        if here:
            raise HTTPException(409, f"This device is already signed in as {here['name']}. One device can be one person. Sign out there first.")
        elsewhere = conn.execute("SELECT 1 FROM sessions WHERE participant_id=? AND device_id!=?", (acc["id"], body.device_id)).fetchone()
        if elsewhere:
            raise HTTPException(409, f"{acc['name']} is already signed in on another device.")
    conn.execute("DELETE FROM sessions WHERE participant_id=? AND device_id=?", (acc["id"], body.device_id))
    token = secrets.token_urlsafe(24)
    conn.execute("INSERT INTO sessions (token, participant_id, device_id) VALUES (?,?,?)", (token, acc["id"], body.device_id))
    conn.commit()
    return {"token": token, "participant": {"id": acc["id"], "role": acc["role"], "name": acc["name"], "org": acc["org"]}}


@app.post("/auth/leave")
def leave(x_token: str = Header(default=""), conn=Depends(txn)):
    conn.execute("DELETE FROM sessions WHERE token=?", (x_token,))
    conn.commit()
    return {"ok": True}


@app.get("/health")
def health():
    return {"ok": True, "product": PRODUCT_NAME}


# ---------- shipments ----------
class CreateShipment(BaseModel):
    order_ref: str
    product_name: str
    category: str = "home"
    declared_value: float
    expected_colour: str = ""
    has_unique_id: bool = False
    payment_mode: str = "prepaid"
    identity: dict = {}
    reference_sha256: str = ""


@app.post("/identify")
async def identify(file: UploadFile = File(...), hint_code: str = Form(""), mode: str = Form("product"), front_sha: str = Form(""), actor=Depends(auth_ro)):
    """Show the product to the camera: a local open-source vision model reads it. Nothing is registered yet.
       What the camera read is remembered here by photo hash, so the barcode and the product name that get registered later
       can only be ones the camera actually read: a typed number is never accepted."""
    require(actor, "SELLER")
    data = await file.read()
    sha = ledger.sha256_hex(data)
    (dbm.STORAGE / sha).write_bytes(data)
    conn = dbm.connect()
    ai_mode_now = ai_mode(conn)
    conn.close()

    def remember(kind, payload):
        with locked() as c:
            c.execute("INSERT OR REPLACE INTO reads (sha256, kind, data, created_at) VALUES (?,?,?,?)", (sha, kind, json.dumps(payload), ledger.now_iso()))
            c.commit()

    if mode == "barcode":  # step two: just the number, from the barcode or the digits printed under it
        code = await run_in_threadpool(vision.read_barcode_only, data, hint_code)
        if not code:
            return {"sha256": sha, "url": f"/files/{sha}", "barcode": None}
        verdict = {"status": "unverified", "detail": "Checks are in mock mode, so the barcode was not compared with the product."}
        if ai_mode_now == "live":
            rc = dbm.connect()
            try:
                row = rc.execute("SELECT data FROM reads WHERE sha256=? AND kind='front'", (front_sha,)).fetchone()
            finally:
                rc.close()
            front = json.loads(row["data"]) if row else {}
            fdata = (dbm.STORAGE / front_sha).read_bytes() if row and (dbm.STORAGE / front_sha).exists() else None
            verdict = await run_in_threadpool(vision.cross_check, front, {"title": code.get("title", ""), "by": code.get("by", ""), "source": code.get("source", "")}, data, fdata) \
                if row else {"status": "unverified", "detail": "Show the front of the product first, so the barcode can be compared with it."}
        bc = {"code": code["code"], "format": code["format"], "isbn": code["isbn"], "how": code["how"]}
        remember("barcode", {"barcode": bc, "verdict": verdict, "front_sha": front_sha})
        return {"sha256": sha, "url": f"/files/{sha}", "barcode": code, "verdict": verdict}
    if ai_mode_now == "live":
        try:
            found = await run_in_threadpool(vision.identify, data, hint_code)
        except Exception as e:
            raise HTTPException(503, f"The product reader is not responding ({type(e).__name__}). Is Ollama running?")
    else:
        found = {"product_name": "Green carpet", "brand": "", "variant": "", "category": "home", "net_quantity": "",
                 "colours": ["dark green"], "visible_text": [], "confidence": 0.9, "model": "mock-deterministic"}
    found.pop("barcode", None)   # the barcode is its own step, scanned on its own, so it cannot be smuggled in with the front photo
    remember("front", found)
    return {"sha256": sha, "url": f"/files/{sha}", "identity": found}


def risk_tier(value: float, unique_id: bool) -> str:
    return "HIGH" if value >= 20000 or unique_id else "STANDARD" if value >= 1000 else "LOW"


def create_shipment(conn, actor, b: CreateShipment) -> str:
    sid = str(uuid.uuid4())
    conn.execute("INSERT INTO shipments VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                 (sid, b.order_ref, b.product_name, b.category, b.declared_value, b.expected_colour,
                  int(bool(b.identity.get("barcode"))), b.payment_mode, risk_tier(b.declared_value, bool(b.identity.get("barcode"))), "CREATED",
                  ledger.now_iso(), json.dumps(b.identity)))
    for i, role in enumerate(ROLE_ORDER, start=1):
        conn.execute("INSERT INTO checkpoints (id, shipment_id, sequence, role, state) VALUES (?,?,?,?,?)",
                     (str(uuid.uuid4()), sid, i, role, "PENDING"))
    conn.execute("INSERT INTO payments VALUES (?,?,?,?)", (sid, "wallet", b.declared_value, "PENDING"))
    log(conn, sid, actor, "SHIPMENT_CREATED", {"order_ref": b.order_ref, "product": b.product_name})
    first = conn.execute("SELECT id FROM checkpoints WHERE shipment_id=? AND sequence=1", (sid,)).fetchone()["id"]
    conn.execute("UPDATE checkpoints SET actor_id=? WHERE id=?", (actor["id"], first))
    set_checkpoint_state(conn, first, "IN_PROGRESS")
    set_shipment_state(conn, sid, "IN_TRANSIT")
    if b.reference_sha256 and (dbm.STORAGE / b.reference_sha256).exists():
        evid = str(uuid.uuid4())
        conn.execute("INSERT INTO evidence VALUES (?,?,?,?,?,?,?,?)",
                     (evid, sid, first, "photo", "product_front", b.reference_sha256, ledger.now_iso(), actor["id"]))
        log(conn, sid, actor, "PRODUCT_IDENTIFIED", {"identity": b.identity},
            [{"evidence_id": evid, "sha256": b.reference_sha256}])
        conn.execute("INSERT INTO ai_checks (id, shipment_id, checkpoint_id, evidence_id, purpose, check_type, status, score, threshold, explanation, model_name, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                     (str(uuid.uuid4()), sid, first, evid, "product_front", "product_identity", "PASS",
                      float(b.identity.get("confidence", 0) or 0), 0.5,
                      f"Recognised as {b.identity.get('product_name', 'unknown')} and confirmed by the seller.",
                      str(b.identity.get("model", "seller")), ledger.now_iso()))
    return sid


def vet_identity(conn, b: CreateShipment):
    """The barcode on a new order must be one the camera read and that agrees with the product it saw. Nothing the phone
       types or edits can change that: the number comes from what this server read from the photo."""
    if not b.identity.get("barcode"):
        return
    row = conn.execute("SELECT data FROM reads WHERE sha256=? AND kind='barcode'", (b.identity.get("barcode_sha256", ""),)).fetchone()
    if not row:
        raise HTTPException(422, "The barcode has to be scanned with the camera. A typed number is not accepted.")
    rd = json.loads(row["data"])
    if rd["verdict"]["status"] == "mismatch":
        raise HTTPException(422, rd["verdict"]["detail"])
    b.identity["barcode"], b.identity["verification"] = rd["barcode"], rd["verdict"]
    b.identity["barcode_sha256"] = b.identity.get("barcode_sha256", "")


@app.post("/shipments")
def post_shipment(b: CreateShipment, actor=Depends(me), conn=Depends(txn)):
    require(actor, "SELLER")
    vet_identity(conn, b)
    return done(conn, create_shipment(conn, actor, b))


@app.get("/shipments")
def list_shipments(conn=Depends(txn)):
    return shipment_list(conn)


@app.get("/shipments/{sid}")
def get_shipment(sid: str, conn=Depends(txn)):
    return snapshot(conn, sid)


@app.get("/shipments/{sid}/verify-chain")
def verify(sid: str, conn=Depends(txn)):
    return ledger.verify_chain(conn, sid)


# ---------- stage actions ----------
def require_workable(conn, cp):
    """A stage is workable when it is in progress, or on hold with the stage owner still allowed to fix it."""
    if cp["state"] == "FAILED":
        ship = conn.execute("SELECT state FROM shipments WHERE id=?", (cp["shipment_id"],)).fetchone()["state"]
        if ship == "UNDER_REVIEW":
            raise HTTPException(409, "A reviewer is looking at this case. Wait for their decision.")
        if ship == "ON_HOLD":
            if owner_attempts(conn, cp["shipment_id"], cp["role"]) >= MAX_ATTEMPTS:
                raise HTTPException(409, "No tries left. Report it to the reviewer.")
            return
    require_open(cp)


@app.post("/checkpoints/{cp_id}/evidence")
async def upload_evidence(cp_id: str, purpose: str = Form(...), client_sha256: str = Form(...),
                          kind: str = Form("photo"), hint_code: str = Form(""), file: UploadFile = File(...), actor=Depends(auth_ro)):
    data = await file.read()
    if len(data) > (80 if kind == "video" else 15) * 1024 * 1024:
        raise HTTPException(413, "File too large")
    server_hash = ledger.sha256_hex(data)
    if server_hash != client_sha256:
        raise HTTPException(422, "Upload was corrupted: hashes do not match")

    def validate(conn):
        cp = own_stage(conn, actor, cp_id)
        require_workable(conn, cp)
        if purpose not in REQUIRED_EVIDENCE[cp["role"]]:
            raise HTTPException(422, f"{purpose} is not evidence for the {cp['role'].lower()} stage")
        return cp

    # Slow model work happens before the write lock is taken, so one phone's check never freezes the others.
    ro = dbm.connect()
    try:
        cp = validate(ro)
        scenario, mode = dbm.get_setting(ro, "scenario", "A"), ai_mode(ro)
        ref = ro.execute("SELECT e.sha256 FROM evidence e JOIN checkpoints c ON c.id=e.checkpoint_id WHERE c.shipment_id=? "
                         "AND c.role='SELLER' AND e.purpose='product_front' ORDER BY e.captured_at DESC", (cp["shipment_id"],)).fetchone()
        ship = ro.execute("SELECT identity FROM shipments WHERE id=?", (cp["shipment_id"],)).fetchone()
        seal = active_seal(ro, cp["shipment_id"])
    finally:
        ro.close()
    (dbm.STORAGE / server_hash).write_bytes(data)
    reference = (dbm.STORAGE / ref["sha256"]).read_bytes() if ref and (dbm.STORAGE / ref["sha256"]).exists() else None
    identity = json.loads(ship["identity"] or "{}")
    results = await run_in_threadpool(run_checks, cp["role"], purpose, scenario, mode, data, reference,
                                      identity, seal["seal_code"] if seal else "", hint_code)
    serial = None
    if purpose == "seal_side":   # the seal's own serial is read from the photo, whatever the AI mode is
        serial = await run_in_threadpool(vision.read_seal, data, "", (identity.get("barcode") or {}).get("code", ""))

    def write():
      with locked() as conn:
        cp = validate(conn)  # the stage may have locked while the model was thinking
        rs = results + (seal_results(conn, cp, actor, serial) if purpose == "seal_side" else [])
        evid = str(uuid.uuid4())
        conn.execute("INSERT INTO evidence VALUES (?,?,?,?,?,?,?,?)",
                     (evid, cp["shipment_id"], cp_id, kind, purpose, server_hash, ledger.now_iso(), actor["id"]))
        conn.execute("UPDATE checkpoints SET actor_id=? WHERE id=?", (actor["id"], cp_id))
        log(conn, cp["shipment_id"], actor, "EVIDENCE_CAPTURED", {"purpose": purpose, "kind": kind},
            [{"evidence_id": evid, "sha256": server_hash}])
        for r in rs:
            conn.execute("INSERT INTO ai_checks (id, shipment_id, checkpoint_id, evidence_id, purpose, check_type, status, score, threshold, explanation, model_name, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                         (str(uuid.uuid4()), cp["shipment_id"], cp_id, evid, purpose, r["check_type"], r["status"],
                          r["score"], r["threshold"], r["explanation"], r["model_name"], ledger.now_iso()))
        log(conn, cp["shipment_id"], system_actor(), "AI_CHECK_COMPLETED",
            {"purpose": purpose, "results": [{k: r[k] for k in ("check_type", "status", "score")} for r in rs]})
        failed = [r for r in rs if r["status"] == "FAIL"]
        if failed:
            if cp["role"] != "CONSUMER" or cp["state"] == "FAILED":
                fail_checkpoint(conn, cp["shipment_id"], cp, failed[0]["explanation"])
            else:
                log(conn, cp["shipment_id"], system_actor(), "CHECK_FAILED",
                    {"failed_stage": "CONSUMER", "reason": failed[0]["explanation"]})
        if cp["state"] == "FAILED":
            settle_hold(conn, cp_id, actor, failed_now=bool(failed))
        return done(conn, cp["shipment_id"])

    return await run_in_threadpool(write)


class ScanBody(BaseModel):
    seal_code: str
    how: str = "QR"


@app.post("/checkpoints/{cp_id}/scan")
def scan_seal(cp_id: str, b: ScanBody, actor=Depends(me), conn=Depends(txn)):
    """The seal's serial, read by the phone's scanner (or typed from the sticker when nothing else works). The seller's first
       scan registers the seal; everyone after compares against it. No one is ever given the serial by the system."""
    cp = own_stage(conn, actor, cp_id)
    require_workable(conn, cp)
    code = vision.norm_seal(b.seal_code)
    if not code:
        raise HTTPException(422, "That does not look like a seal serial. Scan the seal's QR or type what is printed on it.")
    how = "typed" if b.how == "typed" else "QR"
    sid = cp["shipment_id"]
    conn.execute("UPDATE checkpoints SET actor_id=? WHERE id=?", (actor["id"], cp_id))  # the case names the real person
    active = active_seal(conn, sid)
    event = "SEAL_SCANNED" if cp["role"] != "CONSUMER" else "CONSUMER_SCANNED"
    voided = bool(conn.execute("SELECT 1 FROM seals WHERE shipment_id=? AND status='VOID'", (sid,)).fetchone())
    mark = {"seal_sha256": ledger.sha256_hex(code.encode()), "hint": seal_hint(code), "how": how}
    if not active and (cp["role"] == "SELLER" or voided):       # registering the seal, or the new one after a re-seal
        err = register_seal(conn, sid, code, actor, how)
        if err:
            log(conn, sid, actor, event, {**mark, "match": False})
            fail_checkpoint(conn, sid, cp, err)
            if cp["state"] == "FAILED":
                settle_hold(conn, cp_id, actor, failed_now=True)
            return done(conn, sid)
        if cp["role"] != "SELLER":
            conn.execute("UPDATE checkpoints SET seal_scanned=1 WHERE id=?", (cp_id,))
            if cp["state"] == "FAILED":
                settle_hold(conn, cp_id, actor, failed_now=False)
        return done(conn, sid)
    if not active:
        raise HTTPException(409, "The seller has not registered the seal on this parcel yet.")
    row = conn.execute("SELECT * FROM seals WHERE seal_code=?", (code,)).fetchone()
    ok = code == active["seal_code"]
    log(conn, sid, actor, event, {**mark, "match": ok})
    if ok:
        conn.execute("UPDATE checkpoints SET seal_scanned=1, actor_id=? WHERE id=?", (actor["id"], cp_id))
        if cp["state"] == "FAILED":
            settle_hold(conn, cp_id, actor, failed_now=False)
    else:
        why = ("This seal was voided when the parcel was re-sealed. Use the new seal." if row and row["shipment_id"] == sid
               else "The scanned seal belongs to a different parcel." if row else "The scanned seal is not this parcel's seal.")
        fail_checkpoint(conn, sid, cp, why)
        if cp["state"] == "FAILED":
            settle_hold(conn, cp_id, actor, failed_now=True)
    return done(conn, sid)


@app.post("/checkpoints/{cp_id}/lock")
def lock(cp_id: str, actor=Depends(me), conn=Depends(txn)):
    cp = own_stage(conn, actor, cp_id)
    if cp["role"] == "CONSUMER":
        raise HTTPException(409, "The consumer accepts or disputes instead of locking.")
    require_open(cp)
    problems = readiness(conn, cp)
    if problems:
        raise HTTPException(409, "Not ready: " + "; ".join(problems))
    sid = cp["shipment_id"]
    ev = log(conn, sid, actor, "STAGE_LOCKED", {"stage": cp["role"]})
    set_checkpoint_state(conn, cp_id, "LOCKED")
    conn.execute("UPDATE checkpoints SET locked_at=?, lock_event_id=? WHERE id=?", (ledger.now_iso(), ev["event_id"], cp_id))
    nxt = conn.execute("SELECT * FROM checkpoints WHERE shipment_id=? AND sequence=?", (sid, cp["sequence"] + 1)).fetchone()
    set_checkpoint_state(conn, nxt["id"], "IN_PROGRESS")
    set_shipment_state(conn, sid, "AWAITING_CONSUMER" if nxt["role"] == "CONSUMER" else "IN_TRANSIT")
    log(conn, sid, system_actor(), "HANDOVER_CONFIRMED", {"from": cp["role"], "to": nxt["role"]})
    return done(conn, sid)


def readiness(conn, cp) -> list[str]:
    problems = []
    have = latest_checks(conn, cp["id"])
    for purpose in REQUIRED_EVIDENCE[cp["role"]]:
        if purpose not in have:
            if cp["role"] != "CONSUMER":   # the buyer can scan the seal live instead of photographing it
                problems.append(f"missing {purpose.replace('_', ' ')} photo")
        elif any(c["status"] == "FAIL" and not c["overridden"] for c in have[purpose]):
            problems.append(f"{purpose.replace('_', ' ')} failed a check")
    if cp["role"] == "SELLER":
        if not active_seal(conn, cp["shipment_id"]):
            problems.append("scan the seal on the parcel so it is registered")
    elif not cp["seal_scanned"]:
        problems.append("scan the parcel seal")
    return problems


class Note(BaseModel):
    note: str


@app.post("/checkpoints/{cp_id}/exception")
def exception(cp_id: str, b: Note, actor=Depends(me), conn=Depends(txn)):
    cp = own_stage(conn, actor, cp_id)
    if cp["state"] != "FAILED":
        raise HTTPException(409, "There is no failed check to report on this stage.")
    ship = conn.execute("SELECT state FROM shipments WHERE id=?", (cp["shipment_id"],)).fetchone()["state"]
    if ship != "ON_HOLD":
        raise HTTPException(409, "This case is already with a reviewer.")
    log(conn, cp["shipment_id"], actor, "EXCEPTION_REPORTED", {"note": b.note})
    set_shipment_state(conn, cp["shipment_id"], "UNDER_REVIEW")
    return done(conn, cp["shipment_id"])


# ---------- consumer ----------
# ---------- wallet ----------
def fail_loud(conn, sid, status, message):
    """Keep what just happened on the record (events, messages) and tell every phone, then answer with the error."""
    conn.commit()
    hub.notify(sid)
    raise HTTPException(status, message)


@app.get("/wallet")
def my_wallet(actor=Depends(me), conn=Depends(txn)):
    if actor["role"] not in ("CONSUMER", "SELLER"):
        raise HTTPException(403, "Only the buyer and the seller have a wallet")
    wallet.ensure(conn)
    conn.commit()
    return {"balance": wallet.balance(conn, actor["id"]), "txns": wallet.history(conn, actor["id"]), "linked": wallet.LINKED, "phone": ""}


class TopUp(BaseModel):
    amount: float
    source: str
    pin: str


@app.post("/wallet/topup")
def topup(b: TopUp, actor=Depends(me), conn=Depends(txn)):
    require(actor, "CONSUMER")
    if not wallet.pin_ok(conn, actor["id"], b.pin):
        raise HTTPException(403, "Wrong wallet PIN")
    if b.source not in wallet.LINKED:
        raise HTTPException(422, "Choose one of your linked accounts")
    if not (50 <= b.amount <= 50000):
        raise HTTPException(422, "You can add between ₹50 and ₹50,000 at a time")
    ref = wallet.move(conn, actor["id"], "TOPUP", b.amount, f"From {b.source}")
    bal = wallet.balance(conn, actor["id"])
    # If a delivery person is waiting at the door because this wallet was short, tell them they can go ahead.
    sid = conn.execute("SELECT id FROM shipments WHERE state='AWAITING_CONSUMER' ORDER BY created_at DESC").fetchone()
    h = live_handover(conn, sid["id"]) if sid else None
    amount = pay_row(conn, sid["id"])["amount"] if sid else 0
    notify.direct(conn, actor["id"], ("SMS",), "info", "Money added to your wallet",
                  f"TrustSeal: ₹{b.amount:,.0f} added to your wallet from {b.source}. Balance ₹{bal:,.0f}. Ref {ref}.", "", "Confirms the money you just added.", sid["id"] if sid else "")
    if h and h["short_by"] > 0 and bal >= amount:
        conn.execute("UPDATE handovers SET short_by=0 WHERE id=?", (h["id"],))
        notify.direct(conn, h["agent_id"], ("SMS",), "action", "Buyer topped up their wallet",
                      f"TrustSeal: the buyer added money and can pay now for parcel {sid['id'][:8]}. Please wait at the door.", "", "You were waiting for the buyer's wallet to be topped up.", sid["id"])
    conn.commit()
    hub.notify(sid["id"] if sid else None)
    return {"balance": bal, "reference": ref}


# ---------- the door: handover protocol ----------
def door_events(conn, sid, actor, h):
    pay = pay_row(conn, sid)
    bal = wallet.balance(conn, "acc-consumer")
    if bal < pay["amount"]:
        short = pay["amount"] - bal
        conn.execute("UPDATE handovers SET short_by=? WHERE id=?", (short, h["id"]))
        log(conn, sid, system_actor(), "WALLET_SHORT", {"balance": bal, "short_by": short, "agent_id": h["agent_id"]})


@app.post("/shipments/{sid}/door")
def at_the_door(sid: str, actor=Depends(me), conn=Depends(txn)):
    """The delivery person says they are physically at the door. Until this happens the buyer cannot pay and the seal QR does nothing."""
    require(actor, "DELIVERY")
    tick(conn)
    st = conn.execute("SELECT state FROM shipments WHERE id=?", (sid,)).fetchone()["state"]
    if st != "AWAITING_CONSUMER":
        raise HTTPException(409, "This parcel is not ready for the door yet." if st == "IN_TRANSIT" else f"Nothing to hand over: the parcel is {st.lower().replace('_', ' ')}.")
    if live_handover(conn, sid):
        raise HTTPException(409, "A handover is already open.")
    hid = str(uuid.uuid4())
    conn.execute("INSERT INTO handovers (id, shipment_id, state, agent_id, opened_at, expires_at) VALUES (?,?,?,?,?,?)",
                 (hid, sid, "OPEN", actor["id"], ledger.now_iso(), iso_in(setting_int(conn, "door_window_sec", 900))))
    h = conn.execute("SELECT * FROM handovers WHERE id=?", (hid,)).fetchone()
    log(conn, sid, actor, "DOOR_ARRIVED", {"agent": actor["name"]})
    door_events(conn, sid, actor, h)
    return done(conn, sid)


class PayBody(BaseModel):
    pin: str = ""
    override: bool = False
    note: str = ""


def check_ready_to_pay(conn, sid, b):
    cp = dict(conn.execute("SELECT * FROM checkpoints WHERE shipment_id=? AND role='CONSUMER'", (sid,)).fetchone())
    require_open(cp)
    problems = readiness(conn, cp)
    failing = [p for p in problems if "failed a check" in p]
    if [p for p in problems if p not in failing]:
        raise HTTPException(409, "Cannot pay yet: " + "; ".join(problems))
    if failing and not (b.override and b.note.strip()):
        raise HTTPException(409, "Cannot accept: " + "; ".join(failing) + ". Raise a dispute, or accept anyway if you have checked it yourself.")
    return cp, failing


@app.post("/shipments/{sid}/accept")
def accept(sid: str, b: PayBody | None = None, actor=Depends(me), conn=Depends(txn)):
    """Buyer checked the parcel and pays from the wallet. The money is RESERVED, not paid: the seller is only paid when the
       delivery person enters the one-time code that proves the parcel was physically handed over."""
    require(actor, "CONSUMER")
    b = b or PayBody()
    tick(conn)
    cp, failing = check_ready_to_pay(conn, sid, b)
    h = live_handover(conn, sid)
    if not h or h["state"] != "OPEN":
        raise HTTPException(409, "Payment opens when the delivery person is at your door. Scanning the seal on its own never pays." if not h
                            else "You have already paid. Show your handover code to the delivery person.")
    if pay_row(conn, sid)["state"] != "PENDING":
        raise HTTPException(409, "This order is not waiting for payment.")
    if not wallet.pin_ok(conn, actor["id"], b.pin):
        raise HTTPException(403, "Wrong wallet PIN")
    pay = pay_row(conn, sid)
    bal = wallet.balance(conn, actor["id"])
    if bal < pay["amount"]:  # the alarm: tell the buyer exactly what is missing and tell the delivery person to call
        short = pay["amount"] - bal
        conn.execute("UPDATE handovers SET short_by=? WHERE id=?", (short, h["id"]))
        log(conn, sid, system_actor(), "WALLET_SHORT", {"balance": bal, "short_by": short, "agent_id": h["agent_id"]})
        fail_loud(conn, sid, 402, f"Your wallet has ₹{bal:,.0f} and this parcel is ₹{pay['amount']:,.0f}. You are short by ₹{short:,.0f}. Add money, or call the delivery person.")
    code = f"{secrets.randbelow(10**6):06d}"
    ref = wallet.move(conn, actor["id"], "RESERVE", -pay["amount"], "Reserved for delivery. Not paid to the seller until the handover is confirmed.", sid)
    conn.execute("UPDATE handovers SET state='PAID', code=?, attempts=0, short_by=0, reserve_expires_at=?, override=?, override_note=? WHERE id=?",
                 (code, iso_in(setting_int(conn, "code_window_sec", 300)), int(bool(failing)), b.note.strip() if failing else "", h["id"]))
    set_payment_state(conn, sid, "RESERVED", actor, "Buyer paid from the wallet. Waiting for the handover code.", ref)
    log(conn, sid, actor, "PAYMENT_RESERVED", {"amount": pay["amount"], "agent_id": h["agent_id"], "balance": wallet.balance(conn, actor["id"])})
    return done(conn, sid)


@app.get("/shipments/{sid}/handover/code")
def handover_code(sid: str, actor=Depends(me), conn=Depends(txn)):
    require(actor, "CONSUMER")
    tick(conn)
    h = handover_row(conn, sid)
    if not h or h["state"] != "PAID":
        raise HTTPException(409, "There is no code: you have not paid, or the time ran out.")
    conn.commit()
    return {"code": h["code"], "expires_at": h["reserve_expires_at"]}


class CodeBody(BaseModel):
    code: str


@app.post("/shipments/{sid}/handover/confirm")
def confirm_handover(sid: str, b: CodeBody, actor=Depends(me), conn=Depends(txn)):
    """The delivery person types the code the buyer shows. Only now does the seller get paid and the parcel count as delivered."""
    require(actor, "DELIVERY")
    tick(conn)
    h = handover_row(conn, sid)
    if not h or h["state"] != "PAID":
        raise HTTPException(409, "Nothing to confirm. The buyer has not paid, or the payment time ran out and the money went back to their wallet.")
    if h["agent_id"] != actor["id"]:
        raise HTTPException(403, "This handover belongs to another delivery person.")
    if not secrets.compare_digest(h["code"], b.code.strip()):
        n = h["attempts"] + 1
        conn.execute("UPDATE handovers SET attempts=? WHERE id=?", (n, h["id"]))
        log(conn, sid, actor, "CODE_WRONG", {"attempt": n})
        if n >= 3:  # a stranger guessing, or someone who is not the buyer: undo the payment and stop
            reverse_reservation(conn, sid, system_actor(), "Three wrong handover codes. The payment was returned to your wallet.")
            close_handover(conn, h, "FAILED", system_actor(), "Three wrong handover codes", "HANDOVER_CANCELLED", {"reason": "wrong code"})
            fail_loud(conn, sid, 409, "Three wrong codes. The handover is cancelled and the buyer's money went back to their wallet.")
        fail_loud(conn, sid, 409, f"That code is not right. {3 - n} {'try' if 3 - n == 1 else 'tries'} left.")
    pay = pay_row(conn, sid)
    conn.execute("UPDATE handovers SET state='CONFIRMED' WHERE id=?", (h["id"],))
    payload = {"stage": "CONSUMER", "by": "handover code", "agent": actor["name"]}
    if h["override"]:
        payload |= {"override": True, "note": h["override_note"]}
    cp = dict(conn.execute("SELECT * FROM checkpoints WHERE shipment_id=? AND role='CONSUMER'", (sid,)).fetchone())
    log(conn, sid, actor, "ACCEPTED", payload)
    set_checkpoint_state(conn, cp["id"], "LOCKED")
    buyer = conn.execute("SELECT id FROM accounts WHERE role='CONSUMER'").fetchone()["id"]
    conn.execute("UPDATE checkpoints SET locked_at=?, actor_id=? WHERE id=?", (ledger.now_iso(), buyer, cp["id"]))
    set_shipment_state(conn, sid, "ACCEPTED")
    ref = wallet.move(conn, "acc-seller", "PAYOUT", pay["amount"], "Buyer accepted the parcel and the handover was confirmed", sid)
    set_payment_state(conn, sid, "RELEASED", actor, "Handover confirmed with the buyer's code", ref)
    log(conn, sid, actor, "DELIVERY_CONFIRMED", {"agent": actor["name"], "amount": pay["amount"], "ref": ref})
    return done(conn, sid)


class CancelBody(BaseModel):
    reason: str = "other"


@app.post("/shipments/{sid}/handover/cancel")
def cancel_handover(sid: str, b: CancelBody, actor=Depends(me), conn=Depends(txn)):
    """Nobody to hand it to, buyer not answering, wrong person at the door: the parcel stays with the courier, money is reversed."""
    require(actor, "DELIVERY")
    tick(conn)
    h = live_handover(conn, sid)
    if not h:
        raise HTTPException(409, "There is no open handover to cancel.")
    labels = {"nobody_home": "Nobody was there to receive it", "unreachable": "The buyer could not be reached", "wrong_person": "The person at the door was not the buyer", "refused": "The buyer refused it", "other": "Delivery attempt cancelled"}
    why = labels.get(b.reason, labels["other"])
    if h["state"] == "PAID":
        reverse_reservation(conn, sid, actor, why + ". Your payment was returned to the wallet.")
    close_handover(conn, h, "CANCELLED", actor, why, "HANDOVER_CANCELLED", {"reason": b.reason})
    return done(conn, sid)


class DisputeBody(BaseModel):
    note: str
    category: str = "other"


@app.post("/shipments/{sid}/dispute")
def dispute(sid: str, b: DisputeBody, actor=Depends(me), conn=Depends(txn)):
    """The buyer reports a problem, at the door or after opening the parcel. Same path as the automatic complaint."""
    require(actor, "CONSUMER")
    tick(conn)
    st = conn.execute("SELECT state FROM shipments WHERE id=?", (sid,)).fetchone()["state"]
    if st not in ("AWAITING_CONSUMER", "ACCEPTED"):
        raise HTTPException(409, "There is already a complaint on this order." if st in ("DISPUTED", "UNDER_REVIEW") else "A complaint can be raised once the parcel is with you.")
    if pay_row(conn, sid)["state"] == "RESERVED":
        raise HTTPException(409, "You have already paid for this parcel. Ask the delivery person to cancel the handover first, or wait for the code to expire.")
    close = live_handover(conn, sid)
    if close:
        conn.execute("UPDATE handovers SET state='CANCELLED', note='The buyer opened a dispute' WHERE id=?", (close["id"],))
    file_complaint(conn, sid, actor, category=b.category, reason=b.note, source="reported by the buyer")
    return done(conn, sid)


# ---------- after delivery: the buyer opens the parcel and scans the product ----------
def unboxed(conn, sid) -> str:
    """none | verified | unsure"""
    last = conn.execute("SELECT event_type FROM events WHERE shipment_id=? AND event_type IN ('UNBOX_VERIFIED','UNBOX_UNSURE') ORDER BY sequence_no DESC", (sid,)).fetchone()
    return "none" if not last else "verified" if last["event_type"] == "UNBOX_VERIFIED" else "unsure"


@app.post("/shipments/{sid}/unbox")
async def unbox(sid: str, front: UploadFile = File(...), barcode: UploadFile | None = File(None), hint_code: str = Form(""), actor=Depends(auth_ro)):
    """The buyer has the parcel at home and scans the product's barcode (and photographs it). A match closes the order. A
       difference files a complaint on the spot, and if the evidence is strong the buyer is refunded without anyone's say-so."""
    require(actor, "CONSUMER")
    bdata = await barcode.read() if barcode else b""
    fdata = await front.read()
    ro = dbm.connect()
    try:
        s = ro.execute("SELECT * FROM shipments WHERE id=?", (sid,)).fetchone()
        if not s:
            raise HTTPException(404, "No such shipment")
        if s["state"] != "ACCEPTED":
            raise HTTPException(409, "There is already a complaint on this order." if s["state"] in ("DISPUTED", "UNDER_REVIEW") or s["state"].startswith("RESOLVED") else "The product can be checked once it has been delivered to you.")
        if unboxed(ro, sid) == "verified":
            raise HTTPException(409, "You already checked this product and it matched.")
        identity = json.loads(s["identity"] or "{}")
        ref = ro.execute("SELECT e.sha256 FROM evidence e JOIN checkpoints c ON c.id=e.checkpoint_id WHERE c.shipment_id=? AND c.role='SELLER' AND e.purpose='product_front' ORDER BY e.captured_at DESC", (sid,)).fetchone()
        scenario, mode = dbm.get_setting(ro, "scenario", "A"), ai_mode(ro)
    finally:
        ro.close()
    reference = (dbm.STORAGE / ref["sha256"]).read_bytes() if ref and (dbm.STORAGE / ref["sha256"]).exists() else None
    result = await run_in_threadpool(run_unbox, mode, scenario, bdata, fdata, reference, identity, hint_code)
    if result["outcome"] == "UNREADABLE":
        raise HTTPException(422, "The barcode could not be read. Hold it close inside the frame, flat and steady, with no glare, then try again.")
    fsha, bsha = ledger.sha256_hex(fdata), ledger.sha256_hex(bdata) if bdata else ""
    (dbm.STORAGE / fsha).write_bytes(fdata)
    if bdata:
        (dbm.STORAGE / bsha).write_bytes(bdata)

    def write():
        with locked() as conn:
            if conn.execute("SELECT state FROM shipments WHERE id=?", (sid,)).fetchone()["state"] != "ACCEPTED":
                raise HTTPException(409, "This order changed while the photo was being checked.")
            cp = dict(conn.execute("SELECT * FROM checkpoints WHERE shipment_id=? AND role='CONSUMER'", (sid,)).fetchone())
            refs, ids = [], {}
            for purpose, sha, data in (("unbox_front", fsha, fdata), ("unbox_barcode", bsha, bdata)):
                if data:
                    evid = str(uuid.uuid4()); ids[purpose] = evid
                    conn.execute("INSERT INTO evidence VALUES (?,?,?,?,?,?,?,?)", (evid, sid, cp["id"], "photo", purpose, sha, ledger.now_iso(), actor["id"]))
                    refs.append({"evidence_id": evid, "sha256": sha})
            for r in result["results"]:
                purpose = "unbox_barcode" if r["check_type"] == "identifier_match" and "unbox_barcode" in ids else "unbox_front"
                conn.execute("INSERT INTO ai_checks (id, shipment_id, checkpoint_id, evidence_id, purpose, check_type, status, score, threshold, explanation, model_name, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                             (str(uuid.uuid4()), sid, cp["id"], ids.get(purpose), purpose, r["check_type"], r["status"], r["score"], r["threshold"], r["explanation"], r["model_name"], ledger.now_iso()))
            out = result["outcome"]
            log(conn, sid, actor, "UNBOX_CHECKED", {"outcome": out, "read": result["read"],
                                                  "results": [{k: r[k] for k in ("check_type", "status", "score")} for r in result["results"]]}, refs)
            if out == "MATCH":
                log(conn, sid, system_actor(), "UNBOX_VERIFIED", {"how": "scan", "read": result["read"]})
            elif out == "UNSURE":
                log(conn, sid, system_actor(), "UNBOX_UNSURE", {"read": result["read"]})
            else:   # MISMATCH: the complaint is filed for the buyer, with everything the system knows attached
                tri = triage(conn, sid, result)
                bad = next((r for r in result["results"] if r["status"] == "FAIL"), result["results"][0])
                file_complaint(conn, sid, actor, category="wrong_item", reason=bad["explanation"], source="unboxing scan", tri=tri, auto=True)
            return done(conn, sid)

    return await run_in_threadpool(write)


class Confirm(BaseModel):
    note: str = ""


@app.post("/shipments/{sid}/unbox/confirm")
def unbox_confirm(sid: str, b: Confirm, actor=Depends(me), conn=Depends(txn)):
    """The system could not decide whether the product matches, so the buyer, who is holding it, says it is right."""
    require(actor, "CONSUMER")
    if conn.execute("SELECT state FROM shipments WHERE id=?", (sid,)).fetchone()["state"] != "ACCEPTED" or unboxed(conn, sid) != "unsure":
        raise HTTPException(409, "There is nothing to confirm.")
    log(conn, sid, actor, "UNBOX_VERIFIED", {"how": "buyer confirmed", "note": b.note.strip()})
    return done(conn, sid)


# ---------- reviewer ----------
class Decision(BaseModel):
    decision: str  # resume | recheck | reseal | return | refund | replace | reject
    reason: str
    instruction: str = ""


HOLD_DECISIONS = {"resume", "recheck", "reseal", "return", "refund"}
DISPUTE_DECISIONS = {"refund", "replace", "reject", "return"}


@app.get("/cases")
def cases(actor=Depends(me), conn=Depends(txn)):
    require(actor, "REVIEWER", "ADMIN")
    out = []
    for r in conn.execute("SELECT id, order_ref, product_name, state, created_at FROM shipments ORDER BY created_at DESC"):
        c = case_info(conn, r["id"])
        if c["open"]:
            out.append({**dict(r), "case": c})
    return out


@app.post("/review/{sid}/decision")
def review(sid: str, b: Decision, actor=Depends(me), conn=Depends(txn)):
    require(actor, "REVIEWER", "ADMIN")
    if not b.reason.strip():
        raise HTTPException(422, "A reason is required")
    case = case_info(conn, sid)
    if not case["open"]:
        raise HTTPException(409, "There is no open case on this shipment.")
    allowed = DISPUTE_DECISIONS if case["kind"] == "DISPUTE" else ({"resume", "return"} if case.get("handover") else HOLD_DECISIONS)
    if b.decision not in allowed:
        raise HTTPException(422, f"For this case the decision must be one of: {', '.join(sorted(allowed))}")
    if b.decision == "recheck" and not b.instruction.strip():
        raise HTTPException(422, "Tell the person what to recheck")
    if case["state"] in ("ON_HOLD", "DISPUTED"):
        set_shipment_state(conn, sid, "UNDER_REVIEW")
    cp = dict(conn.execute("SELECT * FROM checkpoints WHERE shipment_id=? AND role=?", (sid, case["stage"])).fetchone())
    payload = {"decision": b.decision, "reason": b.reason.strip(), "stage": case["stage"],
               "responsibility_suggested": case["stage"], "instruction": b.instruction.strip()}
    log(conn, sid, actor, "REVIEW_DECISION", payload)

    def reopen(*, scanned: bool):
        conn.execute("UPDATE handovers SET counted=0 WHERE shipment_id=?", (sid,))  # a reviewer's go-ahead gives the courier fresh attempts
        conn.execute("UPDATE ai_checks SET overridden=1 WHERE checkpoint_id=? AND status='FAIL'", (cp["id"],))
        if cp["state"] == "FAILED":
            set_checkpoint_state(conn, cp["id"], "IN_PROGRESS")
        conn.execute("UPDATE checkpoints SET seal_scanned=? WHERE id=?", (1 if scanned else 0, cp["id"]))
        set_shipment_state(conn, sid, resume_state(cp))

    if b.decision == "resume":
        reopen(scanned=True)  # the reviewer checked the parcel by hand
    elif b.decision == "recheck":
        set_shipment_state(conn, sid, "ON_HOLD")  # back to the stage owner, with fresh tries
    elif b.decision == "reseal":
        old = active_seal(conn, sid)
        conn.execute("UPDATE seals SET status='VOID' WHERE shipment_id=? AND status='ACTIVE'", (sid,))
        log(conn, sid, actor, "SEAL_REPLACED", {"old": seal_hint(old["seal_code"]) if old else None})
        reopen(scanned=False)  # a new physical seal goes on, and whoever holds the parcel scans it to register it
    elif b.decision == "return":
        set_shipment_state(conn, sid, "RETURNED")
        cancel_payment(conn, sid, actor, "Parcel returned to the seller: " + b.reason)
    elif b.decision == "refund":
        set_shipment_state(conn, sid, "RESOLVED_REFUND")
        cancel_payment(conn, sid, actor, b.reason)
    elif b.decision == "reject":
        set_shipment_state(conn, sid, "RESOLVED_REJECTED")
        charge_due(conn, sid, actor, b.reason)
    elif b.decision == "replace":
        set_shipment_state(conn, sid, "RESOLVED_REPLACEMENT")  # funds stay held for the replacement parcel
    return done(conn, sid)


class ScenarioBody(BaseModel):
    scenario: str


class WalletSet(BaseModel):
    balance: float
    door_window_sec: int | None = None
    code_window_sec: int | None = None


@app.post("/admin/wallet")
def admin_wallet(b: WalletSet, actor=Depends(me), conn=Depends(txn)):
    """Presenter: set the buyer's balance (to show the short-balance alarm) and shorten the time windows for the demo."""
    require(actor, "ADMIN")
    wallet.ensure(conn)
    conn.execute("UPDATE wallets SET balance=? WHERE account_id='acc-consumer'", (b.balance,))
    if b.door_window_sec:
        dbm.set_setting(conn, "door_window_sec", str(b.door_window_sec))
    if b.code_window_sec:
        dbm.set_setting(conn, "code_window_sec", str(b.code_window_sec))
    conn.commit()
    return {"balance": b.balance, "door_window_sec": setting_int(conn, "door_window_sec", 900), "code_window_sec": setting_int(conn, "code_window_sec", 300)}


class Flag(BaseModel):
    on: bool


@app.post("/admin/rehearsal")
def rehearsal(b: Flag, actor=Depends(me), conn=Depends(txn)):
    """Rehearsal lets one browser hold several people (one per tab) while you test on a single laptop."""
    require(actor, "ADMIN")
    dbm.set_setting(conn, "rehearsal", "1" if b.on else "0")
    conn.commit()
    hub.notify(None)
    return {"rehearsal": b.on}


@app.post("/admin/ai-mode")
def set_ai_mode(b: ScenarioBody, actor=Depends(me), conn=Depends(txn)):
    require(actor, "ADMIN")
    if b.scenario not in ("live", "mock"):
        raise HTTPException(422, "mode must be live or mock")
    dbm.set_setting(conn, "ai_mode", b.scenario)
    conn.commit()
    hub.notify(None)
    return {"ai_mode": b.scenario}


@app.post("/admin/signout-all")
def signout_all(actor=Depends(me), conn=Depends(txn)):
    require(actor, "ADMIN")
    conn.execute("DELETE FROM sessions WHERE participant_id!=?", (actor["id"],))
    conn.commit()
    return {"ok": True}


@app.post("/admin/scenario")
def scenario(b: ScenarioBody, actor=Depends(me), conn=Depends(txn)):
    require(actor, "ADMIN")
    if b.scenario not in ("A", "B", "C"):
        raise HTTPException(422, "scenario must be A, B or C")
    dbm.set_setting(conn, "scenario", b.scenario)
    conn.commit()
    hub.notify(None)
    return {"scenario": b.scenario}


@app.post("/admin/reset")
def reset(actor=Depends(me), conn=Depends(txn)):
    require(actor, "ADMIN")
    with dbm.unguarded(conn):
        for t in ("events", "ai_checks", "evidence", "payments", "payment_txns", "notifications", "seals", "reads", "checkpoints", "shipments"):
            conn.execute(f"DELETE FROM {t}")
    conn.execute("DELETE FROM settings WHERE key LIKE 'tamper_%'")
    conn.execute("DELETE FROM handovers")
    wallet.reset(conn)
    conn.commit()
    hub.last_nid = 0
    hub.notify(None)
    return {"ok": True}


@app.post("/admin/seed")
def seed(actor=Depends(me), conn=Depends(txn)):
    require(actor, "ADMIN")
    seller = {"id": "acc-seller", "role": "SELLER", "name": "Riya Sharma"}
    sid = create_shipment(conn, seller, CreateShipment(order_ref="ORD-1042", product_name="Green carpet",
                                                       category="home", declared_value=4200, expected_colour="dark green",
                                                       identity={"product_name": "Green carpet", "brand": "", "category": "home",
                                                                 "colours": ["dark green"], "confidence": 1.0, "model": "seeded"}))
    conn.commit()
    hub.notify(sid)
    return {"shipment_id": sid, "seal": "TS-TEST-0001", "spare_seal": "TS-TEST-0002"}   # serials a test "reads" off a sticker


@app.post("/admin/tamper-demo/{sid}")
def tamper(sid: str, actor=Depends(me), conn=Depends(txn)):
    require(actor, "ADMIN")
    row = conn.execute("SELECT event_id, payload FROM events WHERE shipment_id=? AND sequence_no=2", (sid,)).fetchone()
    if not row:
        raise HTTPException(404, "Shipment has fewer than 2 events")
    dbm.set_setting(conn, f"tamper_{sid}", json.dumps({"event_id": row["event_id"], "payload": row["payload"]}))
    with dbm.unguarded(conn):
        conn.execute("UPDATE events SET payload=? WHERE event_id=?", ('{"seal_code":"TS-FAKE-0000"}', row["event_id"]))
    return done(conn, sid)


@app.post("/admin/tamper-restore/{sid}")
def untamper(sid: str, actor=Depends(me), conn=Depends(txn)):
    require(actor, "ADMIN")
    saved = dbm.get_setting(conn, f"tamper_{sid}", "")
    if saved:
        s = json.loads(saved)
        with dbm.unguarded(conn):
            conn.execute("UPDATE events SET payload=? WHERE event_id=?", (s["payload"], s["event_id"]))
        conn.execute("DELETE FROM settings WHERE key=?", (f"tamper_{sid}",))
    return done(conn, sid)


# ---------- notifications ----------
@app.get("/notifications")
def my_notifications(actor=Depends(auth_ro)):
    conn = dbm.connect()
    try:
        rows = [dict(r) for r in conn.execute("SELECT * FROM notifications WHERE to_id=? ORDER BY id DESC LIMIT 100", (actor["id"],))]
    finally:
        conn.close()
    return {"items": rows, "unread": sum(1 for r in rows if not r["read_at"])}


@app.post("/notifications/read")
def read_notifications(actor=Depends(me), conn=Depends(txn)):
    conn.execute("UPDATE notifications SET read_at=? WHERE to_id=? AND read_at IS NULL", (ledger.now_iso(), actor["id"]))
    conn.commit()
    return {"ok": True}


@app.get("/admin/notifications")
def all_notifications(actor=Depends(me), conn=Depends(txn)):
    require(actor, "ADMIN", "REVIEWER")
    return [dict(r) for r in conn.execute("SELECT * FROM notifications ORDER BY id DESC LIMIT 200")]


# ---------- websocket ----------
@app.websocket("/ws")
async def ws(socket: WebSocket):
    await socket.accept()
    hub.clients.add(socket)
    try:
        while True:
            await socket.receive_text()
    except WebSocketDisconnect:
        hub.clients.discard(socket)
