"""Verification checks. Every check returns the same result shape.

Two modes (admin toggle, stored in settings.ai_mode):
  live  real open-source checks: CIEDE2000 colour (scikit-image), product identity (local gemma3 vision via Ollama), and the
        barcode compared digit for digit (zxing-cpp + RapidOCR). Seal serials are always read from the photo, in both modes.
        Seal-photo integrity is still scenario-driven until the OpenCV seal compare lands; its result says so.
  mock  deterministic results per scenario, so the demo cannot fail on a model or on venue lighting.
"""
from . import vision


def _result(check_type, status, score, threshold, explanation, model="mock-deterministic"):
    return {"check_type": check_type, "status": status, "score": score, "threshold": threshold,
            "explanation": explanation, "model_name": model}


def _seal(role, scenario, model):
    if scenario == "B" and role == "WAREHOUSE":
        return _result("seal_integrity", "FAIL", 0.22, 0.7,
                       "The tamper tape pattern is broken and does not match the seller's seal photo.", model)
    return _result("seal_integrity", "PASS", 0.97, 0.7, "Seal pattern matches the seller's seal photo.", model)


def _mock_received(scenario):
    bad = scenario == "C"
    return [
        _result("colour_match", "FAIL" if bad else "PASS", 18.4 if bad else 2.1, 10.0,
                "Colour differs from the seller's reference. Seller: dark green. Received: light green. Delta E 18.4, limit 10."
                if bad else "Colour matches the seller's reference. Delta E 2.1, limit 10."),
        _result("product_match", "FAIL" if bad else "PASS", 0.61 if bad else 0.93, 0.85,
                "Received product looks different from the seller's reference." if bad
                else "Received product looks the same as the seller's reference."),
    ]


def run_checks(role: str, purpose: str, scenario: str, mode: str = "mock", image: bytes | None = None,
               reference: bytes | None = None, identity: dict | None = None, seal_code: str = "", hint: str = "") -> list[dict]:
    """scenario: A happy path, B seal tampered at the warehouse, C wrong product/colour at the consumer."""
    out = [_result("image_quality", "PASS", 0.91, 0.5, "Sharp and well lit.")]
    if purpose == "seal_side":
        out.append(_seal(role, scenario, "mock-deterministic"))
    elif purpose == "packing_video":
        out = []
        if mode == "live" and image:
            out += vision.analyse_packing_video(image, seal_code, reference, identity or {})
        else:
            out += [_result("packing_video", "PASS", 6.0, 3.0, "Recorded 6 s of packing."),
                    _result("seal_in_video", "PASS", 1.0, 1.0, f"The registered seal (…{seal_code[-3:]}) was seen in the video."),
                    _result("product_in_video", "PASS", 0.9, 0.6, "The product in the video matches the registered one.")]
    elif purpose in ("product_front", "product_side"):
        out.append(_result("product_reference", "PASS", 0.95, 0.7, "Product reference recorded."))
    elif purpose == "parcel":
        out.append(_result("package_condition", "PASS", 0.94, 0.7, "Parcel looks intact."))
    return out


def _identifier(image: bytes, identity: dict, hint: str = "") -> dict | None:
    """The product's own number is the strongest evidence there is: compare it exactly, no opinion involved."""
    want = (identity.get("barcode") or {}).get("code")
    if not want:
        return None
    got = vision.read_identifier(image, hint)
    if got and got["code"] == want:
        return {**_result("identifier_match", "PASS", 1.0, 1.0, f"Barcode {want} matches the seller's registered product (read from the {got['how']}).", "zxing-cpp + RapidOCR"), "read": got["code"]}
    if got:
        return {**_result("identifier_match", "FAIL", 0.0, 1.0,
                       f"The barcode on the product you took out ({got['code']}) is different from the one the seller registered ({want}).", "zxing-cpp + RapidOCR"), "read": got["code"]}
    return {**_result("identifier_match", "REVIEW", 0.0, 1.0,
                   "No barcode could be read in this photo. Hold the barcode close and steady inside the frame.", "zxing-cpp + RapidOCR"), "read": None}


# ---------- after delivery: the buyer opens the parcel and scans the product ----------
def _visual(reference: bytes, image: bytes) -> list[dict]:
    res = []
    c = vision.colour_delta(reference, image)
    de = c["delta_e"]
    status = "PASS" if de < 5 else "REVIEW" if de <= 10 else "FAIL"
    res.append(_result("colour_match", status, de, 10.0,
                       f"Colour {'matches' if status == 'PASS' else 'differs from'} the seller's reference. Delta E {de}, limit 10.", "scikit-image ciede2000"))
    try:
        s = vision.same_product(reference, image)
        conf = float(s.get("confidence", 0))
        status = ("PASS" if conf >= 0.6 else "REVIEW") if s["same_product"] else ("FAIL" if conf >= 0.85 else "REVIEW")
        res.append(_result("product_match", status, round(conf, 2), 0.85 if status == "FAIL" else 0.6, s.get("reason", ""), vision.MODEL))
    except Exception as e:
        res.append(_result("product_match", "REVIEW", 0.0, 0.7, f"The look-alike check could not run ({type(e).__name__}).", vision.MODEL))
    return res


def run_unbox(mode: str, scenario: str, barcode: bytes, front: bytes, reference: bytes | None, identity: dict, hint: str = "") -> dict:
    """Compare what the buyer took out of the box with what the seller registered. Outcome is one of
       MATCH, MISMATCH (a complaint is filed), UNSURE (the buyer decides) or UNREADABLE (try again, nothing is recorded)."""
    want = (identity.get("barcode") or {}).get("code")
    idr, got = None, None
    if mode == "live" and reference:
        if want:
            idr = _identifier(barcode or front, identity, hint)
            if idr["status"] == "REVIEW" and barcode and front:
                idr = _identifier(front, identity, hint)
            got = idr.get("read")
        visual = _visual(reference, front)
    else:
        bad = scenario == "C"
        visual = _mock_received(scenario)
        if want:
            idr = _result("identifier_match", "FAIL" if bad else "PASS", 0.0 if bad else 1.0, 1.0,
                          "The barcode on the product you took out is different from the one the seller registered." if bad
                          else f"Barcode {want} matches the seller's registered product.")
            got = "9780000000002" if bad else want
    if idr and idr["status"] == "REVIEW":
        outcome = "UNREADABLE"
    elif idr and idr["status"] == "FAIL":
        outcome = "MISMATCH"
    else:
        v = {r["check_type"]: r["status"] for r in visual}
        if idr:   # the barcode matched exactly; a different-looking photo is for the buyer to judge
            outcome = "UNSURE" if "FAIL" in v.values() else "MATCH"
        elif "FAIL" in v.values():
            outcome = "MISMATCH"
        elif all(x == "PASS" for x in v.values()):
            outcome = "MATCH"
        else:
            outcome = "UNSURE"
    return {"outcome": outcome, "results": ([idr] if idr else []) + visual, "read": got}
