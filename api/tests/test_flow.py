import os
import tempfile

os.environ["TRUSTSEAL_DATA"] = tempfile.mkdtemp()

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import ledger  # noqa: E402
from app.main import app  # noqa: E402
from app.state_machine import CHECKPOINT_TRANSITIONS, SHIPMENT_TRANSITIONS  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"demo-bytes"


@pytest.fixture()
def world():
    with TestClient(app) as c:
        PINS = {"SELLER": "1111", "WAREHOUSE": "2222", "DELIVERY": "3333", "CONSUMER": "4444", "REVIEWER": "5555", "ADMIN": "0000"}

        def join(role):
            r = c.post("/auth/join", json={"account_id": "acc-" + role.lower(), "pin": PINS[role], "device_id": "dev-" + role})
            return {"x-token": r.json()["token"]}
        h = {"ADMIN": join("ADMIN")}
        c.post("/admin/signout-all", headers=h["ADMIN"])
        c.post("/admin/rehearsal", json={"on": False}, headers=h["ADMIN"])
        h.update({r: join(r) for r in PINS if r != "ADMIN"})
        c.post("/admin/reset", headers=h["ADMIN"])
        c.post("/admin/scenario", json={"scenario": "A"}, headers=h["ADMIN"])
        c.post("/admin/ai-mode", json={"scenario": "mock"}, headers=h["ADMIN"])
        c.post("/admin/wallet", json={"balance": 10000, "door_window_sec": 900, "code_window_sec": 300}, headers=h["ADMIN"])
        seed = c.post("/admin/seed", headers=h["ADMIN"]).json()
        yield c, h, seed
        
        
def snap(c, sid):
    return c.get(f"/shipments/{sid}").json()


def cp_of(s, role):
    return next(x for x in s["checkpoints"] if x["role"] == role)


def shoot(c, h, role, sid, purposes):
    for p in purposes:
        cp = cp_of(snap(c, sid), role)
        r = c.post(f"/checkpoints/{cp['id']}/evidence", headers=h[role],
                   data={"purpose": p, "client_sha256": ledger.sha256_hex(PNG), "kind": "video" if p == "packing_video" else "photo"},
                   files={"file": ("x.png", PNG, "image/png")})
        assert r.status_code == 200, r.text


SEAL = "TS-TEST-0001"   # what a phone reads off the sticker; the system never issues it
SPARE = "TS-TEST-0002"


def scan(c, h, role, sid, code=None):
    s = snap(c, sid)
    return c.post(f"/checkpoints/{cp_of(s, role)['id']}/scan", headers=h[role],
                  json={"seal_code": code or SEAL})


def pack(c, h, sid):
    """The seller photographs the product, scans the seal on the parcel (which registers it) and films the packing."""
    shoot(c, h, "SELLER", sid, ["product_front", "product_side"])
    assert scan(c, h, "SELLER", sid).status_code == 200
    shoot(c, h, "SELLER", sid, ["seal_side", "packing_video"])


def qr_png(text):
    import io
    import zxingcpp
    from PIL import Image
    img = Image.fromarray(zxingcpp.write_barcode(zxingcpp.BarcodeFormat.QRCode, text, 300, 300)).convert("RGB")
    b = io.BytesIO(); img.save(b, "PNG")
    return b.getvalue()


def photo(c, h, role, sid, purpose, data):
    cp = cp_of(snap(c, sid), role)
    return c.post(f"/checkpoints/{cp['id']}/evidence", headers=h[role],
                  data={"purpose": purpose, "client_sha256": ledger.sha256_hex(data), "kind": "photo"}, files={"file": ("x.png", data, "image/png")})


def lock(c, h, role, sid):
    return c.post(f"/checkpoints/{cp_of(snap(c, sid), role)['id']}/lock", headers=h[role])


def run_to_consumer(c, h, sid):
    pack(c, h, sid)
    assert lock(c, h, "SELLER", sid).status_code == 200
    scan(c, h, "WAREHOUSE", sid); shoot(c, h, "WAREHOUSE", sid, ["seal_side"])
    assert lock(c, h, "WAREHOUSE", sid).status_code == 200
    scan(c, h, "DELIVERY", sid); shoot(c, h, "DELIVERY", sid, ["seal_side", "parcel"])
    assert lock(c, h, "DELIVERY", sid).status_code == 200


def unbox(c, h, sid, barcode=True):
    files = {"front": ("f.png", PNG, "image/png")}
    if barcode:
        files["barcode"] = ("b.png", PNG + b"1", "image/png")
    return c.post(f"/shipments/{sid}/unbox", headers=h["CONSUMER"], files=files)


def barcode_order(c, h, value=500, code="9788119153565", verdict="match"):
    """An order whose barcode was read by the camera: the server remembers what it read, keyed by the photo's hash."""
    import json
    from app import db
    sha = ledger.sha256_hex(b"barcode-photo-" + code.encode())
    conn = db.connect()
    conn.execute("INSERT OR REPLACE INTO reads (sha256, kind, data, created_at) VALUES (?,?,?,?)",
                 (sha, "barcode", json.dumps({"barcode": {"code": code, "format": "EAN13", "isbn": True, "how": "barcode"},
                                               "verdict": {"status": verdict, "detail": "test"}}), "2026-01-01T00:00:00.000+00:00"))
    conn.commit(); conn.close()
    return c.post("/shipments", headers=h["SELLER"], json={"order_ref": "ORD-2001", "product_name": "A Book", "declared_value": value,
                                                           "identity": {"product_name": "A Book", "confidence": 1, "barcode": {"code": "0"}, "barcode_sha256": sha}})


PIN = "2580"


def door(c, h, sid):
    return c.post(f"/shipments/{sid}/door", headers=h["DELIVERY"])


def accept(c, h, sid, **body):
    return c.post(f"/shipments/{sid}/accept", headers=h["CONSUMER"], json={"pin": PIN, **body})


def code_of(c, h, sid):
    return c.get(f"/shipments/{sid}/handover/code", headers=h["CONSUMER"]).json()["code"]


def confirm(c, h, sid, code):
    return c.post(f"/shipments/{sid}/handover/confirm", headers=h["DELIVERY"], json={"code": code})


def hand_over(c, h, sid, **body):
    """The whole doorstep handshake: courier at the door, buyer pays, buyer shows the code, courier confirms."""
    assert door(c, h, sid).status_code == 200
    r = accept(c, h, sid, **body)
    assert r.status_code == 200, r.text
    return confirm(c, h, sid, code_of(c, h, sid))


def wallet_of(c, h, role):
    return c.get("/wallet", headers=h[role]).json()["balance"]


def test_happy_path_accepts_and_releases_payment(world):
    c, h, seed = world; sid = seed["shipment_id"]
    run_to_consumer(c, h, sid)
    assert snap(c, sid)["shipment"]["state"] == "AWAITING_CONSUMER"
    scan(c, h, "CONSUMER", sid)
    assert hand_over(c, h, sid).status_code == 200
    s = snap(c, sid)
    assert s["shipment"]["state"] == "ACCEPTED" and s["payment"]["state"] == "RELEASED"
    assert wallet_of(c, h, "SELLER") == 4200 and wallet_of(c, h, "CONSUMER") == 10000 - 4200
    assert s["chain"]["ok"]
    s = unbox(c, h, sid).json()                      # at home: the product matches, order closed
    assert s["shipment"]["state"] == "ACCEPTED" and any(e["event_type"] == "UNBOX_VERIFIED" for e in s["events"]) and not s["complaint"]


def test_locked_stage_rejects_edits(world):
    c, h, seed = world; sid = seed["shipment_id"]
    pack(c, h, sid)
    lock(c, h, "SELLER", sid)
    cp = cp_of(snap(c, sid), "SELLER")
    r = c.post(f"/checkpoints/{cp['id']}/evidence", headers=h["SELLER"],
               data={"purpose": "product_front", "client_sha256": ledger.sha256_hex(PNG)},
               files={"file": ("x.png", PNG, "image/png")})
    assert r.status_code == 409 and "locked" in r.json()["detail"]
    assert lock(c, h, "SELLER", sid).status_code == 409


def test_cannot_skip_stage_or_cross_roles(world):
    c, h, seed = world; sid = seed["shipment_id"]
    wh = cp_of(snap(c, sid), "WAREHOUSE")
    assert c.post(f"/checkpoints/{wh['id']}/scan", headers=h["WAREHOUSE"], json={"seal_code": "x"}).status_code == 409
    assert c.post(f"/checkpoints/{wh['id']}/lock", headers=h["SELLER"]).status_code == 403
    assert c.post("/shipments", headers=h["DELIVERY"], json={"order_ref": "a", "product_name": "b", "declared_value": 1}).status_code == 403


def test_lock_needs_all_evidence(world):
    c, h, seed = world; sid = seed["shipment_id"]
    shoot(c, h, "SELLER", sid, ["product_front"])
    r = lock(c, h, "SELLER", sid)
    assert r.status_code == 409 and "missing" in r.json()["detail"]


def test_scenario_b_wrong_seal_puts_shipment_on_hold(world):
    c, h, seed = world; sid = seed["shipment_id"]
    pack(c, h, sid); lock(c, h, "SELLER", sid)
    r = scan(c, h, "WAREHOUSE", sid, code=SPARE)
    s = r.json()
    assert s["shipment"]["state"] == "ON_HOLD"
    assert cp_of(s, "WAREHOUSE")["state"] == "FAILED"
    hold = next(e for e in s["events"] if e["event_type"] == "HOLD_PLACED")
    assert hold["payload"]["last_verified_stage"] == "SELLER" and hold["payload"]["failed_stage"] == "WAREHOUSE"
    assert lock(c, h, "WAREHOUSE", sid).status_code == 409


def test_scenario_b_tampered_seal_photo_holds(world):
    c, h, seed = world; sid = seed["shipment_id"]
    c.post("/admin/scenario", json={"scenario": "B"}, headers=h["ADMIN"])
    pack(c, h, sid); lock(c, h, "SELLER", sid)
    scan(c, h, "WAREHOUSE", sid); shoot(c, h, "WAREHOUSE", sid, ["seal_side"])
    assert snap(c, sid)["shipment"]["state"] == "ON_HOLD"


def delivered(c, h, sid):
    run_to_consumer(c, h, sid)
    scan(c, h, "CONSUMER", sid)
    assert hand_over(c, h, sid).status_code == 200


def test_a_wrong_product_found_at_home_files_a_complaint_for_the_buyer(world):
    c, h, seed = world; sid = seed["shipment_id"]
    c.post("/admin/scenario", json={"scenario": "C"}, headers=h["ADMIN"])
    delivered(c, h, sid)
    s = unbox(c, h, sid).json()
    # one weak signal (the look of it) with no barcode on record: filed, but a person decides
    assert s["shipment"]["state"] == "DISPUTED" and s["complaint"]["ticket"] == "CMP-1042" and s["complaint"]["auto"] and not s["complaint"]["auto_resolved"]
    assert s["case"]["open"] and s["case"]["kind"] == "DISPUTE" and s["payment"]["state"] == "RELEASED"
    assert wallet_of(c, h, "CONSUMER") == 10000 - 4200 and s["chain"]["ok"]
    s = c.post(f"/review/{sid}/decision", headers=h["REVIEWER"], json={"decision": "refund", "reason": "Photos show the wrong rug"}).json()
    assert s["payment"]["state"] == "REFUNDED" and wallet_of(c, h, "CONSUMER") == 10000 and wallet_of(c, h, "SELLER") == 0


def test_strong_evidence_refunds_the_buyer_automatically(world):
    c, h, seed = world
    c.post("/admin/scenario", json={"scenario": "C"}, headers=h["ADMIN"])
    sid = barcode_order(c, h, value=500).json()["shipment"]["id"]
    delivered(c, h, sid)
    assert wallet_of(c, h, "SELLER") == 500
    s = unbox(c, h, sid).json()                      # the barcode and the look both differ
    assert s["shipment"]["state"] == "RESOLVED_REFUND" and s["payment"]["state"] == "REFUNDED"
    assert s["complaint"]["auto_resolved"] and s["complaint"]["decision"]["auto"] and s["complaint"]["status"] == "CLOSED"
    assert wallet_of(c, h, "CONSUMER") == 10000 and wallet_of(c, h, "SELLER") == 0 and s["chain"]["ok"]
    assert any("refunded automatically" in n["subject"] for n in inbox(c, h, "CONSUMER"))
    assert any("wrong item" in n["subject"] for n in inbox(c, h, "SELLER"))
    assert not any(n["event_type"] == "DISPUTE_OPENED" for n in inbox(c, h, "WAREHOUSE"))      # warehouse is not told


def test_big_orders_are_not_refunded_by_a_machine(world):
    c, h, seed = world
    c.post("/admin/scenario", json={"scenario": "C"}, headers=h["ADMIN"])
    sid = barcode_order(c, h, value=9000).json()["shipment"]["id"]
    delivered(c, h, sid)
    s = unbox(c, h, sid).json()
    assert s["shipment"]["state"] == "DISPUTED" and not s["complaint"]["auto_resolved"] and "limit" in s["complaint"]["triage"]["cause"]
    assert s["payment"]["state"] == "RELEASED"       # a person has to confirm before money moves


def test_a_buyer_who_cannot_tell_confirms_it_themselves(world):
    c, h, seed = world; sid = seed["shipment_id"]
    delivered(c, h, sid)
    assert c.post(f"/shipments/{sid}/unbox/confirm", headers=h["CONSUMER"], json={}).status_code == 409   # nothing unsure yet
    assert unbox(c, h, sid).json()["shipment"]["state"] == "ACCEPTED"
    assert c.post(f"/shipments/{sid}/unbox", headers=h["SELLER"], files={"front": ("f.png", PNG, "image/png")}).status_code == 403


def test_the_product_can_only_be_checked_after_delivery(world):
    c, h, seed = world; sid = seed["shipment_id"]
    assert unbox(c, h, sid).status_code == 409
    run_to_consumer(c, h, sid)
    assert unbox(c, h, sid).status_code == 409       # still with the courier: nothing is checked, nothing is paid


def test_reviewer_can_resume_a_hold(world):
    c, h, seed = world; sid = seed["shipment_id"]
    pack(c, h, sid); lock(c, h, "SELLER", sid)
    scan(c, h, "WAREHOUSE", sid, code=SPARE)
    r = c.post(f"/review/{sid}/decision", headers=h["ADMIN"], json={"decision": "resume", "reason": "Wrong label scanned by mistake"})
    assert r.status_code == 200 and r.json()["shipment"]["state"] == "IN_TRANSIT"
    assert scan(c, h, "WAREHOUSE", sid).status_code == 200


def test_chain_verifier_catches_db_tampering_and_restore(world):
    c, h, seed = world; sid = seed["shipment_id"]
    pack(c, h, sid)
    assert snap(c, sid)["chain"]["ok"]
    s = c.post(f"/admin/tamper-demo/{sid}", headers=h["ADMIN"]).json()
    assert not s["chain"]["ok"] and s["chain"]["broken_at"] == 2
    assert c.post(f"/admin/tamper-restore/{sid}", headers=h["ADMIN"]).json()["chain"]["ok"]


def test_events_table_is_append_only(world):
    import sqlite3
    from app import db
    conn = db.connect()
    with pytest.raises(sqlite3.DatabaseError):
        conn.execute("DELETE FROM events")
    conn.close()


def test_bad_hash_upload_rejected(world):
    c, h, seed = world; sid = seed["shipment_id"]
    cp = cp_of(snap(c, sid), "SELLER")
    r = c.post(f"/checkpoints/{cp['id']}/evidence", headers=h["SELLER"],
               data={"purpose": "product_front", "client_sha256": "0" * 64}, files={"file": ("x.png", PNG, "image/png")})
    assert r.status_code == 422


def test_every_transition_table_is_closed():
    assert SHIPMENT_TRANSITIONS["ACCEPTED"] == {"DISPUTED"} and SHIPMENT_TRANSITIONS["RESOLVED_REFUND"] == set() and CHECKPOINT_TRANSITIONS["LOCKED"] == set()
    for table in (SHIPMENT_TRANSITIONS, CHECKPOINT_TRANSITIONS):
        for targets in table.values():
            assert targets <= set(table)


def test_one_device_is_one_person(world):
    c, h, seed = world
    r = c.post("/auth/join", json={"account_id": "acc-warehouse", "pin": "2222", "device_id": "dev-SELLER"})
    assert r.status_code == 409 and "already signed in as Riya Sharma" in r.json()["detail"]
    r = c.post("/auth/join", json={"account_id": "acc-seller", "pin": "1111", "device_id": "other-phone"})
    assert r.status_code == 409 and "another device" in r.json()["detail"]
    assert c.post("/auth/join", json={"account_id": "acc-seller", "pin": "0000", "device_id": "x"}).status_code == 403


def test_rehearsal_mode_lets_one_device_hold_several_people(world):
    c, h, seed = world
    c.post("/admin/rehearsal", json={"on": True}, headers=h["ADMIN"])
    r = c.post("/auth/join", json={"account_id": "acc-warehouse", "pin": "2222", "device_id": "dev-SELLER"})
    assert r.status_code == 200
    c.post("/admin/rehearsal", json={"on": False}, headers=h["ADMIN"])


def test_sign_out_frees_the_device(world):
    c, h, seed = world
    c.post("/auth/leave", headers=h["SELLER"])
    r = c.post("/auth/join", json={"account_id": "acc-warehouse", "pin": "2222", "device_id": "dev-SELLER"})
    assert r.status_code == 409  # warehouse is still signed in on its own device
    c.post("/admin/signout-all", headers=h["ADMIN"])
    assert c.post("/auth/join", json={"account_id": "acc-warehouse", "pin": "2222", "device_id": "dev-SELLER"}).status_code == 200


def test_identified_product_is_registered_with_its_reference_photo(world):
    c, h, seed = world
    sha = ledger.sha256_hex(PNG)
    from app import db
    (db.STORAGE / sha).write_bytes(PNG)
    body = {"order_ref": "ORD-9", "product_name": "Lay's Classic Salted", "declared_value": 20,
            "identity": {"product_name": "Lay's Classic Salted", "brand": "Lay's", "confidence": 0.93, "model": "gemma3:4b"},
            "reference_sha256": sha}
    s = c.post("/shipments", headers=h["SELLER"], json=body).json()
    assert s["shipment"]["identity"]["brand"] == "Lay's"
    seller = s["checkpoints"][0]
    assert [e["purpose"] for e in seller["evidence"]] == ["product_front"]
    assert any(e["event_type"] == "PRODUCT_IDENTIFIED" for e in s["events"])
    assert s["chain"]["ok"]


def test_seller_only_can_identify(world):
    c, h, seed = world
    r = c.post("/identify", headers=h["DELIVERY"], files={"file": ("x.png", PNG, "image/png")})
    assert r.status_code == 403
    r = c.post("/identify", headers=h["SELLER"], files={"file": ("x.png", PNG, "image/png")})
    assert r.status_code == 200 and r.json()["identity"]["product_name"]  # mock mode in tests


# ---------- case resolution ----------
def to_warehouse_hold(c, h, sid, seed):
    pack(c, h, sid); lock(c, h, "SELLER", sid)
    return scan(c, h, "WAREHOUSE", sid, code=SPARE).json()


def test_stage_owner_fixes_a_wrong_scan_by_rescanning(world):
    c, h, seed = world; sid = seed["shipment_id"]
    s = to_warehouse_hold(c, h, sid, seed)
    assert s["shipment"]["state"] == "ON_HOLD" and s["case"]["attempts_left"] == 3 and s["case"]["waiting_on"]["role"] == "WAREHOUSE"
    s = scan(c, h, "WAREHOUSE", sid).json()  # the right seal this time
    assert s["shipment"]["state"] == "IN_TRANSIT" and not s["case"]["open"]
    assert cp_of(s, "WAREHOUSE")["state"] == "IN_PROGRESS" and cp_of(s, "WAREHOUSE")["seal_scanned"] == 1
    assert any(e["event_type"] == "HOLD_CLEARED" for e in s["events"]) and s["chain"]["ok"]


def test_three_wrong_tries_escalate_to_a_reviewer(world):
    c, h, seed = world; sid = seed["shipment_id"]
    to_warehouse_hold(c, h, sid, seed)
    for _ in range(3):
        s = scan(c, h, "WAREHOUSE", sid, code=SPARE).json()
    assert s["shipment"]["state"] == "UNDER_REVIEW" and s["case"]["waiting_on"]["role"] == "REVIEWER"
    assert scan(c, h, "WAREHOUSE", sid).status_code == 409  # a reviewer owns it now


def test_owner_can_report_straight_to_reviewer(world):
    c, h, seed = world; sid = seed["shipment_id"]
    s = to_warehouse_hold(c, h, sid, seed)
    r = c.post(f"/checkpoints/{cp_of(s, 'WAREHOUSE')['id']}/exception", headers=h["WAREHOUSE"], json={"note": "Box looks reopened"})
    assert r.json()["shipment"]["state"] == "UNDER_REVIEW"
    cases = c.get("/cases", headers=h["REVIEWER"]).json()
    assert cases[0]["id"] == sid and cases[0]["case"]["stage"] == "WAREHOUSE"
    assert c.get("/cases", headers=h["SELLER"]).status_code == 403


def escalate_hold(c, h, sid, seed):
    s = to_warehouse_hold(c, h, sid, seed)
    c.post(f"/checkpoints/{cp_of(s, 'WAREHOUSE')['id']}/exception", headers=h["WAREHOUSE"], json={"note": "x"})


def test_reviewer_recheck_sends_it_back_with_fresh_tries(world):
    c, h, seed = world; sid = seed["shipment_id"]
    escalate_hold(c, h, sid, seed)
    assert c.post(f"/review/{sid}/decision", headers=h["REVIEWER"], json={"decision": "recheck", "reason": "Unclear"}).status_code == 422
    s = c.post(f"/review/{sid}/decision", headers=h["REVIEWER"], json={"decision": "recheck", "reason": "Unclear photo", "instruction": "Scan the label again in good light"}).json()
    assert s["shipment"]["state"] == "ON_HOLD" and s["case"]["instruction"].startswith("Scan the label") and s["case"]["attempts_left"] == 3
    assert scan(c, h, "WAREHOUSE", sid).json()["shipment"]["state"] == "IN_TRANSIT"


def test_reviewer_reseal_voids_the_old_seal(world):
    c, h, seed = world; sid = seed["shipment_id"]
    escalate_hold(c, h, sid, seed)
    s = c.post(f"/review/{sid}/decision", headers=h["REVIEWER"], json={"decision": "reseal", "reason": "Tape torn, re-sealed at hub"}).json()
    assert not s["seal"]["registered"] and s["shipment"]["state"] == "IN_TRANSIT" and cp_of(s, "WAREHOUSE")["seal_scanned"] == 0
    assert scan(c, h, "WAREHOUSE", sid, code=SEAL).json()["shipment"]["state"] == "ON_HOLD"   # the voided seal no longer works
    assert any(e["event_type"] == "SEAL_REPLACED" for e in s["events"]) and s["chain"]["ok"]
    # a new physical seal goes on; whoever holds the parcel scans it and that registers it, once
    assert scan(c, h, "WAREHOUSE", sid, code="TS-NEW1-SEAL").json()["shipment"]["state"] == "IN_TRANSIT"
    s = snap(c, sid)
    assert s["seal"]["registered"] and s["seal"]["hint"] == "…EAL" and cp_of(s, "WAREHOUSE")["seal_scanned"] == 1


def test_reviewer_return_to_seller_refunds(world):
    c, h, seed = world; sid = seed["shipment_id"]
    escalate_hold(c, h, sid, seed)
    s = c.post(f"/review/{sid}/decision", headers=h["REVIEWER"], json={"decision": "return", "reason": "Parcel opened in transit"}).json()
    assert s["shipment"]["state"] == "RETURNED" and s["payment"]["state"] == "CANCELLED" and wallet_of(c, h, "CONSUMER") == 10000


def test_dispute_decisions_are_limited_to_dispute_options(world):
    c, h, seed = world; sid = seed["shipment_id"]
    run_to_consumer(c, h, sid)
    scan(c, h, "CONSUMER", sid)
    c.post(f"/shipments/{sid}/dispute", headers=h["CONSUMER"], json={"note": "Wrong colour", "category": "wrong_item"})
    assert c.post(f"/review/{sid}/decision", headers=h["REVIEWER"], json={"decision": "reseal", "reason": "x"}).status_code == 422
    s = c.post(f"/review/{sid}/decision", headers=h["REVIEWER"], json={"decision": "replace", "reason": "Send the right one"}).json()
    assert s["shipment"]["state"] == "RESOLVED_REPLACEMENT"


def test_buyer_can_accept_a_flagged_parcel_after_checking_it_themselves(world):
    c, h, seed = world; sid = seed["shipment_id"]
    run_to_consumer(c, h, sid)
    scan(c, h, "CONSUMER", sid)
    assert photo(c, h, "CONSUMER", sid, "seal_side", qr_png("TS-NOPE-0000")).status_code == 200      # a different seal in the photo
    assert door(c, h, sid).status_code == 200
    assert accept(c, h, sid).status_code == 409
    assert accept(c, h, sid, override=True, note="").status_code == 409                    # a reason is needed
    assert accept(c, h, sid, override=True, note="Lighting made it look wrong, I checked").status_code == 200
    s = confirm(c, h, sid, code_of(c, h, sid)).json()
    ev = next(e for e in s["events"] if e["event_type"] == "ACCEPTED")
    assert s["shipment"]["state"] == "ACCEPTED" and ev["payload"]["override"] is True


# ---------- barcode and video ----------
def test_barcode_is_read_from_a_photo_and_checked_exactly():
    import io
    import zxingcpp
    from PIL import Image
    from app import checks, vision
    img = Image.fromarray(zxingcpp.write_barcode(zxingcpp.BarcodeFormat.EAN13, "9788119153565", 400, 200)).convert("RGB")
    buf = io.BytesIO(); img.save(buf, "JPEG"); data = buf.getvalue()
    assert [(c["format"], c["text"]) for c in vision.decode_codes(data)] == [("EAN13", "9788119153565")]
    assert vision.is_isbn("9788119153565") and not vision.is_isbn("8901491101158")
    same = checks._identifier(data, {"barcode": {"code": "9788119153565"}})
    other = checks._identifier(data, {"barcode": {"code": "9780140328721"}})
    blank = checks._identifier(PNG, {"barcode": {"code": "9788119153565"}})
    assert (same["status"], other["status"], blank["status"]) == ("PASS", "FAIL", "REVIEW")


def test_quantity_noise_from_the_model_is_dropped():
    from app import vision
    assert vision.QTY.match("52 g") and vision.QTY.match("1.5 L") and not vision.QTY.match("20×20")


def test_printed_digits_are_read_safely():
    from app import vision
    # real OCR output from a webcam photo: pieces, a misread digit in the ISBN line, noise digits from the guard bars
    assert vision._valid_in(["1SBN978-81-19153-68-5", "9117881194153565", "J-3154"]) == "9788119153565"
    assert vision._valid_in(["9788119", "H153565"]) == "9788119153565"      # digits under the bars come back in pieces
    assert vision._valid_in(["TS-EYGWGX 2026-10-23T10:55:20.709Z"]) is None    # our own camera timestamp is not a barcode
    assert vision._valid_in(["9788119153566"]) is None                      # one wrong digit fails the check digit
    assert vision._valid_in(["9117881194153565"]) is None                   # noise digits alone, no ISBN line to confirm: refuse
    # A check digit catches a single misread digit but not an invented number: "9788119153985" (what the vision model once
    # hallucinated for this book) passes it. That is why a language model is never a source of barcode digits.
    assert vision.checksum_ok("9788119153565") and vision.checksum_ok("9788119153985") and not vision.checksum_ok("9788119153566")


def test_barcode_step_returns_only_the_number(world):
    import io
    import zxingcpp
    from PIL import Image
    c, h, seed = world
    c.post("/admin/ai-mode", json={"scenario": "live"}, headers=h["ADMIN"])
    img = Image.fromarray(zxingcpp.write_barcode(zxingcpp.BarcodeFormat.EAN13, "9788119153565", 400, 200)).convert("RGB")
    buf = io.BytesIO(); img.save(buf, "JPEG")
    r = c.post("/identify", headers=h["SELLER"], data={"mode": "barcode"}, files={"file": ("b.jpg", buf.getvalue(), "image/jpeg")})
    assert r.status_code == 200 and r.json()["barcode"]["code"] == "9788119153565" and r.json()["barcode"]["isbn"] is True
    c.post("/admin/ai-mode", json={"scenario": "mock"}, headers=h["ADMIN"])


# ---------- notifications: only the people who must know ----------
def inbox(c, h, role):
    return c.get("/notifications", headers=h[role]).json()["items"]


def who(c, h, subject_part=None, channel=None):
    out = {}
    for r in ("SELLER", "WAREHOUSE", "DELIVERY", "CONSUMER", "REVIEWER"):
        items = [n for n in inbox(c, h, r) if (not subject_part or subject_part in n["subject"]) and (not channel or n["channel"] == channel)]
        if items:
            out[r] = items
    return out


def new_order(c, h, value=500):
    return c.post("/shipments", headers=h["SELLER"], json={"order_ref": "ORD-N", "product_name": "Notebook", "declared_value": value,
                                                           "identity": {"product_name": "Notebook", "confidence": 1}}).json()


def pay_card(c, h, sid, number="4242 4242 4242 4242"):
    return c.post(f"/shipments/{sid}/pay", headers=h["CONSUMER"], json={"method": "card", "card_number": number, "expiry": "12/39", "cvv": "123"})


def test_new_order_tells_the_buyer_about_the_wallet_and_nobody_in_the_chain_yet(world):
    c, h, _ = world
    new_order(c, h)
    told = who(c, h)
    assert set(told) == {"CONSUMER", "SELLER"}          # warehouse, delivery and support hear nothing yet
    assert {n["channel"] for n in told["CONSUMER"]} == {"SMS", "EMAIL"} and "wallet" in told["CONSUMER"][0]["body"].lower()
    assert all(n["why"] for rows in told.values() for n in rows)   # every message says why this person is being told


def run_chain_from_seller_done(c, h, sid):
    assert lock(c, h, "SELLER", sid).status_code == 200
    scan(c, h, "WAREHOUSE", sid); shoot(c, h, "WAREHOUSE", sid, ["seal_side"]); assert lock(c, h, "WAREHOUSE", sid).status_code == 200
    scan(c, h, "DELIVERY", sid); shoot(c, h, "DELIVERY", sid, ["seal_side", "parcel"]); assert lock(c, h, "DELIVERY", sid).status_code == 200


def ready_at_the_door(c, h, sid):
    run_to_consumer(c, h, sid)
    scan(c, h, "CONSUMER", sid)


def test_handover_tells_the_next_person_and_the_buyer_but_not_everyone(world):
    c, h, seed = world; sid = seed["shipment_id"]
    pack(c, h, sid); lock(c, h, "SELLER", sid)
    told = who(c, h, "on its way to you") | who(c, h, "Packed and sealed")
    assert "WAREHOUSE" in told and "CONSUMER" in told and "DELIVERY" not in told and "REVIEWER" not in told


def test_hold_tells_the_owner_buyer_and_seller_only(world):
    c, h, seed = world; sid = seed["shipment_id"]
    to_warehouse_hold(c, h, sid, seed)
    held = {r: [n for n in inbox(c, h, r) if n["event_type"] == "HOLD_PLACED"] for r in ("SELLER", "WAREHOUSE", "DELIVERY", "CONSUMER", "REVIEWER")}
    assert held["WAREHOUSE"] and held["CONSUMER"] and held["SELLER"]
    assert not held["DELIVERY"] and not held["REVIEWER"]                           # not yet: the owner still has tries
    assert {n["severity"] for n in held["WAREHOUSE"]} == {"action"} and {n["channel"] for n in held["WAREHOUSE"]} == {"SMS", "EMAIL"}
    assert {n["channel"] for n in held["CONSUMER"]} == {"SMS"}


def test_escalation_brings_in_the_reviewer_and_a_clear_tells_the_others(world):
    c, h, seed = world; sid = seed["shipment_id"]
    escalate_hold(c, h, sid, seed)
    assert any(n["event_type"] == "EXCEPTION_REPORTED" and n["channel"] == "EMAIL" for n in inbox(c, h, "REVIEWER"))
    assert not [n for n in inbox(c, h, "DELIVERY")]                                # downstream stages are not bothered
    c.post(f"/review/{sid}/decision", headers=h["REVIEWER"], json={"decision": "resume", "reason": "Checked by hand"})
    assert any(n["event_type"] == "REVIEW_DECISION" for n in inbox(c, h, "WAREHOUSE"))
    assert any(n["event_type"] == "REVIEW_DECISION" for n in inbox(c, h, "CONSUMER"))


def test_dispute_notifies_support_seller_and_last_handler_not_the_warehouse(world):
    c, h, seed = world; sid = seed["shipment_id"]
    c.post("/admin/scenario", json={"scenario": "C"}, headers=h["ADMIN"])
    run_to_consumer(c, h, sid)
    mark = {r: len(inbox(c, h, r)) for r in ("SELLER", "WAREHOUSE", "DELIVERY", "CONSUMER", "REVIEWER")}
    scan(c, h, "CONSUMER", sid)
    c.post(f"/shipments/{sid}/dispute", headers=h["CONSUMER"], json={"note": "Wrong colour", "category": "wrong_colour"})
    new = {r: [n for n in inbox(c, h, r) if n["event_type"] in ("DISPUTE_OPENED", "PAYMENT_STATE_CHANGED")] for r in mark}
    assert new["REVIEWER"] and new["SELLER"] and new["DELIVERY"] and new["CONSUMER"] and not new["WAREHOUSE"]
    assert any(n["severity"] == "action" and n["channel"] == "EMAIL" for n in new["REVIEWER"])
    c.post(f"/review/{sid}/decision", headers=h["REVIEWER"], json={"decision": "refund", "reason": "Wrong shade shipped"})
    s = snap(c, sid)
    assert s["payment"]["state"] == "CANCELLED" and wallet_of(c, h, "CONSUMER") == 10000           # pay-on-delivery: nothing to refund
    assert any(n["subject"] == "Nothing was charged" for n in inbox(c, h, "CONSUMER"))
    assert any("closed without a sale" in n["subject"] for n in inbox(c, h, "SELLER"))
    assert not any("Nothing was charged" in n["subject"] for n in inbox(c, h, "WAREHOUSE"))


def test_tampering_alerts_support(world):
    c, h, seed = world; sid = seed["shipment_id"]
    pack(c, h, sid)
    c.post(f"/admin/tamper-demo/{sid}", headers=h["ADMIN"])
    alerts = [n for n in inbox(c, h, "REVIEWER") if n["event_type"] == "CHAIN_BROKEN"]
    assert alerts and alerts[0]["severity"] == "alert"
    assert not [n for n in inbox(c, h, "SELLER") if n["event_type"] == "CHAIN_BROKEN"]


def test_everyone_reads_only_their_own_messages(world):
    c, h, seed = world; sid = seed["shipment_id"]
    new_order(c, h)
    assert all(n["to_role"] == "CONSUMER" for n in inbox(c, h, "CONSUMER"))
    assert c.get("/notifications", headers=h["CONSUMER"]).json()["unread"] > 0
    c.post("/notifications/read", headers=h["CONSUMER"])
    assert c.get("/notifications", headers=h["CONSUMER"]).json()["unread"] == 0
    assert c.get("/notifications", headers=h["SELLER"]).json()["unread"] > 0       # reading yours does not clear someone else's
    assert c.get("/admin/notifications", headers=h["SELLER"]).status_code == 403


# ---------- wallet ----------
def test_wallet_topup_checks_pin_source_amount_and_tells_the_buyer(world):
    c, h, _ = world
    top = lambda **k: c.post("/wallet/topup", headers=h["CONSUMER"], json={"amount": 500, "source": "HDFC Bank ••4321", "pin": PIN} | k)
    assert top(pin="0000").status_code == 403
    assert top(source="Some other bank").status_code == 422
    assert top(amount=10).status_code == 422 and top(amount=999999).status_code == 422
    assert c.post("/wallet/topup", headers=h["SELLER"], json={"amount": 500, "source": "HDFC Bank ••4321", "pin": PIN}).status_code == 403
    assert top().status_code == 200 and wallet_of(c, h, "CONSUMER") == 10500
    assert any("added to your wallet" in n["body"] for n in inbox(c, h, "CONSUMER"))
    assert c.get("/wallet", headers=h["DELIVERY"]).status_code == 403


# ---------- the doorstep handshake ----------
def test_scanning_the_seal_alone_never_pays_the_door_must_be_opened_by_the_courier(world):
    c, h, seed = world; sid = seed["shipment_id"]
    ready_at_the_door(c, h, sid)
    r = accept(c, h, sid)                                       # a stranger holding the QR, or the buyer paying from far away
    assert r.status_code == 409 and "delivery person is at your door" in r.json()["detail"]
    assert wallet_of(c, h, "CONSUMER") == 10000 and snap(c, sid)["payment"]["state"] == "PENDING"
    assert c.post(f"/shipments/{sid}/door", headers=h["CONSUMER"]).status_code == 403
    assert c.post(f"/shipments/{sid}/door", headers=h["WAREHOUSE"]).status_code == 403


def test_payment_is_only_reserved_until_the_courier_enters_the_code(world):
    c, h, seed = world; sid = seed["shipment_id"]
    ready_at_the_door(c, h, sid)
    assert door(c, h, sid).status_code == 200 and door(c, h, sid).status_code == 409
    assert accept(c, h, sid, pin="0000").status_code == 403    # wrong wallet PIN
    s = accept(c, h, sid).json()
    assert s["payment"]["state"] == "RESERVED" and s["shipment"]["state"] == "AWAITING_CONSUMER"
    assert wallet_of(c, h, "CONSUMER") == 10000 - 4200 and wallet_of(c, h, "SELLER") == 0     # the buyer's money is set aside, the seller has none
    assert "code" not in str(s["handover"]) and "code" not in str(c.get(f"/shipments/{sid}").json()["handover"])   # the code is never broadcast
    assert c.get(f"/shipments/{sid}/handover/code", headers=h["DELIVERY"]).status_code == 403
    assert accept(c, h, sid).status_code == 409                 # cannot pay twice
    code = code_of(c, h, sid)
    wrong = "000000" if code != "000000" else "111111"
    r = confirm(c, h, sid, wrong)
    assert r.status_code == 409 and "2 tries left" in r.json()["detail"]
    assert any("wrong handover code" in n["subject"].lower() for n in inbox(c, h, "CONSUMER"))   # the buyer is warned
    done_ = confirm(c, h, sid, code).json()
    assert done_["shipment"]["state"] == "ACCEPTED" and done_["payment"]["state"] == "RELEASED" and wallet_of(c, h, "SELLER") == 4200
    assert any(e["event_type"] == "DELIVERY_CONFIRMED" for e in done_["events"]) and done_["chain"]["ok"]
    assert any("paid out" in n["subject"] for n in inbox(c, h, "SELLER")) and any(n["subject"].startswith("Receipt") for n in inbox(c, h, "CONSUMER"))


def test_paid_but_nobody_takes_the_parcel_the_money_goes_back(world):
    c, h, seed = world; sid = seed["shipment_id"]
    ready_at_the_door(c, h, sid)
    door(c, h, sid); accept(c, h, sid)
    assert wallet_of(c, h, "CONSUMER") == 10000 - 4200
    s = c.post(f"/shipments/{sid}/handover/cancel", headers=h["DELIVERY"], json={"reason": "nobody_home"}).json()
    assert s["payment"]["state"] == "PENDING" and s["shipment"]["state"] == "AWAITING_CONSUMER"
    assert wallet_of(c, h, "CONSUMER") == 10000 and wallet_of(c, h, "SELLER") == 0
    assert any(e["event_type"] == "PAYMENT_REVERSED" for e in s["events"])
    assert any("returned to your wallet" in n["subject"] for n in inbox(c, h, "CONSUMER"))
    assert confirm(c, h, sid, "123456").status_code == 409     # nothing left to confirm


def test_three_wrong_codes_reverse_the_payment_and_stop_the_handover(world):
    c, h, seed = world; sid = seed["shipment_id"]
    ready_at_the_door(c, h, sid)
    door(c, h, sid); accept(c, h, sid)
    code = code_of(c, h, sid); wrong = "000000" if code != "000000" else "111111"
    for _ in range(3):
        r = confirm(c, h, sid, wrong)
    assert r.status_code == 409 and "cancelled" in r.json()["detail"]
    assert wallet_of(c, h, "CONSUMER") == 10000 and snap(c, sid)["payment"]["state"] == "PENDING" and wallet_of(c, h, "SELLER") == 0


def test_the_reservation_times_out_by_itself(world):
    import time
    c, h, seed = world; sid = seed["shipment_id"]
    ready_at_the_door(c, h, sid)
    c.post("/admin/wallet", json={"balance": 10000, "code_window_sec": 1}, headers=h["ADMIN"])
    door(c, h, sid); accept(c, h, sid)
    assert wallet_of(c, h, "CONSUMER") == 10000 - 4200
    time.sleep(1.4)
    r = confirm(c, h, sid, "123456")                            # even a right code is too late; the clock already gave the money back
    assert r.status_code == 409
    s = snap(c, sid)
    assert s["payment"]["state"] == "PENDING" and wallet_of(c, h, "CONSUMER") == 10000 and s["handover"]["state"] == "OPEN"
    assert any("not charged" in e["payload"].get("why", "") for e in s["events"] if e["event_type"] == "PAYMENT_REVERSED")
    c.post("/admin/wallet", json={"balance": 10000, "code_window_sec": 300}, headers=h["ADMIN"])


def test_short_wallet_raises_the_alarm_for_the_buyer_and_a_call_prompt_for_the_courier(world):
    c, h, seed = world; sid = seed["shipment_id"]
    ready_at_the_door(c, h, sid)
    c.post("/admin/wallet", json={"balance": 150}, headers=h["ADMIN"])
    door(c, h, sid)
    buyer = [n for n in inbox(c, h, "CONSUMER") if n["subject"].startswith("Wallet short")]
    agent = [n for n in inbox(c, h, "DELIVERY") if n["subject"].startswith("Buyer wallet short")]
    assert buyer and {n["channel"] for n in buyer} == {"SMS", "EMAIL"} and buyer[0]["severity"] == "alert"
    assert "+91 98200 00003" in buyer[0]["body"]                # the buyer can call the courier
    assert agent and "+91 98200 00004" in agent[0]["body"] and agent[0]["severity"] == "action"      # the courier can call the buyer
    assert not [n for n in inbox(c, h, "WAREHOUSE") + inbox(c, h, "REVIEWER") if "short" in n["subject"].lower()]
    r = accept(c, h, sid)
    assert r.status_code == 402 and "short by ₹4,050" in r.json()["detail"]
    s = snap(c, sid)
    assert s["payment"]["state"] == "PENDING" and wallet_of(c, h, "CONSUMER") == 150 and s["handover"]["short_by"] == 4050
    assert c.post("/wallet/topup", headers=h["CONSUMER"], json={"amount": 5000, "source": "SBI ••9087", "pin": PIN}).status_code == 200
    assert any("topped up" in n["subject"] for n in inbox(c, h, "DELIVERY"))                          # the courier hears they can go ahead
    assert accept(c, h, sid).status_code == 200 and wallet_of(c, h, "CONSUMER") == 150 + 5000 - 4200


def test_repeated_failed_handovers_go_to_a_reviewer(world):
    c, h, seed = world; sid = seed["shipment_id"]
    ready_at_the_door(c, h, sid)
    for i in range(3):
        assert door(c, h, sid).status_code == 200
        s = c.post(f"/shipments/{sid}/handover/cancel", headers=h["DELIVERY"], json={"reason": "unreachable"}).json()
    assert s["shipment"]["state"] == "UNDER_REVIEW" and s["case"]["open"] and s["case"]["waiting_on"]["role"] == "REVIEWER"
    assert door(c, h, sid).status_code == 409
    assert any(n["event_type"] == "HANDOVER_FAILED" and n["channel"] == "EMAIL" for n in inbox(c, h, "REVIEWER"))
    assert c.post(f"/review/{sid}/decision", headers=h["REVIEWER"], json={"decision": "refund", "reason": "x"}).status_code == 422   # only resume or return here
    s = c.post(f"/review/{sid}/decision", headers=h["REVIEWER"], json={"decision": "resume", "reason": "Buyer was travelling, try again"}).json()
    assert s["shipment"]["state"] == "AWAITING_CONSUMER" and door(c, h, sid).status_code == 200      # fresh attempts


def test_a_buyer_cannot_dispute_after_paying_and_a_rejected_dispute_charges_them(world):
    c, h, seed = world; sid = seed["shipment_id"]
    ready_at_the_door(c, h, sid)
    door(c, h, sid); accept(c, h, sid)
    assert c.post(f"/shipments/{sid}/dispute", headers=h["CONSUMER"], json={"note": "x"}).status_code == 409
    c.post(f"/shipments/{sid}/handover/cancel", headers=h["DELIVERY"], json={"reason": "other"})
    c.post(f"/shipments/{sid}/dispute", headers=h["CONSUMER"], json={"note": "Not what I ordered"})
    s = c.post(f"/review/{sid}/decision", headers=h["REVIEWER"], json={"decision": "reject", "reason": "It matches the listing"}).json()
    assert s["payment"]["state"] == "RELEASED" and wallet_of(c, h, "SELLER") == 4200 and wallet_of(c, h, "CONSUMER") == 10000 - 4200


# ---------- seals are read from the parcel, never handed out ----------
def test_the_system_never_hands_out_a_seal_serial(world):
    import json
    c, h, seed = world; sid = seed["shipment_id"]
    pack(c, h, sid); lock(c, h, "SELLER", sid)
    s = snap(c, sid)
    assert s["seal"]["registered"] and s["seal"]["hint"] == "…001"
    assert SEAL not in json.dumps(s) and "seal_code" not in s                              # not in the shared snapshot or ledger
    for r in ("SELLER", "WAREHOUSE", "DELIVERY", "CONSUMER", "REVIEWER"):
        assert SEAL not in json.dumps(inbox(c, h, r))                                     # nor in any message


def test_nothing_locks_until_the_seal_has_been_read_and_registered(world):
    c, h, seed = world; sid = seed["shipment_id"]
    shoot(c, h, "SELLER", sid, ["product_front", "product_side", "seal_side", "packing_video"])
    r = lock(c, h, "SELLER", sid)
    assert r.status_code == 409 and "seal" in r.json()["detail"]
    assert scan(c, h, "WAREHOUSE", sid).status_code == 409                                  # not the warehouse's turn, and no seal yet


def test_a_seal_can_only_ever_be_used_once(world):
    c, h, seed = world; sid = seed["shipment_id"]
    pack(c, h, sid)
    other = new_order(c, h)["shipment"]["id"]
    shoot(c, h, "SELLER", other, ["product_front", "product_side"])
    s = scan(c, h, "SELLER", other, code=SEAL).json()                                       # the same sticker on a second parcel
    assert s["shipment"]["state"] == "ON_HOLD" and "already used" in s["case"]["reason"]
    assert not s["seal"]["registered"]


def test_the_seal_serial_is_read_from_the_photo(world):
    c, h, seed = world; sid = seed["shipment_id"]
    shoot(c, h, "SELLER", sid, ["product_front", "product_side"])
    r = photo(c, h, "SELLER", sid, "seal_side", qr_png("TS-AB12-CD34"))                       # a photo of a sticker's QR
    assert r.status_code == 200
    s = r.json()
    assert s["seal"]["registered"] and s["seal"]["hint"] == "…D34"
    shoot(c, h, "SELLER", sid, ["packing_video"]); assert lock(c, h, "SELLER", sid).status_code == 200
    ok = photo(c, h, "WAREHOUSE", sid, "seal_side", qr_png("TS-AB12-CD34")).json()          # warehouse photographs the same seal
    assert cp_of(ok, "WAREHOUSE")["seal_scanned"] == 1 and ok["shipment"]["state"] == "IN_TRANSIT"
    bad = photo(c, h, "WAREHOUSE", sid, "seal_side", qr_png("TS-ZZ99-YY88")).json()           # a different seal
    assert bad["shipment"]["state"] == "ON_HOLD" and "not this parcel's seal" in bad["case"]["reason"]


def test_seal_reading_prefers_the_seal_over_the_products_barcode():
    import io
    import zxingcpp
    from PIL import Image
    from app import vision
    ean = Image.fromarray(zxingcpp.write_barcode(zxingcpp.BarcodeFormat.EAN13, "9788119153565", 400, 200)).convert("RGB")
    qr = Image.fromarray(zxingcpp.write_barcode(zxingcpp.BarcodeFormat.QRCode, "TS-AB12-CD34", 200, 200)).convert("RGB")
    both = Image.new("RGB", (700, 300), "white"); both.paste(ean, (0, 40)); both.paste(qr, (460, 40))
    buf = io.BytesIO(); both.save(buf, "PNG")
    assert vision.read_seal(buf.getvalue(), avoid="9788119153565")["code"] == "TS-AB12-CD34"
    buf = io.BytesIO(); ean.save(buf, "PNG")
    assert vision.read_seal(buf.getvalue(), avoid="9788119153565") is None                  # the product's barcode is not a seal
    assert vision.norm_seal("https://x.in/s/ts-1a2b-3c4d") == "TS-1A2B-3C4D" and vision.norm_seal("12") is None


# ---------- the barcode scam: one product shown, another product's number given ----------
def test_a_typed_barcode_is_refused(world):
    c, h, seed = world
    r = c.post("/shipments", headers=h["SELLER"], json={"order_ref": "ORD-3", "product_name": "Lay's", "declared_value": 20,
                                                        "identity": {"product_name": "Lay's", "barcode": {"code": "9788119153565", "how": "typed"}}})
    assert r.status_code == 422 and "camera" in r.json()["detail"]
    r = c.post("/shipments", headers=h["SELLER"], json={"order_ref": "ORD-3", "product_name": "Lay's", "declared_value": 20,
                                                        "identity": {"product_name": "Lay's", "barcode": {"code": "9788119153565"}, "barcode_sha256": "f" * 64}})
    assert r.status_code == 422                                                              # a hash the server never read


def test_a_barcode_that_belongs_to_another_product_is_refused(world):
    c, h, seed = world
    r = barcode_order(c, h, verdict="mismatch")
    assert r.status_code == 422
    assert barcode_order(c, h, verdict="match").status_code == 200
    # what the server read wins over anything the phone sends along
    s = barcode_order(c, h, code="9788119153565").json()
    assert s["shipment"]["identity"]["barcode"]["code"] == "9788119153565" and s["shipment"]["identity"]["verification"]["status"] == "match"


def test_the_barcode_must_agree_with_what_the_front_photo_shows():
    from app import vision
    book = {"title": "Atomic Habits", "by": "James Clear", "source": "Open Library"}
    assert vision.cross_check({"product_name": "Atomic Habits", "brand": "James Clear", "visible_text": []}, book)["status"] == "match"
    chips = {"product_name": "Lay's Classic Salted", "brand": "Lay's", "visible_text": ["Classic", "Salted"]}
    swapped = vision.cross_check(chips, book)
    assert swapped["status"] == "mismatch" and "Atomic Habits" in swapped["detail"] and "Lay's" in swapped["detail"]
    assert vision.cross_check(chips, {"title": "Lay's Classic Salted", "by": "Lay's"})["status"] == "match"
    assert vision.cross_check(chips, {})["status"] == "unverified"                          # unknown code: never silently trusted
