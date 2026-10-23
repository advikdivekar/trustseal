import { useEffect, useRef, useState } from "react";
import { motion } from "motion/react";
import jsQR from "jsqr";

/* In-app capture only. There is deliberately no file picker: evidence must come from the live camera. */
async function openCamera(audio = false): Promise<MediaStream | null> {
  try {
    if (!navigator.mediaDevices?.getUserMedia) return null;
    return await navigator.mediaDevices.getUserMedia({ video: { facingMode: { ideal: "environment" }, width: { ideal: 1920 }, height: { ideal: 1080 } }, audio });
  } catch { return null; }
}

function useStream() {
  const video = useRef<HTMLVideoElement>(null);
  const [stream, setStream] = useState<MediaStream | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let live: MediaStream | null = null, dead = false;
    openCamera().then((s) => {
      if (dead) return s?.getTracks().forEach((t) => t.stop());
      if (!s) return setFailed(true);
      live = s; setStream(s);
      // Close-up barcodes need the camera to keep refocusing; ignored where the browser or device does not support it.
      s.getVideoTracks()[0]?.applyConstraints({ advanced: [{ focusMode: "continuous" } as any] }).catch(() => {});
    });
    return () => { dead = true; live?.getTracks().forEach((t) => t.stop()); };
  }, []);
  // The <video> only exists once the stream has arrived, so attach it after that render, not inside the promise.
  useEffect(() => {
    const v = video.current;
    if (stream && v) { v.srcObject = stream; v.play().catch(() => {}); }
  }, [stream]);
  return { video, stream, failed };
}

const ghost = { color: "#fbfcef", borderColor: "rgba(251,252,239,.45)" };
const sheet = { initial: { clipPath: "circle(0% at 50% 90%)" }, animate: { clipPath: "circle(150% at 50% 90%)" }, transition: { duration: 0.45, ease: [0.65, 0, 0.35, 1] as const } };

/* BarcodeDetector is native in Chrome and Safari; where it is missing the server still reads the photo. */
const Detector = (globalThis as any).BarcodeDetector as undefined | (new (o: { formats: string[] }) => { detect: (v: CanvasImageSource) => Promise<{ rawValue: string; format: string }[]> });

/* sharpness of the current frame: variance of a Laplacian on a small grey copy. Higher is sharper. */
function sharpness(v: HTMLVideoElement): number {
  const c = document.createElement("canvas"), W = 320, H = Math.round((320 * v.videoHeight) / v.videoWidth);
  c.width = W; c.height = H;
  const g = c.getContext("2d", { willReadFrequently: true })!; g.drawImage(v, 0, 0, W, H);
  const d = g.getImageData(0, 0, W, H).data, gray = new Float32Array(W * H);
  for (let i = 0; i < W * H; i++) gray[i] = d[i * 4] * 0.3 + d[i * 4 + 1] * 0.59 + d[i * 4 + 2] * 0.11;
  let sum = 0, sum2 = 0, n = 0;
  for (let y = 1; y < H - 1; y++) for (let x = 1; x < W - 1; x++) {
    const l = 4 * gray[y * W + x] - gray[y * W + x - 1] - gray[y * W + x + 1] - gray[(y - 1) * W + x] - gray[(y + 1) * W + x];
    sum += l; sum2 += l * l; n++;
  }
  return sum2 / n - (sum / n) ** 2;
}

export function Camera({ hint, nonce, onShot, onDemo, onClose, readBarcode }: {
  hint: string; nonce: string; onShot: (b: Blob, code?: string) => void; onDemo?: () => void; onClose: () => void; readBarcode?: boolean;
}) {
  const { video, stream, failed } = useStream();
  const [code, setCode] = useState("");
  const seen = useRef("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (!readBarcode || !stream || !Detector) return;
    const d = new Detector({ formats: ["ean_13", "ean_8", "upc_a", "upc_e", "code_128"] });
    const id = setInterval(async () => {
      const v = video.current; if (!v || !v.videoWidth) return;
      try { const r = await d.detect(v); if (r[0]) { setCode(r[0].rawValue); seen.current = r[0].rawValue; } } catch { /* frame not ready */ }
    }, 350);
    return () => clearInterval(id);
  }, [readBarcode, stream, video]);
  const shoot = async () => {
    const v = video.current; if (!v || !v.videoWidth || busy) return;
    setBusy(true);
    // For barcodes: take a short burst and keep the sharpest frame, so a moment of hand shake or refocusing does not ruin the shot.
    let best = 0, bestScore = -1;
    const frames: HTMLCanvasElement[] = [];
    for (let i = 0; i < (readBarcode ? 6 : 1); i++) {
      const c = document.createElement("canvas"); c.width = v.videoWidth; c.height = v.videoHeight;
      c.getContext("2d")!.drawImage(v, 0, 0); frames.push(c);
      if (readBarcode) { const sc = sharpness(v); if (sc > bestScore) { bestScore = sc; best = i; } await new Promise((r) => setTimeout(r, 110)); }
    }
    const c = frames[best], g = c.getContext("2d")!;
    g.fillStyle = "rgba(0,0,0,.45)"; g.fillRect(0, c.height - 30, c.width, 30);
    g.fillStyle = "#fff"; g.font = "16px monospace"; g.fillText(`${nonce} · ${new Date().toISOString()}`, 10, c.height - 10);
    c.toBlob((b) => { setBusy(false); b && onShot(b, seen.current || undefined); }, "image/jpeg", 0.93);
  };
  return (
    <motion.div className="ts-cam" {...sheet} exit={{ opacity: 0 }}>
      <div className="hint">{hint}</div>
      {readBarcode && Detector && <div className="ts-cam-chip" style={code ? { background: "#2a9d4a" } : undefined}>{code ? `Barcode read: ${code}` : "Looking for a barcode…"}</div>}
      <div className="view">
        {stream ? <video ref={video} playsInline muted /> : (
          <div style={{ position: "absolute", inset: 0, display: "grid", placeItems: "center", color: "#fbfcef", textAlign: "center", padding: 32 }}>
            {failed ? <div>No camera on this device or connection.<br /><span className="ts-small" style={{ opacity: .7 }}>The camera needs https or localhost.</span></div> : "Opening camera…"}
          </div>
        )}
        {stream && readBarcode && <div className="ts-frame wide"><i /><i /><i /><i /></div>}
      </div>
      <div className="bar">
        <button className="ts-btn ghost" style={ghost} onClick={onClose}>Cancel</button>
        {stream ? <button className="ts-shutter" aria-label="Take photo" onClick={shoot} style={busy ? { opacity: .5 } : undefined} /> : <span />}
        {onDemo ? <button className="ts-btn ghost" style={ghost} onClick={onDemo}>Demo capture</button> : <span style={{ width: 84 }} />}
      </div>
    </motion.div>
  );
}

/* Packing video: the seller films the product going into the box and the seal being closed. */
export function VideoRecorder({ hint, minSec = 3, maxSec = 45, onDone, onDemo, onClose }: {
  hint: string; minSec?: number; maxSec?: number; onDone: (b: Blob) => void; onDemo?: () => void; onClose: () => void;
}) {
  const { video, stream, failed } = useStream();
  const rec = useRef<MediaRecorder | null>(null), parts = useRef<Blob[]>([]);
  const [secs, setSecs] = useState(0), [on, setOn] = useState(false);
  useEffect(() => {
    if (!on) return;
    const t0 = Date.now(), id = setInterval(() => { const s = Math.floor((Date.now() - t0) / 1000); setSecs(s); if (s >= maxSec) stop(); }, 250);
    return () => clearInterval(id);
  }, [on]); // eslint-disable-line react-hooks/exhaustive-deps
  const start = () => {
    if (!stream) return;
    const mime = ["video/webm;codecs=vp9", "video/webm", "video/mp4"].find((m) => MediaRecorder.isTypeSupported(m));
    const r = new MediaRecorder(stream, mime ? { mimeType: mime, videoBitsPerSecond: 2_000_000 } : undefined);
    parts.current = []; r.ondataavailable = (e) => e.data.size && parts.current.push(e.data);
    r.onstop = () => onDone(new Blob(parts.current, { type: r.mimeType || "video/webm" }));
    r.start(500); rec.current = r; setOn(true); setSecs(0);
  };
  const stop = () => { if (rec.current?.state === "recording") rec.current.stop(); setOn(false); };
  return (
    <motion.div className="ts-cam" {...sheet} exit={{ opacity: 0 }}>
      <div className="hint">{on ? `Recording ${secs}s. Close the box, then hold the seal label to the camera.` : hint}</div>
      {on && <div className="ts-cam-chip" style={{ background: "#d8452f" }}>● REC {String(Math.floor(secs / 60)).padStart(2, "0")}:{String(secs % 60).padStart(2, "0")}</div>}
      <div className="view">
        {stream ? <video ref={video} playsInline muted /> : (
          <div style={{ position: "absolute", inset: 0, display: "grid", placeItems: "center", color: "#fbfcef", textAlign: "center", padding: 32 }}>
            {failed ? <div>No camera on this device or connection.<br /><span className="ts-small" style={{ opacity: .7 }}>The camera needs https or localhost.</span></div> : "Opening camera…"}
          </div>
        )}
      </div>
      <div className="bar">
        <button className="ts-btn ghost" style={ghost} onClick={() => { stop(); onClose(); }}>Cancel</button>
        {stream && !on && <button className="ts-shutter" aria-label="Start recording" onClick={start} />}
        {stream && on && <button className="ts-btn" style={{ background: secs >= minSec ? "#d8452f" : "#555", borderColor: "transparent" }} disabled={secs < minSec} onClick={stop}>{secs < minSec ? `Keep filming (${minSec - secs}s)` : "Stop and save"}</button>}
        {!stream && <span />}
        {onDemo ? <button className="ts-btn ghost" style={ghost} onClick={onDemo} disabled={on}>Demo clip</button> : <span style={{ width: 84 }} />}
      </div>
    </motion.div>
  );
}

/* The seal scanner. The seal's serial is read off the sticker on the parcel: live from its QR code, from a photo the server reads, or, when
   nothing else works, typed in from the sticker. Nobody is ever shown the serial by the system, so there is nothing to copy. */
export function Scanner({ onSeal, onPhoto, onDemo, onClose }: {
  onSeal: (code: string, how: "QR" | "typed") => void; onPhoto?: (b: Blob) => void; onDemo?: () => void; onClose: () => void;
}) {
  const { video, stream, failed } = useStream();
  const [typed, setTyped] = useState(""), [manual, setManual] = useState(false), [hit, setHit] = useState(false);
  useEffect(() => {
    if (!stream) return;
    let stopped = false;
    const found = (raw: string) => { if (stopped) return; stopped = true; setHit(true); setTimeout(() => onSeal(raw.trim().toUpperCase(), "QR"), 450); };
    const native = Detector ? new Detector({ formats: ["qr_code", "code_128", "code_39", "data_matrix"] }) : null;
    const c = document.createElement("canvas"), g = c.getContext("2d", { willReadFrequently: true })!;
    const id = setInterval(async () => {
      const v = video.current; if (stopped || !v || !v.videoWidth) return;
      try {
        if (native) { const r = await native.detect(v); if (r[0]) return found(r[0].rawValue); }
        const scale = Math.min(1, 900 / v.videoWidth);
        c.width = v.videoWidth * scale; c.height = v.videoHeight * scale; g.drawImage(v, 0, 0, c.width, c.height);
        const q = jsQR(g.getImageData(0, 0, c.width, c.height).data, c.width, c.height, { inversionAttempts: "attemptBoth" });
        if (q?.data) found(q.data);
      } catch { /* frame not ready */ }
    }, 220);
    return () => { stopped = true; clearInterval(id); };
  }, [stream, video, onSeal]);
  const snap = () => {
    const v = video.current; if (!v || !v.videoWidth || !onPhoto) return;
    const c = document.createElement("canvas"); c.width = v.videoWidth; c.height = v.videoHeight; c.getContext("2d")!.drawImage(v, 0, 0);
    c.toBlob((b) => b && onPhoto(b), "image/jpeg", 0.93);
  };
  return (
    <motion.div className="ts-cam" {...sheet}>
      <div className="hint">Hold the seal's QR code inside the square</div>
      <div className="view">
        {stream ? <video ref={video} playsInline muted /> : <div style={{ position: "absolute", inset: 0, display: "grid", placeItems: "center", color: "#fbfcef", padding: 32, textAlign: "center" }}>{failed ? "No camera here. Type the serial printed on the seal below." : "Opening camera…"}</div>}
        {stream && <div className={"ts-frame" + (hit ? " hit" : "")}><i /><i /><i /><i /></div>}
      </div>
      <div className="bar" style={{ flexDirection: "column", gap: 10, alignItems: "stretch" }}>
        {(manual || failed) ? (
          <div className="ts-row">
            <input value={typed} onChange={(e) => setTyped(e.target.value)} placeholder="Serial printed on the seal" autoCapitalize="characters" autoFocus
                   style={{ flex: 1, background: "rgba(251,252,239,.12)", border: "1px solid rgba(251,252,239,.5)", color: "#fbfcef", padding: "12px", letterSpacing: ".12em", minWidth: 0 }} />
            <button className="ts-btn" style={{ background: "#fbfcef", color: "#231a6b" }} disabled={typed.trim().length < 6} onClick={() => onSeal(typed.trim().toUpperCase(), "typed")}>Use</button>
          </div>
        ) : null}
        <div className="ts-row" style={{ justifyContent: "space-between", flexWrap: "wrap" }}>
          <button className="ts-btn ghost" style={ghost} onClick={onClose}>Cancel</button>
          {stream && onPhoto && <button className="ts-btn ghost" style={ghost} onClick={snap}>Photograph it</button>}
          {!manual && !failed && <button className="ts-btn ghost" style={ghost} onClick={() => setManual(true)}>Type the serial</button>}
          {onDemo && <button className="ts-btn ghost" style={ghost} onClick={onDemo} title="Rehearsal only">Demo seal</button>}
        </div>
      </div>
    </motion.div>
  );
}
