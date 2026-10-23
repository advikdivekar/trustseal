import QRCode from "qrcode";
import type { Snapshot } from "./types";

/* Rehearsal only: stand-ins for the camera when a laptop has none. The server still reads every one of these like a real photo,
   so a demo capture is checked exactly as strictly as a real one. The system never tells anyone a seal's serial: the "physical seal"
   for a rehearsal is a serial this browser invents once per order and keeps in its own storage, the way a sticker lives on a box. */
const rand = (n: number) => Array.from({ length: n }, () => "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"[Math.floor(Math.random() * 32)]).join("");
export function demoSerial(shipmentId: string): string {
  const key = "trustseal.demoseal." + shipmentId;
  try {
    let s = localStorage.getItem(key);
    if (!s) { s = `TS-${rand(4)}-${rand(4)}`; localStorage.setItem(key, s); }
    return s;
  } catch { return `TS-${rand(4)}-${rand(4)}`; }
}

export function demoColourFor(purpose: string, role: string, scenario: Snapshot["settings"]["scenario"]) {
  if (purpose === "unbox_front") return scenario === "C" ? "#8fc46a" : "#2f6b3a";
  if (purpose === "seal_side" || purpose === "parcel") return "#b99a6a";
  return "#2f6b3a";
}

async function qrCanvas(text: string, size: number) {
  const q = document.createElement("canvas");
  await QRCode.toCanvas(q, text, { margin: 2, width: size, color: { dark: "#000000", light: "#ffffff" } });
  return q;
}

export async function demoCapture(purpose: string, role: string, scenario: Snapshot["settings"]["scenario"], serial: string): Promise<Blob> {
  const c = document.createElement("canvas"); c.width = 960; c.height = 720;
  const g = c.getContext("2d")!;
  g.fillStyle = "#e9e4d3"; g.fillRect(0, 0, 960, 720);
  g.fillStyle = demoColourFor(purpose, role, scenario);
  g.fillRect(140, 110, 680, 500);
  g.strokeStyle = "rgba(0,0,0,.12)"; g.lineWidth = 2;
  for (let x = 140; x < 820; x += 18) { g.beginPath(); g.moveTo(x, 110); g.lineTo(x, 610); g.stroke(); }
  if (purpose === "seal_side" || purpose === "parcel") {   // the seal sticker on the box: a QR and its printed serial
    g.fillStyle = "#fff"; g.fillRect(560, 330, 240, 276);
    g.drawImage(await qrCanvas(serial, 220), 570, 336);
    g.fillStyle = "#222"; g.font = "bold 18px monospace"; g.fillText(serial, 578, 588);
  }
  g.fillStyle = "rgba(0,0,0,.55)"; g.font = "20px monospace";
  g.fillText(`DEMO CAPTURE · ${purpose} · ${new Date().toISOString()}`, 24, 40);
  return await new Promise((r) => c.toBlob((b) => r(b!), "image/jpeg", 0.92));
}

// Average colour of a photo, to show the buyer swatches next to the seller's reference.
export async function averageColour(url: string): Promise<[number, number, number]> {
  const img = new Image(); img.crossOrigin = "anonymous"; img.src = url;
  await img.decode();
  const c = document.createElement("canvas"); c.width = c.height = 24;
  const g = c.getContext("2d")!; g.drawImage(img, img.width * 0.2, img.height * 0.2, img.width * 0.6, img.height * 0.6, 0, 0, 24, 24);
  const d = g.getImageData(0, 0, 24, 24).data; let r = 0, gg = 0, b = 0;
  for (let i = 0; i < d.length; i += 4) { r += d[i]; gg += d[i + 1]; b += d[i + 2]; }
  const n = d.length / 4; return [Math.round(r / n), Math.round(gg / n), Math.round(b / n)];
}

/* Rehearsal only: a short synthetic "packing" clip for laptops without a camera. */
export async function demoVideo(serial: string): Promise<Blob> {
  const c = document.createElement("canvas"); c.width = 640; c.height = 480;
  const g = c.getContext("2d")!, stream = c.captureStream(15);
  const qr = await qrCanvas(serial, 150);
  const mime = ["video/webm;codecs=vp9", "video/webm", "video/mp4"].find((m) => MediaRecorder.isTypeSupported(m)) ?? "";
  const rec = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined), parts: Blob[] = [];
  rec.ondataavailable = (e) => e.data.size && parts.push(e.data);
  const done = new Promise<Blob>((r) => { rec.onstop = () => r(new Blob(parts, { type: rec.mimeType || "video/webm" })); });
  rec.start(); const t0 = performance.now();
  await new Promise<void>((resolve) => {
    const draw = () => {
      const t = (performance.now() - t0) / 1000;
      g.fillStyle = "#e9e4d3"; g.fillRect(0, 0, 640, 480);
      g.fillStyle = "#8a6a3c"; g.fillRect(120, 220 + Math.sin(t * 2) * 6, 400, 190);
      g.fillStyle = "#2f6b3a"; g.fillRect(200, 80 + Math.min(t, 2.5) * 50, 240, 100);
      if (t > 1.5) g.drawImage(qr, 440, 250, 150, 150);
      g.fillStyle = "#222"; g.font = "bold 20px monospace"; g.fillText(`DEMO PACKING ${t.toFixed(1)}s`, 20, 40);
      t < 4 ? requestAnimationFrame(draw) : resolve();
    };
    draw();
  });
  rec.stop(); return done;
}
