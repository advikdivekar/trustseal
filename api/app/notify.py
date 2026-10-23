"""Who must know what. One rule per thing that can happen; each rule names the people affected and why.

Principle: tell the person who has to ACT, and the people whose product or money is AFFECTED. Nobody else.
(Warehouse staff are not told about a delivery dispute; the seller is not pinged about every handover.)
Messages are simulated: SMS go to a log, emails are written as .eml files in api/data/outbox. Swap `deliver` for a real
SMS or SMTP provider later; nothing else changes.
"""
import json
import sqlite3
from email.message import EmailMessage
from pathlib import Path

from . import db as dbm
from . import wallet
from .ledger import now_iso

OUTBOX = dbm.DATA / "outbox"
ROLE_NAME = {"SELLER": "the seller", "WAREHOUSE": "the warehouse", "DELIVERY": "delivery", "CONSUMER": "the buyer", "REVIEWER": "support"}
STAGE_ORDER = ["SELLER", "WAREHOUSE", "DELIVERY", "CONSUMER"]


def inr(n) -> str:
    return "₹" + f"{float(n):,.0f}"


def _account(conn, role, shipment_id=None):
    """The real person for a role on this shipment: whoever acted at that stage, else the account for the role."""
    if shipment_id and role in STAGE_ORDER:
        r = conn.execute("SELECT a.* FROM checkpoints c JOIN accounts a ON a.id=c.actor_id WHERE c.shipment_id=? AND c.role=?", (shipment_id, role)).fetchone()
        if r:
            return r
    return conn.execute("SELECT * FROM accounts WHERE role=?", (role,)).fetchone()


def _ctx(conn, sid, payload):
    s = conn.execute("SELECT * FROM shipments WHERE id=?", (sid,)).fetchone()
    pay = conn.execute("SELECT * FROM payments WHERE shipment_id=?", (sid,)).fetchone()
    seal = conn.execute("SELECT seal_code FROM seals WHERE shipment_id=? AND status='ACTIVE'", (sid,)).fetchone()
    # messages never carry the serial itself, only its last three characters, so a message cannot be used to fake a scan
    return {"P": s["product_name"], "O": s["order_ref"], "A": inr(pay["amount"]), "mode": pay["mode"], "seal": ("seal …" + seal["seal_code"][-3:]) if seal else "the seal", **payload}


def rules(conn, sid, ev, p, actor_role):
    """Yield (role, severity, channels, why, subject, sms, email). `p` is the event payload plus context."""
    t = ev
    stage = p.get("stage") or p.get("failed_stage") or ""
    rev = _account(conn, "REVIEWER")
    rev_name = rev["name"] if rev else "support"
    owner = lambda role: _account(conn, role, sid)["name"] if _account(conn, role, sid) else ROLE_NAME.get(role, "")

    if t == "SHIPMENT_CREATED":
        bal = wallet.balance(conn, "acc-consumer")
        amt = float(p["A"].replace("₹", "").replace(",", ""))
        warn = f" Your wallet is short by {inr(amt - bal)}: add money before delivery." if bal < amt else ""
        yield ("CONSUMER", "action" if warn else "info", ("SMS", "EMAIL"), "You placed this order and you pay from your wallet at the door.",
               f"Order {p['O']} is registered",
               f"TrustSeal: order {p['O']} ({p['P']}) is registered. You pay {p['A']} from your wallet at the door, after you check the parcel. Wallet balance {inr(bal)}.{warn}",
               f"Your order {p['O']} for {p['P']} is registered with the seller.\n\nYou pay {p['A']} from your TrustSeal wallet at your door, after you have checked the parcel. Nothing is charged before that.\n"
               f"Wallet balance: {inr(bal)}.{warn}")
        yield ("SELLER", "info", ("SMS",), "Confirmation of the order you registered.", f"Order {p['O']} registered",
               f"TrustSeal: {p['P']} registered as {p['O']}. Next: put the seal on the parcel and scan it. The buyer pays from their wallet at the door.", "")
    elif t == "PAYMENT_STATE_CHANGED":
        to, why = p.get("to"), p.get("why", "")
        if to == "RELEASED" and p.get("via") == "dispute":
            yield ("SELLER", "info", ("SMS", "EMAIL"), "A dispute closed in your favour and you were paid.", f"{p['A']} paid out for {p['O']}",
                   f"TrustSeal: the dispute on {p['O']} was closed and {p['A']} was paid to your wallet.", f"The dispute on {p['P']} ({p['O']}) was closed in your favour.\n{why}\n{p['A']} was paid to your wallet.")
            yield ("CONSUMER", "info", ("SMS",), "Money left your wallet after the review.", f"{p['A']} charged for {p['O']}",
                   f"TrustSeal: support found the parcel fine, so {p['A']} was charged to your wallet. {why}", "")
        elif to == "CANCELLED":
            yield ("CONSUMER", "info", ("SMS",), "Confirms you were not charged.", "Nothing was charged",
                   f"TrustSeal: order {p['O']} is closed and you were not charged anything.", "")
            yield ("SELLER", "alert", ("SMS",), "The sale did not complete.", f"{p['O']} closed without a sale",
                   f"TrustSeal: order {p['O']} closed without a sale. {why}", "")
        elif to == "REFUNDED":
            yield ("CONSUMER", "info", ("SMS",), "Money came back to your wallet.", f"{p['A']} refunded",
                   f"TrustSeal: {p['A']} was refunded to your wallet for {p['O']}. Balance {inr(wallet.balance(conn, 'acc-consumer'))}.", "")
            yield ("SELLER", "alert", ("SMS",), "A payout was taken back after a complaint.", f"{p['A']} taken back for {p['O']}",
                   f"TrustSeal: {p['A']} was returned to the buyer from your wallet for {p['O']}. {why}", "")
        elif to == "DISPUTE_HOLD":
            yield ("SELLER", "alert", ("SMS",), "Your sale is on hold.", f"Sale on hold for {p['O']}",
                   f"TrustSeal: the sale of {p['O']} ({p['A']}) is on hold while a dispute is reviewed.", "")
    elif t == "STAGE_LOCKED":
        if stage == "SELLER":
            yield ("WAREHOUSE", "action", ("SMS",), "You are next to handle this parcel.", f"Parcel {p['O']} on its way to you",
                   f"TrustSeal: parcel {p['O']} ({p['P']}) is sealed ({p['seal']}) and heading to you. Scan the seal when it arrives.", "")
            yield ("CONSUMER", "info", ("SMS",), "Status of your order.", "Packed and sealed",
                   f"TrustSeal: {p['P']} is packed and sealed ({p['seal']}). The packing video is on record.", "")
        elif stage == "WAREHOUSE":
            yield ("DELIVERY", "action", ("SMS",), "You are next to handle this parcel.", f"Parcel {p['O']} ready to collect",
                   f"TrustSeal: parcel {p['O']} is cleared at the hub and ready for you ({p['seal']}). Scan it at pickup.", "")
            yield ("CONSUMER", "info", ("SMS",), "Status of your order.", "Cleared the warehouse",
                   f"TrustSeal: {p['P']} cleared the warehouse and goes to delivery next.", "")
        elif stage == "DELIVERY":
            bal = wallet.balance(conn, "acc-consumer"); amt = float(p["A"].replace("₹", "").replace(",", ""))
            agent = owner("DELIVERY")
            warn = f" Your wallet is short by {inr(amt - bal)}: add money now so the delivery is not held up." if bal < amt else ""
            yield ("CONSUMER", "action" if warn else "info", ("SMS", "EMAIL"), "Your parcel is on its way and you pay from your wallet when it arrives.", f"Your parcel {p['O']} is out for delivery",
                   f"TrustSeal: {p['O']} is out for delivery with {agent}. When they are at your door, scan the seal and pay {p['A']} from your wallet. Balance {inr(bal)}.{warn}",
                   f"Your parcel {p['O']} ({p['P']}) is out for delivery with {agent}.\n\nWhen they are at your door, scan the seal on the box ({p['seal']}) to confirm it is your parcel. "
                   f"Then pay {p['A']} from your wallet with your PIN. After they leave, open the box and scan the product's barcode: if it is not what you ordered, a complaint is filed for you automatically. The seller is paid only after {agent} confirms the handover with your one-time code.\nWallet balance: {inr(bal)}.{warn}")
            yield ("SELLER", "info", ("SMS",), "Status of your sale.", "Out for delivery", f"TrustSeal: {p['O']} is out for delivery.", "")
    elif t == "ACCEPTED":
        if p.get("override"):
            yield ("SELLER", "alert", ("SMS", "EMAIL"), "The buyer accepted after a check was flagged, so you should know why.", f"{p['O']} accepted after a flag",
                   f"TrustSeal: the buyer accepted {p['O']} even though a check was flagged. Their note: {p.get('note', '')}",
                   f"The buyer accepted {p['P']} ({p['O']}) after checking it themselves, although the system had flagged it.\nBuyer's note: {p.get('note', '')}\nThe override is recorded in the shared history.")
    elif t == "DOOR_ARRIVED":
        yield ("CONSUMER", "alert", ("SMS",), "The delivery person is at your door right now.", f"{p['agent']} is at your door",
               f"TrustSeal: {p['agent']} is at your door with {p['P']}. Scan the seal on the box, then pay {p['A']} from your wallet.", "")
    elif t == "WALLET_SHORT":
        agent = _account(conn, "DELIVERY", sid); buyer = _account(conn, "CONSUMER")
        short, bal = inr(p["short_by"]), inr(p["balance"])
        yield ("CONSUMER", "alert", ("SMS", "EMAIL"), "Your wallet cannot cover this parcel and the delivery person is waiting.", f"Wallet short by {short}",
               f"TrustSeal ALERT: your wallet has {bal} but {p['O']} is {p['A']}. You are short by {short}. Add money in the app, or call {agent['name']} on {agent['phone']}.",
               f"Your wallet balance is {bal} and the parcel {p['O']} ({p['P']}) costs {p['A']}. You are short by {short}.\n\n"
               f"{agent['name']} is waiting at your door. Add money to your wallet in the app, or call them on {agent['phone']}.")
        yield ("DELIVERY", "action", ("SMS",), "The buyer cannot pay yet and you are the one at their door.", f"Buyer wallet short by {short}",
               f"TrustSeal: the buyer's wallet is short by {short} for {p['O']}. They were alerted. You can call them on {buyer['phone']}. Please wait a few minutes.", "")
    elif t == "PAYMENT_RESERVED":
        agent = owner("DELIVERY")
        yield ("DELIVERY", "action", ("SMS",), "The buyer paid and you hold the parcel.", f"Buyer paid for {p['O']}",
               f"TrustSeal: the buyer paid {p['A']} for {p['O']}. Ask them for the handover code in their app, then hand over the parcel and enter the code. It expires in 5 minutes.", "")
        yield ("CONSUMER", "info", ("SMS",), "You just paid, and the code proves you got the parcel.", "Payment reserved",
               f"TrustSeal: {p['A']} is reserved from your wallet. Show the handover code in the app to {agent} when you have the parcel in your hands. It is valid for 5 minutes. If it runs out you are not charged.", "")
    elif t == "CODE_WRONG":
        yield ("CONSUMER", "alert", ("SMS",), "Someone entered a wrong handover code for your parcel.", "Wrong handover code",
               f"TrustSeal ALERT: a wrong handover code was entered for {p['O']} (attempt {p.get('attempt')} of 3). Do not share your code with anyone but the person handing you the parcel.", "")
    elif t == "DELIVERY_CONFIRMED":
        yield ("SELLER", "info", ("SMS", "EMAIL"), "You were paid for a delivered parcel.", f"{p['A']} paid out for {p['O']}",
               f"TrustSeal: {p['O']} was delivered and {p['A']} was paid to your wallet. Ref {p.get('ref', '')}.",
               f"{p['P']} ({p['O']}) was handed to the buyer and confirmed with their one-time code.\n{p['A']} was paid to your TrustSeal wallet.\nReference: {p.get('ref', '')}")
        yield ("CONSUMER", "info", ("EMAIL",), "Receipt for the parcel you accepted.", f"Receipt: {p['A']} paid for {p['O']}",
               "", f"Thank you. You accepted {p['P']} ({p['O']}) and paid {p['A']} from your wallet.\nThe handover was confirmed by {p.get('agent', 'the delivery person')} with your one-time code.\nWallet balance after payment: {inr(wallet.balance(conn, 'acc-consumer'))}.")
        yield ("DELIVERY", "info", ("SMS",), "You completed this delivery.", f"Delivery {p['O']} confirmed", f"TrustSeal: handover of {p['O']} confirmed. Thank you.", "")
    elif t == "PAYMENT_REVERSED":
        yield ("CONSUMER", "alert", ("SMS",), "Money you paid went back to your wallet, so you know you were not charged.", "Payment returned to your wallet",
               f"TrustSeal: {p['A']} was returned to your wallet for {p['O']}. {p.get('why', '')} Balance {inr(p.get('balance', 0))}.", "")
        yield ("DELIVERY", "action", ("SMS",), "The payment you were waiting on was reversed and the parcel is still yours.", f"Payment reversed for {p['O']}",
               f"TrustSeal: the payment for {p['O']} was reversed. The parcel stays with you until a handover is confirmed.", "")
    elif t in ("HANDOVER_CANCELLED", "HANDOVER_EXPIRED"):
        why = p.get("why", "")
        yield ("CONSUMER", "alert", ("SMS",), "Your delivery attempt did not complete.", "Delivery attempt unsuccessful",
               f"TrustSeal: the delivery attempt for {p['O']} did not complete. {why} The parcel stays with the courier. You were not charged.", "")
        yield ("SELLER", "info", ("SMS",), "Your parcel was not delivered on this attempt.", "Delivery attempt unsuccessful",
               f"TrustSeal: delivery of {p['O']} was not completed. {why}", "")
    elif t == "HANDOVER_FAILED":
        yield ("REVIEWER", "action", ("SMS", "EMAIL"), "Delivery failed repeatedly and needs your decision.", f"Delivery failed: {p['P']} ({p['O']})",
               f"TrustSeal: delivery of {p['O']} failed repeatedly. {p.get('reason', '')} Open the Resolution Center.",
               f"The delivery of {p['P']} ({p['O']}) failed repeatedly.\n{p.get('reason', '')}\n\nThe buyer was not charged and the parcel is with the courier. Open the Resolution Center to decide whether to try again or return it.")
        yield ("CONSUMER", "alert", ("SMS",), "Your parcel could not be delivered.", "Delivery could not be completed",
               f"TrustSeal: delivery of {p['O']} failed repeatedly and support is reviewing it. You were not charged.", "")
        yield ("SELLER", "alert", ("SMS",), "Your parcel could not be delivered.", "Delivery could not be completed",
               f"TrustSeal: delivery of {p['O']} failed repeatedly. Support is deciding next steps.", "")
    elif t == "HOLD_PLACED":
        reason = p.get("reason", "")
        yield (stage, "action", ("SMS", "EMAIL"), "This problem is at your stage and you can fix it.", f"Action needed: {p['P']} on hold at your stage",
               f"TrustSeal: {p['O']} is on hold at your stage: {reason} You have 3 tries to fix it in the app.",
               f"Parcel {p['O']} ({p['P']}) is on hold at your stage.\n\n{reason}\n\nYou have 3 tries to fix it by scanning the right seal again or retaking the photo. If it still fails it goes to {rev_name} for a decision.")
        if stage != "CONSUMER":
            yield ("CONSUMER", "info", ("SMS",), "Your parcel is delayed.", "Your parcel is delayed",
                   f"TrustSeal: {p['P']} ({p['O']}) is on hold at {ROLE_NAME[stage]} for a check. Nothing for you to do. Your money is safe.", "")
        yield ("SELLER", "info", ("SMS",), "Your product and payment are involved.", f"{p['O']} on hold at {ROLE_NAME[stage]}",
               f"TrustSeal: {p['O']} ({p['P']}) is on hold at {ROLE_NAME[stage]}: {reason}", "")
    elif t == "HOLD_CLEARED":
        yield ("CONSUMER", "info", ("SMS",), "Your parcel is moving again.", "Back on route", f"TrustSeal: {p['P']} ({p['O']}) passed its check and is back on route.", "")
        yield ("SELLER", "info", ("SMS",), "Your parcel is moving again.", "Back on route", f"TrustSeal: {p['O']} is back on route.", "")
    elif t == "EXCEPTION_REPORTED" and p.get("handover"):
        return                                                  # HANDOVER_FAILED already told the right people
    elif t == "EXCEPTION_REPORTED":
        reason = p.get("note", "")
        yield ("REVIEWER", "action", ("SMS", "EMAIL"), "A case needs your decision.", f"Case needs a decision: {p['P']} ({p['O']})",
               f"TrustSeal: case on {p['O']} at {ROLE_NAME.get(p.get('stage_now', ''), 'a stage')}: {reason}. Open the Resolution Center.",
               f"A case needs your decision.\n\nParcel: {p['P']} ({p['O']})\nProblem: {reason}\n\nOpen the Resolution Center to read the evidence and decide.")
        yield ("SELLER", "alert", ("SMS", "EMAIL"), "Your parcel and payment are under review.", f"{p['O']} under review",
               f"TrustSeal: {p['O']} ({p['P']}) is under review by support. Payment stays on hold.", f"Parcel {p['O']} is under review by {rev_name}. Payment stays held until a decision.")
        yield ("CONSUMER", "info", ("SMS",), "Your parcel is affected.", "Under review", f"TrustSeal: {p['P']} is being reviewed by support. Your money is safe.", "")
    elif t == "DISPUTE_OPENED":
        tk, auto, done_, paid = p.get("ticket", ""), p.get("auto"), p.get("auto_resolved"), p.get("paid")
        held = "The seller was already paid, so a refund is taken back from them if the complaint is upheld." if paid else "Payment is held."
        if done_:
            yield ("CONSUMER", "info", ("SMS", "EMAIL"), "You scanned a different product, so a complaint was filed and refunded for you.", f"{tk}: refunded automatically",
                   f"TrustSeal: the product you scanned is not what you ordered. We filed complaint {tk} for you and refunded {p['A']} to your wallet. Nothing more to do.",
                   f"We checked the product you took out of {p['P']} ({p['O']}) against what the seller registered. It is different, so we filed complaint {tk} on your behalf "
                   f"and refunded {p['A']} to your wallet straight away.\n\nWhat we found: {(p.get('triage') or {}).get('cause', '')}")
            yield ("SELLER", "alert", ("SMS", "EMAIL"), "A wrong item was reported and the sale was refunded to the buyer.", f"{tk}: wrong item on {p['O']}",
                   f"TrustSeal: the buyer's scan shows a different product from the one you registered for {p['O']}. {p['A']} was refunded from your wallet to the buyer. See the evidence in the app.",
                   f"The product the buyer took out of {p['P']} ({p['O']}) is different from the one you registered.\n{(p.get('triage') or {}).get('cause', '')}\n{p['A']} was returned to the buyer from your wallet.\nThe photos, barcode scan and seal checks are attached to complaint {tk}.")
            yield ("REVIEWER", "info", ("SMS",), "For your records: this complaint was decided automatically.", f"{tk}: auto-refunded",
                   f"TrustSeal: complaint {tk} on {p['O']} was refunded automatically on strong evidence. Nothing to decide.", "")
        else:
            yield ("REVIEWER", "action", ("SMS", "EMAIL"), "A complaint needs your decision.", f"{tk}: {p['P']} ({p['O']})",
                   f"TrustSeal: {'automatic complaint' if auto else 'buyer complaint'} {tk} on {p['O']}: {p.get('reason', '')}. Open the Resolution Center.",
                   f"Complaint {tk} on {p['P']} ({p['O']}) was opened ({p.get('source', '')}).\n\nReason ({p.get('category', 'other')}): {p.get('reason', '')}\n"
                   f"{('System assessment: ' + (p.get('triage') or {}).get('cause', '')) if p.get('triage') else ''}\n\n{held} Open the Resolution Center to read the evidence and decide.")
            yield ("SELLER", "alert", ("SMS", "EMAIL"), "A complaint puts your payment at risk.", f"{tk}: complaint on {p['O']}",
                   f"TrustSeal: complaint {tk} on {p['O']} ({p['P']}): {p.get('reason', '')}. {held}",
                   f"A complaint was opened on {p['P']} ({p['O']}).\nReason: {p.get('reason', '')}\n{held}")
            yield ("CONSUMER", "info", ("SMS", "EMAIL"), "Confirms we received your complaint.", f"We got your complaint {tk}",
                   f"TrustSeal: complaint {tk} on {p['O']} is with support. The evidence is attached; you will hear back here.",
                   f"We received complaint {tk} on {p['P']} ({p['O']}). The seller's evidence, every handover and your photos are attached. {p.get('callback', '')}")
        if not paid:
            yield ("DELIVERY", "info", ("SMS",), "You were the last to handle this parcel.", f"Dispute on parcel {p['O']}",
                   f"TrustSeal: the buyer reported a problem with parcel {p['O']} you delivered. No action now. Support may contact you.", "")
    elif t == "SEAL_REPLACED":
        stg = p.get("stage_now", "")
        yield (stg or "WAREHOUSE", "action", ("SMS",), "The seal changed and you must scan the new one.", f"New seal for {p['O']}",
               f"TrustSeal: {p['O']} was re-sealed. The old seal is void. Put the new seal on the parcel and scan it so it is registered.", "")
        yield ("SELLER", "info", ("SMS",), "Your parcel was re-sealed.", "Parcel re-sealed", f"TrustSeal: {p['O']} was re-sealed by support with a new seal.", "")
        yield ("CONSUMER", "info", ("SMS",), "The seal you will scan changed.", "Parcel re-sealed", f"TrustSeal: {p['P']} was re-sealed. When it arrives, scan the seal on the box as usual.", "")
    elif t == "REVIEW_DECISION":
        d, instr, why = p.get("decision"), p.get("instruction", ""), p.get("reason", "")
        st = p.get("stage", "")
        if d == "recheck":
            yield (st, "action", ("SMS", "EMAIL"), "The reviewer needs you to redo something.", f"{rev_name} asks you to recheck {p['O']}",
                   f"TrustSeal: {rev_name} asks you to recheck {p['O']}: {instr}", f"{rev_name} reviewed {p['P']} ({p['O']}) and asks you to recheck:\n\n{instr}\n\nReason: {why}\n\nYou have fresh tries in the app.")
        elif d == "resume":
            yield (st, "info", ("SMS",), "Your hold was cleared.", "Hold cleared", f"TrustSeal: {rev_name} cleared the hold on {p['O']}. Carry on.", "")
            yield ("CONSUMER", "info", ("SMS",), "Your parcel is moving again.", "Back on route", f"TrustSeal: support cleared the check on {p['P']}. It is back on route.", "")
            yield ("SELLER", "info", ("SMS",), "Your parcel is moving again.", "Back on route", f"TrustSeal: support cleared the check on {p['O']}. Back on route.", "")
        elif d == "return":
            yield ("SELLER", "alert", ("SMS", "EMAIL"), "Your parcel is coming back to you.", f"{p['O']} returned to you",
                   f"TrustSeal: {p['O']} ({p['P']}) is being returned to you. Buyer refunded. {why}", f"Parcel {p['O']} ({p['P']}) is returned to you because: {why}\nThe buyer is refunded.")
            yield (st, "info", ("SMS",), "The case at your stage is closed.", "Case closed", f"TrustSeal: {p['O']} is being returned to the seller. Case closed.", "")
        elif d == "replace":
            yield ("SELLER", "action", ("SMS", "EMAIL"), "You must send the right product.", f"Send a replacement for {p['O']}",
                   f"TrustSeal: send a replacement for {p['O']}. Funds stay held. {why}", f"Support decided the buyer should get a replacement for {p['P']} ({p['O']}).\nReason: {why}\nFunds stay held for the replacement.")
            yield ("CONSUMER", "info", ("SMS",), "Outcome of your dispute.", "Replacement requested", f"TrustSeal: your dispute was upheld. The seller must send a replacement. Funds stay held.", "")
        elif d == "reject":
            yield ("CONSUMER", "info", ("SMS", "EMAIL"), "Outcome of your dispute.", f"Dispute on {p['O']} closed",
                   f"TrustSeal: your dispute on {p['O']} was closed: {why}", f"Support reviewed your dispute on {p['P']} ({p['O']}) and closed it.\nReason: {why}")
    elif t == "UNBOX_VERIFIED":
        yield ("SELLER", "info", ("SMS",), "The buyer opened the parcel and the product matched.", f"{p['O']}: buyer confirmed the product",
               f"TrustSeal: the buyer opened {p['O']} and the product matches what you registered. Sale complete.", "")
    elif t == "AI_CHECK_COMPLETED":
        flagged = [r for r in p.get("results", []) if r["status"] == "REVIEW"]
        if p.get("purpose") == "packing_video" and flagged:
            yield ("SELLER", "info", ("SMS",), "Your packing video may need another take.", "Check your packing video",
                   "TrustSeal: your packing video was not fully clear (" + ", ".join(r["check_type"].replace("_", " ") for r in flagged) + "). Consider recording it again.", "")
    elif t == "CHAIN_BROKEN":
        yield ("REVIEWER", "alert", ("SMS", "EMAIL"), "The shared record failed its integrity check.", f"Integrity alert on {p['O']}",
               f"TrustSeal ALERT: the history of {p['O']} failed verification at entry #{p.get('broken_at')}.",
               f"The shared history of {p['P']} ({p['O']}) failed its integrity check at entry #{p.get('broken_at')}: {p.get('reason', '')}\nSomeone may have changed a record.")


def on_event(conn: sqlite3.Connection, sid: str, ev: str, payload: dict, actor_role: str) -> None:
    ctx = _ctx(conn, sid, payload)
    if ev in ("EXCEPTION_REPORTED", "SEAL_REPLACED"):
        cp = conn.execute("SELECT role FROM checkpoints WHERE shipment_id=? AND state IN ('FAILED','IN_PROGRESS') ORDER BY sequence DESC", (sid,)).fetchone()
        ctx["stage_now"] = cp["role"] if cp else ""
    seen = set()
    for role, sev, channels, why, subject, sms, email in rules(conn, sid, ev, ctx, actor_role):
        acc = _account(conn, role, sid)
        if not acc:
            continue
        for ch in channels:
            body = sms if ch == "SMS" else email
            if not body or (acc["id"], ch, subject) in seen:
                continue
            seen.add((acc["id"], ch, subject))
            conn.execute("INSERT INTO notifications (shipment_id, to_id, to_name, to_role, channel, severity, subject, body, why, event_type, created_at) "
                         "VALUES (?,?,?,?,?,?,?,?,?,?,?)", (sid, acc["id"], acc["name"], role, ch, sev, subject, body, why, ev, now_iso()))
            deliver(acc, ch, subject, body)


def deliver(acc, channel: str, subject: str, body: str) -> None:
    """Simulated delivery. Emails are saved as real .eml files so you can open them in a mail app."""
    if channel != "EMAIL":
        return
    try:
        OUTBOX.mkdir(parents=True, exist_ok=True)
        m = EmailMessage()
        m["From"], m["To"], m["Subject"] = "TrustSeal <no-reply@trustseal.demo>", f"{acc['name']} <{acc['id']}@trustseal.demo>", subject
        m.set_content(body)
        (OUTBOX / f"{now_iso().replace(':', '-')}-{acc['id']}.eml").write_bytes(bytes(m))
    except OSError:
        pass


def direct(conn, account_id, channels, severity, subject, sms, email, why, sid=""):
    """One-off message to one person, outside a shipment event (a wallet top-up, for example)."""
    acc = conn.execute("SELECT * FROM accounts WHERE id=?", (account_id,)).fetchone()
    if not acc:
        return
    for ch in channels:
        body = sms if ch == "SMS" else email
        if body:
            conn.execute("INSERT INTO notifications (shipment_id, to_id, to_name, to_role, channel, severity, subject, body, why, event_type, created_at) "
                         "VALUES (?,?,?,?,?,?,?,?,?,?,?)", (sid or "", acc["id"], acc["name"], acc["role"], ch, severity, subject, body, why, "WALLET", now_iso()))
            deliver(acc, ch, subject, body)
