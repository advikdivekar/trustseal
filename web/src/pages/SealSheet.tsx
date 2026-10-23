import { useEffect, useRef, useState } from "react";
import QRCode from "qrcode";
import { Link } from "react-router-dom";
import { SealMark, NAME } from "../components/Brand";

/* A sheet of tamper-evident seal stickers, to print for a rehearsal. This stands in for a roll from a seal supplier: each sticker
   has its own serial printed on it before it ever reaches the seller. TrustSeal never issues or stores these; the app only ever
   READS the serial off the sticker on the parcel. Stick one on the box, then scan it. */
const ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789";
const serial = () => { const r = (n: number) => Array.from({ length: n }, () => ALPHABET[Math.floor(Math.random() * ALPHABET.length)]).join(""); return `TS-${r(4)}-${r(4)}`; };
const PATCHES = ["#ffffff", "#808080", "#000000", "#d62828", "#2a9d4a", "#1d4ed8"];

function Sticker({ code }: { code: string }) {
  const qr = useRef<HTMLCanvasElement>(null);
  useEffect(() => { if (qr.current) QRCode.toCanvas(qr.current, code, { margin: 1, width: 300, color: { dark: "#000000", light: "#ffffff" } }); }, [code]);
  return (
    <div style={{ width: "100mm", height: "60mm", border: "1px solid #2a1b85", padding: "4mm", display: "grid", gridTemplateColumns: "34mm 1fr", gap: "4mm", position: "relative", color: "#2a1b85", breakInside: "avoid" }}>
      <div><canvas ref={qr} style={{ width: "34mm", height: "34mm" }} />
        <div style={{ fontFamily: "ui-monospace, monospace", fontSize: "3.3mm", marginTop: "2mm", fontWeight: 700 }}>{code}</div></div>
      <div style={{ display: "flex", flexDirection: "column", justifyContent: "space-between" }}>
        <div><div style={{ display: "flex", alignItems: "center", gap: "2mm", fontSize: "4.4mm" }}><SealMark size={34} />{NAME}</div>
          <div style={{ fontSize: "2.7mm", marginTop: "1.5mm" }}>Tamper-evident seal. Scan before accepting. Do not accept if it is broken or peeled.</div></div>
        <div style={{ display: "flex", height: "7mm", border: "0.3mm solid #2a1b85" }}>{PATCHES.map((c) => <i key={c} style={{ flex: 1, background: c }} />)}</div>
        <div style={{ fontSize: "2.4mm" }}>colour reference strip · keep it in the photo</div>
      </div>
      <div style={{ position: "absolute", left: 0, right: 0, bottom: "-9mm", height: "7mm", background: "repeating-linear-gradient(45deg,#2a1b85 0 2mm,#fff 2mm 4mm)", color: "#fff", display: "grid", placeItems: "center", fontSize: "3mm", letterSpacing: "1mm" }}>
        <b style={{ background: "#2a1b85", padding: "0 3mm" }}>VOID IF PEELED</b></div>
    </div>
  );
}

export function SealSheet() {
  const [codes, setCodes] = useState<string[]>(() => Array.from({ length: 4 }, serial));
  return (
    <div className="ts-print" style={{ minHeight: "100vh", padding: 24 }}>
      <div className="ts-noprint" style={{ maxWidth: 560 }}>
        <div className="ts-tiny">Rehearsal supplies</div>
        <h2 className="ts-h2" style={{ color: "#231a6b" }}>Seal stickers</h2>
        <p className="ts-why">Print this page (or show it on a second screen or phone) and put one sticker on the parcel. The seller scans it to register it, and every person after scans the same sticker. Each sticker works for one parcel only. The system does not give out or remember these: it reads them.</p>
        <div className="ts-row" style={{ flexWrap: "wrap" }}>
          <button className="ts-btn" onClick={() => window.print()}>Print</button>
          <button className="ts-btn ghost" onClick={() => setCodes(Array.from({ length: 4 }, serial))}>New stickers</button>
          <Link className="ts-chip" to="/">Back</Link></div>
      </div>
      <div className="ts-seal-sheet">{codes.map((c) => <Sticker key={c} code={c} />)}</div>
    </div>
  );
}
