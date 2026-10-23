"""Open-source vision, all local: Ollama (gemma3 vision model) for product identity, scikit-image for colour.

Nothing here leaves the laptop. If Ollama is not running the callers fall back to mock mode.
"""
import base64
import io
import json
import os
import re
import subprocess
import tempfile
from itertools import combinations
import urllib.request
from pathlib import Path

import numpy as np
import zxingcpp
from PIL import Image, ImageOps
from skimage.color import deltaE_ciede2000, rgb2lab

OLLAMA = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
MODEL = os.environ.get("TRUSTSEAL_VLM", "gemma3:4b")

IDENTITY_SCHEMA = {
    "type": "object",
    "properties": {
        "product_name": {"type": "string"},
        "brand": {"type": "string"},
        "variant": {"type": "string"},
        "category": {"type": "string"},
        "net_quantity": {"type": "string"},
        "colours": {"type": "array", "items": {"type": "string"}},
        "visible_text": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "number"},
    },
    "required": ["product_name", "brand", "category", "confidence"],
}
SAME_SCHEMA = {
    "type": "object",
    "properties": {"same_product": {"type": "boolean"}, "confidence": {"type": "number"}, "reason": {"type": "string"}},
    "required": ["same_product", "confidence", "reason"],
}


def available() -> bool:
    try:
        with urllib.request.urlopen(OLLAMA + "/api/tags", timeout=1.5) as r:
            return any(m["name"] == MODEL for m in json.load(r)["models"])
    except Exception:
        return False


def _unmark(img: Image.Image) -> Image.Image:
    """Our camera burns a nonce and timestamp into the bottom strip of every photo. It is not part of the product,
       and its digits can look like a barcode number, so models and OCR never see it."""
    w, h = img.size
    return img.crop((0, 0, w, int(h * 0.955)))


def _shrink(data: bytes, side: int = 768) -> str:
    img = _unmark(Image.open(io.BytesIO(data)).convert("RGB"))
    img.thumbnail((side, side))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=88)
    return base64.b64encode(buf.getvalue()).decode()


def _ask(prompt: str, images: list[bytes], schema: dict, timeout: int = 120) -> dict:
    body = {"model": MODEL, "stream": False, "format": schema, "options": {"temperature": 0},
            "messages": [{"role": "user", "content": prompt, "images": [_shrink(i) for i in images]}]}
    req = urllib.request.Request(OLLAMA + "/api/chat", json.dumps(body).encode(), {"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(json.load(r)["message"]["content"])


QTY = re.compile(r"^\d+([.,]\d+)?\s?(g|gm|kg|mg|ml|l|ltr|oz|lb|pcs?|pack|n|count)$", re.I)
CACHE = Path(os.environ.get("TRUSTSEAL_DATA") or Path(__file__).resolve().parent.parent / "data") / "barcode_cache.json"


def _variants(img: Image.Image):
    yield img
    g = ImageOps.autocontrast(ImageOps.grayscale(img))
    yield g
    w, h = g.size
    if max(w, h) < 1800:
        yield g.resize((int(w * 1.6), int(h * 1.6)))


def decode_codes(data: bytes) -> list[dict]:
    """Read every barcode and QR in a photo. This is ground truth: no model guesses a barcode."""
    try:
        img = Image.open(io.BytesIO(data)).convert("RGB")
    except Exception:
        return []
    for v in _variants(img):
        found = zxingcpp.read_barcodes(v)
        if found:
            return [{"format": r.format.name, "text": r.text} for r in found]
    return []


def is_isbn(code: str) -> bool:
    return len(code) == 13 and code[:3] in ("978", "979")


def checksum_ok(code: str) -> bool:
    """EAN-13 / EAN-8 / UPC-A check digit. A misread digit fails this, so a wrong number never gets through."""
    if not code.isdigit() or len(code) not in (8, 12, 13):
        return False
    d = [int(c) for c in code]
    body, last = d[:-1], d[-1]
    total = sum(x * (3 if (len(body) - i) % 2 else 1) for i, x in enumerate(body))
    return (10 - total % 10) % 10 == last


_OCR = None


def _ocr_texts(data: bytes) -> list[str]:
    """Open-source OCR (RapidOCR, ONNX, runs on the laptop). Reads the digits printed under a barcode."""
    global _OCR
    try:
        from rapidocr_onnxruntime import RapidOCR
        _OCR = _OCR or RapidOCR()
        img = np.asarray(_unmark(Image.open(io.BytesIO(data)).convert("RGB")))
        res, _ = _OCR(img)
        return [r[1] for r in (res or [])]
    except Exception:
        return []


DIGITS = re.compile(r"(?<!\d)(?:\d[\s\-]?){7,13}\d(?!\d)")


def _valid_in(texts: list[str]) -> str | None:
    """Look through what OCR saw for a 13-digit number that passes the check digit. The digits under a barcode come back in
       pieces ("9788119", "153565"), so neighbouring pieces are joined too. A misread digit fails the check and is dropped."""
    digits = [re.sub(r"\D", "", t) for t in texts]
    found = [re.sub(r"\D", "", m.group()) for m in DIGITS.finditer("  ".join(texts))] + digits
    for i in range(len(digits)):
        for j in (2, 3):
            found.append("".join(digits[i:i + j]))
    ok = [c for c in dict.fromkeys(found) if len(c) == 13 and checksum_ok(c) and not re.match(r"20\d{2}[01]\d[0-3]\d", c)]
    ok.sort(key=lambda c: not is_isbn(c))
    return ok[0] if ok else _recover_isbn(texts, digits)


def _recover_isbn(texts: list[str], digits: list[str]) -> str | None:
    """OCR often drops a stray digit into the line under the bars (the long guard bars look like a '1'). Recover the real
       ISBN by deleting up to three digits, but only trust a result that (a) starts 978/979, (b) passes the check digit and
       (c) agrees within two digits with the separately printed 'ISBN 978-81-...' line. All three together are not luck."""
    refs = [d for t, d in zip(texts, digits) if re.search(r"SBN", t, re.I) and len(d) in (13, 14)]
    refs = [r[-13:] if r[:3] in ("978", "979") or len(r) == 13 else r[-13:] for r in refs]
    if not refs:
        return None
    best = None
    for line in (d for d in digits if 14 <= len(d) <= 17):
        for drop in combinations(range(len(line)), len(line) - 13):
            cand = "".join(c for i, c in enumerate(line) if i not in drop)
            if not (is_isbn(cand) and checksum_ok(cand)):
                continue
            gap = min(sum(a != b for a, b in zip(cand, r)) for r in refs)
            if gap <= 2 and (best is None or gap < best[0]):
                best = (gap, cand)
    return best[1] if best else None


def _tiles(data: bytes):
    """Zoomed, contrast-boosted crops across the photo. A barcode held at arm's length is only a few hundred pixels wide;
       a scanner or OCR finds it easily once it fills the crop."""
    try:
        img = _unmark(Image.open(io.BytesIO(data)).convert("RGB"))
    except Exception:
        return
    w, h = img.size
    cw, ch = int(w * 0.5), int(h * 0.5)
    for y in (0, int(h * 0.25), h - ch):
        for x in (0, int(w * 0.25), w - cw):
            crop = ImageOps.autocontrast(img.crop((x, y, x + cw, y + ch)), cutoff=1)
            crop = crop.resize((int(crop.width * 1.8), int(crop.height * 1.8)), Image.LANCZOS)
            buf = io.BytesIO()
            crop.save(buf, "JPEG", quality=92)
            yield buf.getvalue()


def _kind(code: str) -> str:
    return "EAN13" if len(code) == 13 else "EAN8" if len(code) == 8 else "UPCA"


def read_identifier(data: bytes, hint: str = "") -> dict | None:
    """The product's own number, read the most reliable ways available. Every route is guarded by the check digit, and a
       language model is deliberately NOT one of them: it can invent digits that happen to pass.
       1. the barcode itself  2. the digits printed under it (OCR)  3. the device camera's scanner  4. zoomed tiles of both."""
    for c in decode_codes(data):
        if c["format"] not in ("QRCode", "MicroQRCode"):
            return {"code": c["text"], "format": c["format"], "how": "barcode"}
    code = _valid_in(_ocr_texts(data))
    if code:
        return {"code": code, "format": _kind(code), "how": "printed digits"}
    hint = re.sub(r"\D", "", hint or "")
    if checksum_ok(hint):
        return {"code": hint, "format": _kind(hint), "how": "device scanner"}
    for tile in _tiles(data):
        for c in decode_codes(tile):
            if c["format"] not in ("QRCode", "MicroQRCode"):
                return {"code": c["text"], "format": c["format"], "how": "barcode (zoomed)"}
        code = _valid_in(_ocr_texts(tile))
        if code:
            return {"code": code, "format": _kind(code), "how": "printed digits (zoomed)"}
    return None


def lookup(code: str) -> dict:
    """Best effort name lookup from free public catalogues. Offline or unknown is fine: the code itself is the identity."""
    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    if code in cache:
        return cache[code]
    out: dict = {}
    try:
        if is_isbn(code):
            url = f"https://openlibrary.org/api/books?bibkeys=ISBN:{code}&format=json&jscmd=data"
            with urllib.request.urlopen(url, timeout=3) as r:
                d = json.load(r).get(f"ISBN:{code}")
            if d:
                out = {"title": d.get("title", ""), "by": ", ".join(a["name"] for a in d.get("authors", [])), "source": "Open Library"}
        else:
            url = f"https://world.openfoodfacts.org/api/v2/product/{code}.json?fields=product_name,brands,quantity"
            with urllib.request.urlopen(url, timeout=3) as r:
                d = json.load(r)
            if d.get("status") == 1:
                p = d["product"]
                out = {"title": p.get("product_name", ""), "by": p.get("brands", ""), "quantity": p.get("quantity", ""), "source": "Open Food Facts"}
    except Exception:
        return {}  # do not cache a network failure
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    cache[code] = out
    CACHE.write_text(json.dumps(cache))
    return out


def identify(image: bytes, hint: str = "") -> dict:
    ident = read_identifier(image, hint)
    out = _ask(
        "You are registering a product for a delivery verification system. Identify the product being held up. "
        "For a BOOK: the title is the largest text on the FRONT cover and the author is the name printed on the cover as the author "
        "(usually at the bottom or under the title). IGNORE endorsement quotes, blurbs, review snippets, publisher text and the names of "
        "other books or people mentioned in smaller text. For packaged goods: the brand and product name as printed on the front. "
        "Give the category in one word, the net quantity ONLY if a weight or volume is printed (otherwise an empty string), the main "
        "colours and other readable text. Do not invent anything. Confidence is 0 to 1.", [image], IDENTITY_SCHEMA)
    if not QTY.match((out.get("net_quantity") or "").strip()):
        out["net_quantity"] = ""
    out["model"] = MODEL
    if ident:
        code = ident["code"]
        out["barcode"] = {"code": code, "format": ident["format"], "isbn": is_isbn(code), "how": ident["how"]}
        found = lookup(code)
        if found.get("title"):
            out["product_name"], out["brand"] = found["title"], found.get("by") or out.get("brand", "")
            out["name_source"] = found["source"]
            if found.get("quantity") and not out["net_quantity"]:
                out["net_quantity"] = found["quantity"]
        else:
            out["name_source"] = "read from the packaging"
        out["confidence"] = max(float(out.get("confidence", 0) or 0), 0.95)
    return out


def read_barcode_only(image: bytes, hint: str = "") -> dict | None:
    """Step two of registering a product: only the number, no guessing about the name."""
    ident = read_identifier(image, hint)
    if not ident:
        return None
    code = ident["code"]
    out = {"code": code, "format": ident["format"], "isbn": is_isbn(code), "how": ident["how"]}
    found = lookup(code)
    if found.get("title"):
        out["title"], out["by"], out["source"] = found["title"], found.get("by", ""), found["source"]
    return out


def same_product(seller: bytes, received: bytes) -> dict:
    out = _ask(
        "Image 1 is the product a seller packed. Image 2 is what the buyer received. Decide whether they show the same product: "
        "same brand, same product, same flavour or variant, same packaging design. Ignore lighting, angle and background. "
        "Ignore any text that was added on top of the photo by the camera app, such as timestamps, capture IDs, seal labels, "
        "'demo capture' banners or small white label boxes: those are not part of the product. "
        "Judge only the product itself. Be strict about brand and variant. Give a reason that agrees with your answer. "
        "Confidence is 0 to 1.", [seller, received], SAME_SCHEMA)
    out["model"] = MODEL
    return out


def _centre_lab(data: bytes) -> np.ndarray:
    img = Image.open(io.BytesIO(data)).convert("RGB")
    w, h = img.size
    img = img.crop((int(w * .3), int(h * .3), int(w * .7), int(h * .7))).resize((32, 32))
    return rgb2lab(np.asarray(img, dtype=float) / 255.0).reshape(-1, 3).mean(axis=0)


def colour_delta(ref: bytes, got: bytes) -> dict:
    a, b = _centre_lab(ref), _centre_lab(got)
    de = float(deltaE_ciede2000(a[None, None, :], b[None, None, :])[0, 0])
    return {"delta_e": round(de, 1), "ref_lab": [round(float(x)) for x in a], "got_lab": [round(float(x)) for x in b]}


def _frames(video: bytes, per_second: int = 2, cap: int = 90) -> list[bytes]:
    with tempfile.TemporaryDirectory() as d:
        src = Path(d) / "in.bin"
        src.write_bytes(video)
        subprocess.run(["ffmpeg", "-v", "error", "-i", str(src), "-vf", f"fps={per_second},scale='min(1280,iw)':-2", "-frames:v", str(cap),
                        str(Path(d) / "f_%03d.jpg")], check=False, timeout=60)
        return [p.read_bytes() for p in sorted(Path(d).glob("f_*.jpg"))]


def analyse_packing_video(video: bytes, seal_code: str, reference: bytes | None, identity: dict) -> list[dict]:
    """Did the seller really pack THIS product and close THIS seal on camera? Deterministic codes first, model second."""
    frames = _frames(video)
    seconds = len(frames) / 2
    seen_seal, seen_code = None, None
    want = (identity.get("barcode") or {}).get("code")
    for i, f in enumerate(frames):
        for c in decode_codes(f):
            if seal_code and norm_seal(c["text"]) == seal_code and seen_seal is None:
                seen_seal = i
            if want and c["text"] == want and seen_code is None:
                seen_code = i
    out = [{"check_type": "packing_video", "status": "PASS" if seconds >= 3 else "REVIEW", "score": seconds, "threshold": 3.0,
            "explanation": f"Recorded {seconds:.0f} s of packing." if seconds >= 3 else "The video is too short to show the packing. Record at least 3 seconds.",
            "model_name": "ffmpeg"}]
    out.append({"check_type": "seal_in_video", "status": "PASS" if seen_seal is not None else "REVIEW", "score": 1.0 if seen_seal is not None else 0.0,
                "threshold": 1.0, "model_name": "zxing-cpp",
                "explanation": f"The registered seal (…{seal_code[-3:]}) was seen in the video at {seen_seal / 2:.0f} s." if seen_seal is not None
                else ("Register the seal first, then film the packing." if not seal_code else
                      "The seal label was not readable in the video. Hold it to the camera when you close the box.")})
    if want and seen_code is not None:
        out.append({"check_type": "product_in_video", "status": "PASS", "score": 1.0, "threshold": 1.0, "model_name": "zxing-cpp",
                    "explanation": f"The product's barcode {want} was seen in the video at {seen_code / 2:.0f} s."})
    elif reference and frames:
        try:
            r = same_product(reference, frames[min(len(frames) - 1, len(frames) // 4)])
            ok = r["same_product"] and float(r.get("confidence", 0)) >= 0.6
            out.append({"check_type": "product_in_video", "status": "PASS" if ok else "REVIEW", "score": round(float(r.get("confidence", 0)), 2),
                        "threshold": 0.6, "model_name": MODEL,
                        "explanation": r.get("reason", "") or ("The product in the video matches the registered one." if ok else "The product in the video does not clearly match.")})
        except Exception as e:
            out.append({"check_type": "product_in_video", "status": "REVIEW", "score": 0.0, "threshold": 0.6, "model_name": MODEL,
                        "explanation": f"Could not compare the video with the product ({type(e).__name__}). A person should watch it."})
    return out


# ---------- the physical seal: its serial is read from the parcel, never issued by us ----------
SERIAL = re.compile(r"^[A-Z0-9]{2,8}(?:-[A-Z0-9]{2,8}){1,3}$")


def norm_seal(raw: str) -> str | None:
    """A seal serial as printed on the sticker. Our own sticker format is found even inside a URL."""
    s = re.sub(r"\s+", "", raw or "").upper()
    m = re.search(r"TS-[0-9A-Z]{4}-[0-9A-Z]{4}", s)
    s = m.group() if m else s
    return s if 6 <= len(s) <= 40 and re.fullmatch(r"[A-Z0-9][A-Z0-9\-_.:/]*", s) else None


def _serial_text(text: str) -> str | None:
    t = re.sub(r"[^A-Z0-9\-]", "", text.upper().replace("\u2013", "-"))
    if SERIAL.match(t) and re.search(r"[A-Z]", t) and re.search(r"\d", t) and not re.match(r"\d{4}-\d{2}-\d{2}", t):
        return t
    return None


def read_seal(data: bytes, hint: str = "", avoid: str = "") -> dict | None:
    """The serial on the seal sticker in this photo: its QR or barcode first, then the printed text. The product's own
       barcode (`avoid`) is never mistaken for it, and a number-only EAN is the last thing we would call a seal."""
    def pick(codes):
        ranked = sorted(codes, key=lambda c: c["format"] in ("EAN13", "EAN8", "UPCA", "UPCE"))
        for c in ranked:
            if c["text"] == avoid:
                continue
            s = norm_seal(c["text"])
            if s and not (c["format"] in ("EAN13", "EAN8", "UPCA") and checksum_ok(s)):
                return {"code": s, "how": "QR" if "QR" in c["format"] else "barcode"}
        return None
    got = pick(decode_codes(data))
    if got:
        return got
    h = norm_seal(hint)
    if h and h != avoid:
        return {"code": h, "how": "device scanner"}
    for tile in _tiles(data):
        got = pick(decode_codes(tile))
        if got:
            return {**got, "how": got["how"] + " (zoomed)"}
    for texts in (_ocr_texts(data),):
        for t in texts:
            s = _serial_text(t)
            if s and s != avoid:
                return {"code": s, "how": "printed serial"}
    return None


# ---------- does the barcode agree with the product the camera saw? ----------
STOP = {"the", "and", "for", "with", "from", "new", "pack", "edition", "book", "books", "by", "paperback", "hardcover", "ltd", "pvt"}


def _tok(*parts) -> set[str]:
    words = re.findall(r"[a-z0-9]+", " ".join(str(p) for p in parts if p).lower())
    return {w for w in words if len(w) >= 3 and w not in STOP}


def cross_check(photo: dict, found: dict, barcode_photo: bytes | None = None, front: bytes | None = None) -> dict:
    """The scam this stops: showing one product to the camera and scanning the barcode of another. The barcode is looked up in
       the public catalogues and the name it belongs to must agree with what the front photo shows."""
    if found.get("title"):
        a = _tok(photo.get("product_name"), photo.get("brand"), *(photo.get("visible_text") or []))
        b = _tok(found["title"], found.get("by"))
        hit = sorted(a & b)
        need = min(2, len(b))
        base = {"catalog_title": found["title"], "catalog_by": found.get("by", ""), "source": found.get("source", "")}
        if b and len(hit) >= need:
            return {"status": "match", "hit": hit, **base, "detail": f"The barcode belongs to “{found['title']}”, which is what the photo shows."}
        return {"status": "mismatch", "hit": hit, **base,
                "detail": f"That barcode belongs to “{found['title']}”{' by ' + found['by'] if found.get('by') else ''}, "
                          f"but the photo shows “{photo.get('product_name') or 'something else'}”. Show the barcode that is on this product."}
    if barcode_photo and front:   # not in any catalogue: at least check both photos show the same kind of product
        try:
            s = same_product(front, barcode_photo)
            if not s["same_product"] and float(s.get("confidence", 0)) >= 0.9:
                return {"status": "mismatch", "detail": "The product next to this barcode does not look like the product in the front photo. " + s.get("reason", "")}
        except Exception:
            pass
    return {"status": "unverified", "detail": "This barcode is not in the public catalogues, so the product could not be confirmed independently. It is recorded as unverified."}
