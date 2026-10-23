# TrustSeal

A tamper-evident delivery verification demo. Four phones (Seller, Warehouse, Delivery, Consumer) move one parcel across a
four-span bridge. Each stage locks with **Good to Go**, every action lands in a shared hash-linked ledger, and the buyer pays
only after the parcel checks out. The working name lives in one constant (`web/src/components/Brand.tsx`, `api/app/main.py`).

## Run

```bash
# API (port 8010). Python 3.12 via uv; pip's ensurepip is broken on this machine's 3.15.
cd api && uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python fastapi "uvicorn[standard]" python-multipart pynacl pillow scikit-image numpy zxing-cpp rapidocr-onnxruntime pytest httpx
.venv/bin/uvicorn app.main:app --port 8010

# Web (port 3011, HTTPS with a self-signed certificate, proxies /api, /files, /ws to the API)
cd web && npm install && npm run dev

# Product recognition runs locally on Ollama (open source). One time:  ollama pull gemma3:4b
# ffmpeg must be installed (packing-video analysis):  brew install ffmpeg
```

On the laptop open **`http://localhost:3012`** (`cd web && npm run dev:local`, no certificate warning; localhost counts as secure so the camera works). Phones use `https://<laptop-ip>:3011`; the camera needs HTTPS.
Ports 8000/3001 are left to CertifyAI.

**People:** every person has their own account and PIN (Riya Sharma/Seller 1111, Karan Mehta/Warehouse 2222, Imran Shaikh/Delivery 3333,
Advik Divekar/Buyer 4444). One device is one person; an account can be signed in on one device. Presenter PIN 0000 at `/admin`.
**Rehearsing on one laptop:** in `/admin` turn *Rehearsal* on, then open each person in its own browser tab. Turn it off and press
*Sign everyone out* before the real demo so the one-device-one-person rule applies.

Routes: `/` sign in, `/app` role app, `/screen` projector, `/admin` presenter, `/seals` printable seal stickers (rehearsal supplies).

## What the camera reads

- **Barcode / ISBN** in a second scan step after the front photo. Read the safest ways first: the barcode itself (zxing-cpp), the digits printed under it (RapidOCR, with fragments joined and an ISBN-line cross-check), the phone's own scanner, then zoomed tiles of the photo. Every route must pass the barcode check digit, and a language model is deliberately never a source of digits (it once invented a valid-looking number). The buyer's copy must show the same code (exact match, no model). Titles come from the cover via the local vision model, or from Open Library / Open Food Facts when online and the code is known.
- **The seal is read, never issued.** A tamper-evident sticker with its own printed serial goes on the box (in real life from a seal supplier; for a rehearsal print `/seals`). The seller scans it and the system **reads the serial** (QR via BarcodeDetector/jsQR live, or zxing then OCR on a photo) and registers it to the parcel. Everyone after scans the same sticker and it must match. The system never shows or sends the serial: the shared record and every message carry only a fingerprint and the last three characters. One serial can only ever belong to one parcel, so a reused sticker is refused. If the QR is damaged the serial can be typed from the sticker; that is recorded as "typed".
- **A barcode has to belong to the product that was shown.** The barcode must be read by the camera; typed numbers are refused, and the server only accepts a number it read itself (keyed by the photo's hash), so a phone cannot edit it in. The number is looked up (Open Library, Open Food Facts) and the name it belongs to must agree with the front photo: showing one product and scanning another's barcode is refused. A code that is in no catalogue is accepted but flagged "unverified" for everyone, after a look-alike check of the two photos.
- **Packing video**: the seller films the product going into the box and the seal being closed. The server samples frames (ffmpeg), looks for the product barcode early and this parcel's seal QR late, and flags anything it cannot see for a person to watch. It never fails a parcel on its own.

## Money: the wallet and the door handshake

Pay on delivery, from a pre-loaded **TrustSeal wallet** (demo money). Nothing is charged until the delivery person is at the door and the buyer has scanned the seal on the box (proof it is the parcel that was packed, never opened on the way). The product itself is checked at home afterwards.

- **Wallet:** the buyer's balance (starts at ₹1,000) can be topped up from a linked account with a PIN (demo PIN `2580`). The seller's wallet receives payouts.
- **Balance check at the door.** Enough balance: pay with the PIN. Not enough: an **alarm** goes to the buyer (push with a beep, SMS, email, red card with *Add money* and *Call the delivery person*) and the delivery person gets *Call the buyer* and an SMS.
- **"Paid, but nobody took the parcel" can't happen.** Three rules:
  1. The delivery person must press **I'm at the door**. Until then the buyer cannot pay, and scanning the seal QR on the box never pays, so a stranger with the QR gets nothing.
  2. Paying only **reserves** the money. The buyer's app shows a one-time **handover code** (5 minutes). The seller is paid only when the delivery person types that code.
  3. If nobody confirms in time, the courier cancels (nobody home, wrong person, buyer unreachable), or three wrong codes are entered, the **payment reverses to the wallet automatically** and the parcel stays with the courier. Three failed visits go to the reviewer.
- A dispute cannot be opened while a payment is reserved; ask the courier to cancel the handover. A rejected dispute before delivery charges the buyer's wallet (it may go negative) and pays the seller.

## After delivery: unboxing and the automatic complaint

At home the buyer opens the parcel, **scans the product's barcode** and photographs it. The barcode is compared digit for digit with what the seller registered, the photo with the seller's reference.

- **Match:** the order is closed and the seller is told.
- **Different:** a complaint (`CMP-<order number>`) is **filed for the buyer automatically**, with the scan, the photos, every seal check, the packing video result and a plain-language assessment attached. If the evidence is strong (barcode differs *and* the product looks different, the seal was intact at every stage, the record verifies, the seller's packing video did not show the registered barcode being packed, and the order is under ₹5,000) the **buyer is refunded at once** from the seller's wallet with no one's say-so. Otherwise it goes to the reviewer with the assessment pre-filled. Big orders and contradictory evidence are never decided by a machine.
- **Could not read it:** nothing is recorded, retry. **Not sure:** the buyer, who is holding it, confirms or reports.
- The buyer can also report any other problem (damaged, wrong colour...) from the same screen; it follows the same path. Everyone concerned is told through the notification rules, the warehouse is not.

## Notifications: only the people who must know

Every ledger event runs through a rule that names who is told, by which channel, and why (the message shows "Why you got this"). Action and alert messages go by SMS and email; plain status goes by SMS. Warehouse and delivery are not told about things that do not concern them, and the seller is not pinged about every handover. Examples: a hold tells the stage owner (act), the buyer and the seller; a dispute tells support (act), the seller and the last courier; a short wallet tells the buyer and the courier at the door; tampering tells support only. SMS are simulated; emails are saved as real `.eml` files in `api/data/outbox`. The presenter panel shows the full "who was told what and why" log.

## When something goes wrong (the case system)

1. A wrong seal or failed check puts the stage **on hold** and opens a case naming the stage, the reason and the last verified stage.
2. The **person at that stage gets 3 tries** to fix it themselves (scan the right seal again, retake the photo). A good result lifts the hold. Every try is recorded.
3. Out of tries, or they report it: it goes to the **Reviewer** (Meera, own console). Decisions need a reason and are recorded: *clear and continue*, *ask for a recheck* (with an instruction and fresh tries), *re-seal* (old seal voided, new one scanned), *return to seller* (refund). For buyer disputes: *refund*, *replacement*, *reject*, *return*.
4. The **buyer can accept a flagged parcel anyway** after checking it themselves; the override and their reason are recorded. The AI flags, people decide.
5. Every phone shows a banner: what failed, who has it, what happens next.

## Demo

Seller shows the product to the camera, the local vision model reads it (brand, name, size, colours), the seller corrects anything and registers it.
The product card then follows the parcel to every phone. Presenter panel: scenario A all good, B seal tampered (warehouse photo fails, or scan a
a different sticker at any stage), C the product the buyer scans at home is not the one registered (mock mode). AI *live* uses the real models; AI *mock* is scripted and cannot fail on venue lighting.
Bonus: seller taps "Try to replace a photo" after locking (409), then presenter taps "Simulate DB tampering" and the verifier names the broken event.

## Tests

`cd api && .venv/bin/python -m pytest -q` (57 tests: transitions, locking, 403/409, tamper detection, accounts and device rules, product registration, self-fix, escalation, every reviewer decision, buyer override, barcode checks, the wrong-barcode scam, seals read from photos and never leaked, wallet, door handshake, reversals, unboxing, automatic complaints and refunds, notification targeting).

## What is real and what is mocked

Real: state machine, stage locking, role checks, append-only ledger with SHA-256 links and server Ed25519 signatures, client+server file hashing, QR scan, live sync.
Real (open source, local): product recognition and buyer-side product match (gemma3:4b via Ollama), colour match (CIEDE2000, scikit-image).
Mocked: seal-photo integrity (scenario-driven), SMS and email delivery, money (demo wallet). Real: the seal serial, the barcode and the unboxing comparison are read from the photo in both AI modes.
Known limit: the small vision model can be wordy and wrong on odd photos, so uncertain results go to REVIEW and a person decides; colour is measured from the photo centre without the printed colour strip yet.
