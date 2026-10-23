import { AnimatePresence, motion } from "motion/react";
import { useEffect, useState } from "react";
import { Navigate, useNavigate } from "react-router-dom";
import { LiveScene } from "../components/Live";
import { Camera, Scanner, VideoRecorder } from "../components/Camera";
import { ChainBadge, LedgerSheet } from "../components/Ledger";
import { Bell, InboxSheet, Toasts } from "../components/Inbox";
import { CodeCard, PinSheet, ShortAlarm, WalletChip, WalletSheet, mmss, rs, useCountdown, useWallet, Call, type WalletData } from "../components/Wallet";
import { Slide } from "../components/Slide";
import { Wipe } from "../components/Wipe";
import { Brand } from "../components/Brand";
import { api, isRehearsal, loadSession, signOut } from "../lib/api";
import { demoCapture, demoSerial, demoVideo } from "../lib/demoimg";
import { sha256 } from "../lib/hash";
import { setSnap, useLive } from "../lib/store";
import { PURPOSE_LABEL, ROLE_LABEL, STAGES, STEP_LABEL, type Check, type Checkpoint, type Evidence, type Identity, type Snapshot, type Stage } from "../lib/types";
import { ReviewerApp } from "./Resolution";

export type Ctx = { snap: Snapshot; cp: Checkpoint; run: <T>(f: () => Promise<T>) => Promise<T | undefined>; wallet: WalletData | null; openWallet: () => void };
export const money = (n: number) => "₹" + n.toLocaleString("en-IN");
export const verdict = (cs?: Check[]) => {
  const live = cs?.filter((c) => !c.overridden);
  return !live?.length ? "none" : live.some((c) => c.status === "FAIL") ? "fail" : live.some((c) => c.status === "REVIEW") ? "review" : "pass";
};
const nonce = () => "TS-" + Math.random().toString(36).slice(2, 8).toUpperCase();

export function RoleApp() {
  const session = loadSession();
  if (!session || session.participant.role === "ADMIN") return <Navigate to="/" />;
  return session.participant.role === "REVIEWER" ? <ReviewerApp /> : <StageApp />;
}

/* a photo, or a small video, of a piece of evidence */
export function Thumb({ e }: { e: Evidence }) {
  return e.kind === "video" ? <video className="ts-vthumb" src={e.url} muted playsInline controls preload="metadata" /> : <img className="ts-thumb" src={e.url} alt="" />;
}

function StageApp() {
  const session = loadSession()!;
  const nav = useNavigate();
  const { snap, online } = useLive();
  const [ledger, setLedger] = useState(false);
  const [inbox, setInbox] = useState(false);
  const [walletOpen, setWalletOpen] = useState(false);
  const [err, setErr] = useState("");
  useEffect(() => { if (err) { const t = setTimeout(() => setErr(""), 7000); return () => clearTimeout(t); } }, [err]);
  const role = session.participant.role as Stage;
  const hasWallet = role === "CONSUMER" || role === "SELLER";
  const { wallet, reload } = useWallet(hasWallet);
  const cp = snap?.checkpoints.find((c) => c.role === role);
  const run = async <T,>(f: () => Promise<T>) => { try { setErr(""); return await f(); } catch (e: any) { setErr(e.message); } };

  return (
    <div className="ts-app"><div className="ts-app-col">
      <Toasts onOpen={() => setInbox(true)} />
      <div className="ts-app-bar">
        <Brand />
        <div className="ts-row">
          <span title={online ? "live" : "reconnecting"} style={{ width: 10, height: 10, borderRadius: "50%", background: online ? "var(--ts-ink)" : "var(--ts-red)" }} />
          <Bell onOpen={() => setInbox(true)} />
          {hasWallet && <WalletChip balance={wallet?.balance} onOpen={() => setWalletOpen(true)} />}
          <button className="ts-chip" onClick={async () => { await signOut(); nav("/"); }}>{session.participant.name.split(" ")[0]} · sign out</button>
        </div>
      </div>
      <div className="ts-app-scene"><LiveScene snap={snap} still fit="strip" /></div>
      <div className="ts-app-main">
        <AnimatePresence>{err && (
          <motion.div key={err} initial={{ opacity: 0, y: -8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} className="ts-card"
                      style={{ borderColor: "var(--ts-red)", color: "var(--ts-red)", marginBottom: 12 }} role="alert">{err}</motion.div>)}
        </AnimatePresence>
        {!snap || !cp ? <Empty role={role} run={run} /> : (
          <>
            <Stepper snap={snap} />
            <ProductCard snap={snap} />
            <CaseBanner snap={snap} role={role} />
            {role !== "CONSUMER" && <ComplaintCard snap={snap} />}
            <Terminal snap={snap} />
            <PayStatus snap={snap} role={role} wallet={wallet} openWallet={() => setWalletOpen(true)} />
            <StagePanel ctx={{ snap, cp, run, wallet, openWallet: () => setWalletOpen(true) }} role={role} />
          </>
        )}
      </div>
      {snap && (
        <div className="ts-dock"><div className="ts-dock-in">
          <div className="ts-between">
            <div className="ts-dock-name">
              <div style={{ fontWeight: 600 }}>{snap.shipment.product_name}</div>
              <div className="ts-tiny">{snap.shipment.order_ref} · {money(snap.payment.amount)} · {PAY_LABEL[snap.payment.state]}</div>
            </div>
            <div className="ts-row"><ChainBadge snap={snap} /><button className="ts-btn ghost" onClick={() => setLedger(true)}>History</button></div>
          </div>
        </div></div>
      )}
      <AnimatePresence>
        {ledger && snap && <LedgerSheet snap={snap} onClose={() => setLedger(false)} />}
        {inbox && <InboxSheet onClose={() => setInbox(false)} />}
        {walletOpen && hasWallet && <WalletSheet role={role} wallet={wallet} reload={reload} onClose={() => setWalletOpen(false)} />}
      </AnimatePresence>
    </div></div>
  );
}

const PAY_LABEL: Record<string, string> = { PENDING: "pay at the door", RESERVED: "reserved, awaiting handover", RELEASED: "paid", REFUNDED: "refunded", CANCELLED: "not charged", DISPUTE_HOLD: "on hold" };

/* what the money is doing, in words, for whoever is looking */
function PayStatus({ snap, role, wallet, openWallet }: { snap: Snapshot; role: Stage; wallet: WalletData | null; openWallet: () => void }) {
  const p = snap.payment, h = snap.handover, a = rs(p.amount);
  if (role === "CONSUMER") {
    if (p.state === "RESERVED" && h) return <CodeCard snap={snap} />;
    if (p.state === "PENDING" && h?.state === "OPEN" && (h.short_by > 0 || (wallet && wallet.balance < p.amount))) return <ShortAlarm short={h.short_by || p.amount - (wallet?.balance ?? 0)} h={h} role="CONSUMER" onAdd={openWallet} />;
    if (p.state === "PENDING") {
      const short = wallet && wallet.balance < p.amount;
      return (
        <div className="ts-card" style={{ marginBottom: 16, borderColor: short ? "var(--ts-ochre)" : undefined }}>
          <div className="ts-between"><span className="ts-tiny">Pay on delivery · from your wallet</span><span className="ts-chip">{a}</span></div>
          <div style={{ fontWeight: 700, margin: "6px 0 2px" }}>Wallet {wallet ? rs(wallet.balance) : "…"}</div>
          <div className="ts-why" style={{ marginTop: 0 }}>{short ? <b style={{ color: "var(--ts-ochre)" }}>Your wallet is short by {rs(p.amount - wallet!.balance)}. Add money before the delivery person arrives so the delivery is not held up.</b> : "Nothing is charged until the delivery person is at your door and you have scanned the seal."}</div>
          {short && <button className="ts-btn block" style={{ marginTop: 10 }} onClick={openWallet}>Add money</button>}
        </div>
      );
    }
    if (p.state === "REFUNDED") return <div className="ts-card" style={{ marginBottom: 16 }}><span className="ts-tiny">Refund</span><div style={{ fontWeight: 700, margin: "4px 0" }}>{a} returned to your wallet</div>
      <div className="ts-why" style={{ marginTop: 0 }}>Your complaint was upheld. Wallet balance {wallet ? rs(wallet.balance) : "…"}.</div></div>;
    if (p.state === "RELEASED") {
      const mine = p.txns.find((t) => t.kind === "RESERVE");
      return <div className="ts-card" style={{ marginBottom: 16 }}><span className="ts-tiny">Receipt</span><div style={{ fontWeight: 700, margin: "4px 0" }}>{a} paid from your wallet</div>
        <div className="ts-why" style={{ marginTop: 0 }}>The seller was paid after {h?.agent_name} confirmed the handover with your code.{mine ? ` Ref ${mine.reference}.` : ""}</div></div>;
    }
  }
  if (role === "SELLER") {
    const copy: Record<string, [string, string]> = {
      PENDING: [`${a} to come`, "The buyer scans the seal and pays from their wallet at the door. You are paid when the handover is confirmed."],
      RESERVED: [`${a} reserved`, "The buyer has paid. You are paid when the delivery person confirms the handover with the buyer's code."],
      RELEASED: [`${a} paid to your wallet`, "The handover was confirmed."], REFUNDED: [`${a} refunded to the buyer`, "A complaint on this order was upheld, so the payout was taken back."], CANCELLED: ["No sale", "The order closed without a charge."], DISPUTE_HOLD: [`${a} on hold`, "A dispute is being reviewed."] };
    const [t, d] = copy[p.state] ?? ["", ""];
    return <div className="ts-card" style={{ marginBottom: 16 }}><span className="ts-tiny">Your payment</span><div style={{ fontWeight: 700, margin: "4px 0" }}>{t}</div><div className="ts-why" style={{ marginTop: 0 }}>{d}</div></div>;
  }
  return null;
}

export function Stepper({ snap }: { snap: Snapshot }) {
  const done = snap.shipment.state === "ACCEPTED";
  return (
    <div className="ts-stepper" aria-label="Order progress">
      {snap.checkpoints.map((c, i) => {
        const cls = c.state === "LOCKED" || done ? "done" : c.state === "FAILED" ? "bad" : c.state === "IN_PROGRESS" ? "now" : "";
        return <div key={c.id} className={"ts-step " + cls}><div className="c">{cls === "done" ? "✓" : cls === "bad" ? "!" : i + 1}</div>{STEP_LABEL[c.role]}</div>;
      })}
    </div>
  );
}

export function ProductCard({ snap }: { snap: Snapshot }) {
  const id = snap.shipment.identity, photo = snap.checkpoints[0].evidence.find((e) => e.purpose === "product_front");
  const conf = id.confidence ? Math.round(id.confidence * 100) : null;
  return (
    <div className="ts-product">
      {photo ? <img src={photo.url} alt={id.product_name} /> : <div style={{ background: "var(--ts-paper-2)" }} />}
      <div>
        <div className="ts-tiny">{snap.shipment.order_ref}</div>
        <div className="t">{id.product_name || snap.shipment.product_name}</div>
        <div className="ts-why" style={{ marginTop: 0 }}>{[id.brand, id.variant, id.net_quantity].filter(Boolean).join(" · ")}</div>
        <div className="ts-tags">
          {id.category && <span className="ts-tag">{id.category}</span>}
          {id.barcode && <span className="ts-tag" style={{ borderColor: "var(--ts-ink-deep)" }}>{id.barcode.isbn ? "ISBN" : "Barcode"} {id.barcode.code}</span>}
          {id.colours?.slice(0, 2).map((c) => <span key={c} className="ts-tag">{c}</span>)}
          {conf !== null && id.model !== "seeded" && <span className="ts-tag">recognised {conf}%</span>}
          {id.verification && <span className="ts-tag" style={{ borderColor: id.verification.status === "match" ? "var(--ts-ink-deep)" : "var(--ts-ochre)" }}>{id.verification.status === "match" ? "barcode confirmed" : "barcode unverified"}</span>}
        </div>
      </div>
    </div>
  );
}

/* ---------- case: what went wrong, who has it, what happens next ---------- */
export function CaseBanner({ snap, role }: { snap: Snapshot; role?: string }) {
  const c = snap.case;
  if (!c.open) return null;
  const now = c.state === "ON_HOLD" ? 1 : 2;
  const title = c.kind === "DISPUTE" ? (c.state === "DISPUTED" ? "Complaint open" : "Complaint under review") : c.state === "ON_HOLD" ? "Verification hold" : "Under review";
  return (
    <motion.div className="ts-card" initial={{ opacity: 0, scale: .98 }} animate={{ opacity: 1, scale: 1 }} style={{ borderColor: "var(--ts-red)", marginBottom: 16 }}>
      <div className="ts-between"><span className="ts-chip bad">{title}</span><span className="ts-tiny">{ROLE_LABEL[c.stage!]} stage</span></div>
      <p style={{ margin: "10px 0 2px", fontWeight: 600 }}>{c.kind === "DISPUTE" ? (c.auto ? `Complaint ${c.ticket} was filed automatically when the product scanned at home did not match.` : `The buyer reported a problem (${c.ticket}).`) : <>Last verified: <b>{c.last_verified ? ROLE_LABEL[c.last_verified as Stage].toLowerCase() : "none"}</b>. Problem found at: <b>{ROLE_LABEL[c.stage!].toLowerCase()}</b>.</>}</p>
      <div className="ts-why bad" style={{ marginTop: 0 }}>{c.reason}</div>
      {c.kind === "DISPUTE" && c.triage?.cause && <div className="ts-why" style={{ marginTop: 6 }}><b>What the system found:</b> {c.triage.cause}</div>}
      {c.instruction && <div className="ts-card" style={{ marginTop: 10, background: "var(--ts-mist)" }}><div className="ts-tiny">Reviewer asks</div>{c.instruction}</div>}
      <div className="ts-case-steps">
        {c.steps!.map((s, i) => <span key={s.label} className={i < now ? "done" : i === now ? "now" : ""}><i>{i < now ? "✓" : i + 1}</i>{s.label}</span>)}
      </div>
      <div className="ts-why" style={{ marginTop: 8 }}>
        {c.state === "ON_HOLD" ? (role === c.stage
            ? <>This one is yours to fix. You have <b>{c.attempts_left} {c.attempts_left === 1 ? "try" : "tries"}</b> left before it goes to a reviewer.</>
            : <>Waiting on <b>{c.waiting_on!.name}</b> to fix it. {c.attempts_left} {c.attempts_left === 1 ? "try" : "tries"} left before it goes to a reviewer.</>)
          : <>With reviewer <b>{c.waiting_on!.name}</b>. Nothing is blamed until the evidence has been read.</>}
      </div>
    </motion.div>
  );
}

function Terminal({ snap }: { snap: Snapshot }) {
  const t = ({ RETURNED: ["Returned to the seller", "A reviewer sent this parcel back. The buyer is refunded."],
    RESOLVED_REFUND: ["Refunded", "The complaint was upheld. The buyer's money went back to their wallet."],
    RESOLVED_REPLACEMENT: ["Replacement requested", "The seller has to send the right product. Funds stay held."],
    RESOLVED_REJECTED: ["Complaint closed", "The reviewer found the product was fine. The seller keeps the payment."] } as Record<string, string[]>)[snap.shipment.state];
  if (!t) return null;
  const last = [...snap.events].reverse().find((e) => e.event_type === "REVIEW_DECISION");
  return <div className="ts-card" style={{ marginBottom: 16 }}><span className="ts-chip ok">{t[0]}</span><p style={{ margin: "8px 0 0" }}>{t[1]}</p>
    {last && <div className="ts-why">Reviewer's reason: {last.payload.reason}</div>}</div>;
}

type Found = { sha256: string; url: string; identity: Identity };
type Verdict = NonNullable<Identity["verification"]>;

/* seller registers a product in two steps: show the front (name, look), then scan the barcode (exact identity).
   The barcode must be read by the camera and must belong to the product that was shown: a typed number is never accepted. */
function Empty({ role, run }: { role: Stage; run: Ctx["run"] }) {
  const [cam, setCam] = useState<"" | "front" | "barcode">("");
  const [reading, setReading] = useState<string | null>(null);
  const [found, setFound] = useState<Found | null>(null);
  const [value, setValue] = useState(199);
  const [miss, setMiss] = useState(false);
  const [blocked, setBlocked] = useState<Verdict | null>(null);
  if (role !== "SELLER") return <><h2 className="ts-h2"><Wipe text="Waiting for an order" /></h2><p className="ts-why">The seller has not registered a parcel yet. This screen will come alive the moment they do.</p></>;

  const readFront = (blob: Blob, code?: string) => run(async () => {
    const url = URL.createObjectURL(blob); setReading(url); setFound(null); setMiss(false); setBlocked(null);
    try {
      const f = new FormData(); f.append("file", blob, "product.jpg"); f.append("mode", "product"); if (code) f.append("hint_code", code);
      setFound(await api<Found>("/identify", { form: f }));
    } finally { setReading(null); URL.revokeObjectURL(url); }
  });
  const readBarcode = (blob: Blob, code?: string) => run(async () => {
    if (!found) return;
    setReading(URL.createObjectURL(blob)); setMiss(false); setBlocked(null);
    try {
      const f = new FormData(); f.append("file", blob, "barcode.jpg"); f.append("mode", "barcode"); f.append("front_sha", found.sha256); if (code) f.append("hint_code", code);
      const r = await api<{ sha256: string; barcode: (Identity["barcode"] & { title?: string; by?: string; source?: string }) | null; verdict?: Verdict }>("/identify", { form: f });
      if (!r.barcode) return setMiss(true);
      if (r.verdict?.status === "mismatch") return setBlocked(r.verdict);
      const { title, by, source, ...bc } = r.barcode;
      setFound((cur) => cur && { ...cur, identity: { ...cur.identity, barcode: bc, barcode_sha256: r.sha256, verification: r.verdict,
        ...(title ? { product_name: title, brand: by || cur.identity.brand, name_source: source } : {}) } });
    } finally { setReading(null); }
  });
  const edit = (k: keyof Identity, v: string) => found && setFound({ ...found, identity: { ...found.identity, [k]: v } });
  const register = () => run(async () => {
    if (!found) return;
    const id = found.identity;
    setSnap(await api<Snapshot>("/shipments", { body: {
      order_ref: "ORD-" + Math.floor(1000 + Math.random() * 9000), product_name: id.product_name, category: id.category || "general",
      declared_value: value, expected_colour: id.colours?.[0] ?? "", identity: id, reference_sha256: found.sha256 } }));
  });
  const bc = found?.identity.barcode, ver = found?.identity.verification;

  return (
    <div className="ts-col">
      <div><div className="ts-tiny">{found ? "Step 2 of 2" : "Step 1 of 2"}</div>
        <h2 className="ts-h2">{found ? <><Wipe text="Now scan the" /> <em><Wipe text="barcode" delay={0.3} /></em></> : <><Wipe text="Show us what you're" /> <em><Wipe text="sending" delay={0.3} /></em></>}</h2>
        <p className="ts-why">{found ? "The barcode (or ISBN) is the product's own identity. It has to be on the product you just showed, and the buyer's copy must show the same number." : "Hold up the front of the product: the cover, or the label with the name. We read the name and what it looks like."}</p></div>

      {(reading || found) && (
        <div className="ts-product" style={{ gridTemplateColumns: "96px 1fr" }}>
          <div style={{ position: "relative", overflow: "hidden", width: 96, height: 96 }}>
            <img src={reading ?? found!.url} alt="" style={{ width: 96, height: 96, objectFit: "cover" }} />
            {reading && <motion.div style={{ position: "absolute", left: 0, right: 0, height: 3, background: "var(--ts-ink)", boxShadow: "0 0 12px var(--ts-ink)" }}
              animate={{ top: ["0%", "100%", "0%"] }} transition={{ duration: 1.6, repeat: Infinity, ease: "linear" }} />}
          </div>
          {reading ? <div><div className="ts-tiny">Reading</div><div className="ts-why">{found ? "Reading the barcode and checking it belongs to this product…" : "Reading the name, brand and colours…"}</div></div> : (
            <div className="ts-col" style={{ gap: 8, minWidth: 0 }}>
              <div className="ts-tiny">Recognised{found!.identity.confidence ? ` · ${Math.round(found!.identity.confidence * 100)}%` : ""} · {found!.identity.model}</div>
              <textarea className="ts-input" rows={2} style={{ resize: "none" }} aria-label="Product name" value={found!.identity.product_name} onChange={(e) => edit("product_name", e.target.value)} />
              <input className="ts-input" aria-label="Brand" placeholder="Brand or author" value={found!.identity.brand ?? ""} onChange={(e) => edit("brand", e.target.value)} />
              <input className="ts-input" aria-label="Size" placeholder="Size (optional)" value={found!.identity.net_quantity ?? ""} onChange={(e) => edit("net_quantity", e.target.value)} />
              <div className="ts-tags">{[found!.identity.category, ...(found!.identity.colours ?? [])].filter(Boolean).map((t) => <span key={t} className="ts-tag">{t}</span>)}</div>
            </div>
          )}
        </div>
      )}

      {found && !reading && (bc ? (
        <div className="ts-card" style={{ borderColor: ver?.status === "unverified" ? "var(--ts-ochre)" : "var(--ts-ink)" }}>
          <span className={"ts-chip " + (ver?.status === "match" ? "ok" : "warn")}>{bc.isbn ? "ISBN" : "Barcode"} read{ver?.status === "match" ? " and confirmed" : ", not confirmed"}</span>
          <div style={{ fontWeight: 700, fontSize: 22, letterSpacing: ".06em", margin: "8px 0 2px", wordBreak: "break-all" }}>{bc.code}</div>
          <div className="ts-why" style={{ marginTop: 0 }}>Read from the {bc.how ?? "barcode"}. {ver?.detail}</div>
          <button className="ts-chip" style={{ marginTop: 8 }} onClick={() => setCam("barcode")}>Scan again</button></div>
      ) : (
        <div className="ts-card" style={blocked ? { borderColor: "var(--ts-red)" } : undefined}>
          <span className={"ts-chip " + (blocked ? "bad" : "warn")}>{blocked ? "Wrong barcode" : miss ? "Could not read it" : "Barcode needed"}</span>
          <p className={"ts-why" + (blocked ? " bad" : "")} style={{ marginTop: 6 }}>{blocked ? blocked.detail
            : miss ? "Try again closer: fit the barcode inside the box, 15 to 25 cm away, flat and steady, with no glare. The barcode has to be read by the camera: typing a number is not accepted."
            : "Scan the barcode (or ISBN) on the product you just showed. Fit it inside the box on the camera."}</p>
          <button className="ts-btn block" onClick={() => setCam("barcode")}>{blocked || miss ? "Try again" : "Scan the barcode"}</button>
        </div>
      ))}

      {!found && !reading && <button className="ts-btn block" onClick={() => setCam("front")}>Show the front of the product</button>}
      {found && !reading && (
        <>
          <button className="ts-btn ghost block" onClick={() => { setFound(null); setBlocked(null); setCam("front"); }}>Not right? Show the front again</button>
          <label className="ts-field"><span className="ts-tiny">Price you declare (₹)</span>
            <input className="ts-input" inputMode="numeric" value={value} onChange={(e) => setValue(Number(e.target.value) || 0)} /></label>
          <button className="ts-btn block" disabled={!found.identity.product_name.trim() || !value} onClick={register}>Register product</button>
          {!bc && <p className="ts-why" style={{ textAlign: "center" }}>Without a barcode, the buyer can only be checked by photo, colour and look.</p>}
        </>
      )}
      <AnimatePresence>
        {cam && <Camera key="cam" hint={cam === "barcode" ? "Fit the barcode inside the box. Hold it flat, steady, 15 to 25 cm away." : "Hold up the front of the product"} nonce={nonce()} readBarcode={cam === "barcode"}
          onClose={() => setCam("")} onShot={(b, code) => { const c = cam; setCam(""); c === "barcode" ? readBarcode(b, code) : readFront(b, code); }}
          onDemo={cam === "front" && isRehearsal() ? async () => { setCam(""); readFront(await demoCapture("product_front", "SELLER", "A", "")); } : undefined} />}
      </AnimatePresence>
    </div>
  );
}

/* ---------- one stage ---------- */
function StagePanel({ ctx, role }: { ctx: Ctx; role: Stage }) {
  const { snap, cp } = ctx;
  const idx = STAGES.indexOf(role);
  const prev = idx > 0 ? snap.checkpoints[idx - 1] : null;
  const s = snap.shipment.state;

  if (cp.state === "PENDING") {
    const current = snap.checkpoints.find((c) => c.state === "IN_PROGRESS" || c.state === "FAILED");
    return (
      <div>
        <h2 className="ts-h2"><Wipe text="Not your turn" /> <em><Wipe text="yet" delay={0.2} /></em></h2>
        <p className="ts-why">{current ? `The ${ROLE_LABEL[current.role].toLowerCase()} stage is ${current.state === "FAILED" ? "on hold" : "in progress"}. You will be able to act as soon as ${prev ? ROLE_LABEL[prev.role].toLowerCase() : "the previous stage"} taps Good to Go.` : "Waiting."}</p>
        <Reference snap={snap} />
      </div>
    );
  }
  if (role === "CONSUMER" && ["ACCEPTED", "DISPUTED", "UNDER_REVIEW", "RESOLVED_REFUND", "RESOLVED_REJECTED", "RESOLVED_REPLACEMENT", "RETURNED"].includes(s) && cp.state !== "FAILED") return <Outcome ctx={ctx} />;
  if (cp.state === "LOCKED") return <Locked ctx={ctx} />;
  if (["RETURNED", "RESOLVED_REFUND", "RESOLVED_REPLACEMENT", "RESOLVED_REJECTED"].includes(s)) return null;
  if (role === "CONSUMER") return <Consumer ctx={ctx} />;
  return <Checklist ctx={ctx} role={role} />;
}

function Reference({ snap }: { snap: Snapshot }) {
  const ref = snap.checkpoints[0].evidence.find((e) => e.purpose === "product_front");
  if (!ref) return null;
  return <div style={{ marginTop: 14 }}><div className="ts-tiny" style={{ marginBottom: 6 }}>Seller's reference photo</div><img className="ts-thumb" style={{ width: 120, height: 90 }} src={ref.url} alt="" /></div>;
}

function Locked({ ctx }: { ctx: Ctx }) {
  const { snap, cp, run } = ctx;
  if (cp.role === "DELIVERY") return <DoorPanel ctx={ctx} />;
  return (
    <div>
      <motion.div className="ts-stamp" initial={{ scale: 3, opacity: 0, rotate: 12 }} animate={{ scale: 1, opacity: 1, rotate: -8 }}
                  transition={{ type: "spring", stiffness: 300, damping: 18 }} style={{ marginBottom: 14 }}>STAGE<br />LOCKED</motion.div>
      <h2 className="ts-h2"><Wipe text="Your stage is" /> <em><Wipe text="locked" delay={0.25} /></em></h2>
      <p className="ts-why">Your evidence is part of the shared record. It cannot be replaced or removed.</p>
      <div className="ts-row" style={{ flexWrap: "wrap", margin: "10px 0" }}>{cp.evidence.map((e) => <Thumb key={e.id} e={e} />)}</div>
      {snap.seal.registered && <p className="ts-why">Seal {snap.seal.hint} is registered to this parcel.</p>}
      {cp.role === "SELLER" && snap.settings.rehearsal && (
        <button className="ts-btn ghost" onClick={() => run(async () => {
          const blob = await demoCapture("product_front", "SELLER", snap.settings.scenario, "");
          const f = new FormData(); f.append("purpose", "product_front"); f.append("client_sha256", await sha256(blob)); f.append("file", blob, "x.jpg");
          await api(`/checkpoints/${cp.id}/evidence`, { form: f });
        })}>Try to replace a photo</button>
      )}
    </div>
  );
}

/* delivery person at the door: the handshake that stops "paid, but nobody took the parcel" */
const CANCEL_REASONS: [string, string][] = [["nobody_home", "Nobody was home"], ["unreachable", "Buyer not answering"], ["wrong_person", "Person at the door is not the buyer"], ["refused", "Buyer refused"]];

function DoorPanel({ ctx }: { ctx: Ctx }) {
  const { snap, cp, run } = ctx;
  const h = snap.handover, st = snap.shipment.state;
  const [code, setCode] = useState("");
  const [err, setErr] = useState("");
  const [cancelling, setCancelling] = useState(false);
  const door = useCountdown(h?.expires_at, snap.now), pay = useCountdown(h?.reserve_expires_at, snap.now);
  const id = snap.shipment.id;
  const go = (path: string, body?: unknown) => run(async () => setSnap(await api<Snapshot>(`/shipments/${id}/${path}`, { body: body ?? {} })));
  const confirm = async () => { setErr(""); try { setSnap(await api<Snapshot>(`/shipments/${id}/handover/confirm`, { body: { code } })); } catch (e: any) { setErr(e.message); setCode(""); } };
  return (
    <div>
      <h2 className="ts-h2"><Wipe text="At the" /> <em><Wipe text="door" delay={0.25} /></em></h2>
      <div className="ts-row" style={{ flexWrap: "wrap", margin: "8px 0" }}>{cp.evidence.map((e) => <Thumb key={e.id} e={e} />)}</div>
      {st === "ACCEPTED" ? <p className="ts-why">Handover confirmed. {rs(snap.payment.amount)} went to the seller. Thank you.</p> :
        st !== "AWAITING_CONSUMER" ? <p className="ts-why">This delivery is {st.toLowerCase().replace("_", " ")}. Nothing to do at the door right now.</p> : (
        <>
          {(!h || ["CANCELLED", "EXPIRED", "FAILED", "CONFIRMED"].includes(h.state)) && (
            <div className="ts-col">
              {h && <div className="ts-card" style={{ borderColor: "var(--ts-ochre)" }}><span className="ts-chip warn">Last visit not completed</span><div className="ts-why">{h.state === "EXPIRED" ? "The time ran out before the buyer paid." : "The visit was cancelled."} The buyer was not charged. Attempt {h.failed_sessions} of {h.max_failed}.</div></div>}
              <p className="ts-why">Press this only when you are physically at the buyer's door. Until you do, the buyer cannot pay, and scanning the seal on the box does nothing.</p>
              <button className="ts-btn block" onClick={() => go("door")}>I'm at the door</button>
            </div>
          )}
          {h?.state === "OPEN" && (
            <div className="ts-col">
              {h.short_by > 0 && <ShortAlarm short={h.short_by} h={h} role="DELIVERY" />}
              <div className="ts-card"><span className="ts-chip ok">Waiting for the buyer</span>
                <p style={{ margin: "8px 0 2px" }}>The buyer is checking the parcel and will pay from their wallet. When they have paid you will be asked to enter their code.</p>
                <div className="ts-why" style={{ marginTop: 0 }}>This visit stays open for {mmss(door)}.</div></div>
              {!h.short_by && <Call phone={h.buyer_phone} label="Call the buyer" />}
            </div>
          )}
          {h?.state === "PAID" && (
            <div className="ts-col">
              <div className="ts-card" style={{ borderColor: "var(--ts-ink)" }}><span className="ts-chip ok">Buyer paid {rs(snap.payment.amount)}</span>
                <p style={{ margin: "8px 0 2px" }}><b>Hand over the parcel, then ask the buyer for the code on their phone.</b> Check it is the buyer. Type the code here. The seller is paid only when it matches.</p>
                <div className="ts-why" style={{ marginTop: 0 }}>The payment is reserved for {mmss(pay)}. If time runs out the buyer's money goes back to their wallet and the parcel stays with you.</div></div>
              <input className="ts-input ts-codein" inputMode="numeric" maxLength={6} placeholder="······" value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))} />
              {err && <div className="ts-err" role="alert">{err}</div>}
              <button className="ts-btn block" disabled={code.length < 6} onClick={confirm}>Confirm handover</button>
            </div>
          )}
          {(h?.state === "OPEN" || h?.state === "PAID") && (
            <div className="ts-col" style={{ marginTop: 14 }}>
              {!cancelling ? <button className="ts-btn ghost block" onClick={() => setCancelling(true)}>Can't hand it over</button> : (
                <>
                  <div className="ts-tiny">Why? The parcel stays with you{h.state === "PAID" ? " and the buyer's money goes back to their wallet" : ""}.</div>
                  {CANCEL_REASONS.map(([k, l]) => <button key={k} className="ts-option" onClick={() => { setCancelling(false); go("handover/cancel", { reason: k }); }}>{l}</button>)}
                </>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}

/* seller / warehouse / delivery */
function Checklist({ ctx, role }: { ctx: Ctx; role: Stage }) {
  const { snap, cp, run } = ctx;
  const [cam, setCam] = useState<string | null>(null);
  const [rec, setRec] = useState(false);
  const [scan, setScan] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [report, setReport] = useState(false);
  const failed = cp.state === "FAILED";
  const onHold = snap.shipment.state === "ON_HOLD";
  const mine = failed && onHold;                       // I am the stage owner and I still have tries
  const canWork = !failed || (onHold && (snap.case.attempts_left ?? 0) > 0);
  const demo = snap.settings.rehearsal;
  const serial = () => demoSerial(snap.shipment.id);

  const upload = (purpose: string, blob: Blob) => run(async () => {
    setBusy(purpose);
    try {
      const f = new FormData(); f.append("purpose", purpose); f.append("client_sha256", await sha256(blob)); f.append("file", blob, purpose === "packing_video" ? "pack.webm" : "shot.jpg");
      f.append("kind", purpose === "packing_video" ? "video" : "photo");
      setSnap(await api<Snapshot>(`/checkpoints/${cp.id}/evidence`, { form: f }));
      await new Promise((r) => setTimeout(r, 700));
    } finally { setBusy(null); }
  });
  const sendSeal = (code: string, how: "QR" | "typed") => run(async () => setSnap(await api<Snapshot>(`/checkpoints/${cp.id}/scan`, { body: { seal_code: code, how } })));
  const sealDone = role === "SELLER" ? snap.seal.registered : !!cp.seal_scanned;
  const ready = cp.required.every((p) => ["pass", "review"].includes(verdict(cp.latest_checks[p]))) && sealDone;
  const lastScan = [...snap.events].reverse().find((e) => e.event_type === "SEAL_SCANNED" && e.actor_role === role);
  const scanBad = !!lastScan && !lastScan.payload.match && !sealDone;
  const sealCheck = cp.latest_checks["seal_side"]?.find((c) => c.check_type === "seal_serial");

  return (
    <div>
      <h2 className="ts-h2"><Wipe text={role === "SELLER" ? "Pack and" : role === "WAREHOUSE" ? "Receive and" : "Collect and"} /> <em><Wipe text="verify" delay={0.3} /></em></h2>
      {role !== "SELLER" && <Reference snap={snap} />}
      {mine && (
        <div className="ts-card" style={{ borderColor: "var(--ts-ink)", marginTop: 14 }}>
          <span className="ts-chip ok">Fix it</span>
          <p style={{ margin: "8px 0 0" }}>{snap.case.attempts_left} {snap.case.attempts_left === 1 ? "try" : "tries"} left. Scan the right seal again or retake the failed photo below. If it checks out, the hold lifts and the parcel moves on. A wrong try is recorded, not hidden.</p>
        </div>
      )}
      <ul className="ts-list">
        <li className="ts-item">
          <span className={"ts-dot " + (sealDone ? "pass" : scanBad ? "fail" : "")}>{sealDone ? "✓" : scanBad ? "✕" : "1"}</span>
          <div><div>{role === "SELLER" ? "Put the seal on the parcel and scan it" : "Scan the parcel seal"}</div>
            {sealDone ? <div className="ts-why">{role === "SELLER" ? `Seal ${snap.seal.hint} was read from the parcel and registered to this order.` : `Seal ${snap.seal.hint} is the one registered at packing.`}</div>
              : role === "SELLER" ? <div className="ts-why">Stick a tamper-evident seal over the closed box, then scan it. The app reads the serial printed on the sticker and registers it. It is never given to you by the system.</div> : null}
            {scanBad && <div className="ts-why bad">Seal {lastScan!.payload.hint} is not this parcel's seal.</div>}
            {!sealDone && sealCheck && sealCheck.status !== "PASS" && !scanBad && <div className="ts-why bad">{sealCheck.explanation}</div>}</div>
          {canWork && !sealDone && <button className="ts-btn" onClick={() => setScan(true)}>{scanBad ? "Scan again" : "Scan"}</button>}
        </li>
        {cp.required.filter((p) => !(role === "SELLER" && p === "product_front")).map((p, i) => {
          const cs = cp.latest_checks[p], v = verdict(cs), shot = cp.evidence.filter((e) => e.purpose === p).at(-1);
          const running = busy === p;
          const locked = role === "SELLER" && p === "packing_video" && !snap.seal.registered;
          return (
            <li key={p} className="ts-item" style={locked ? { opacity: .45 } : undefined}>
              <span className={"ts-dot " + (running ? "run" : v === "pass" ? "pass" : v === "fail" ? "fail" : "")}>{running ? "" : v === "pass" ? "✓" : v === "fail" ? "✕" : i + 2}</span>
              <div>
                <div>{PURPOSE_LABEL[p]}</div>
                {p === "packing_video" && !shot && <div className="ts-why">{locked ? "Scan the seal first, so the video can show the seal that is on the box." : "Film the product going into the box and the seal on the closed box. Show the seal's QR and the product's barcode to the camera."}</div>}
                {p === "seal_side" && !shot && <div className="ts-why">Photograph the closed box with the seal in view.</div>}
                {running ? <div className="ts-why">{p === "packing_video" ? "Analysing the video…" : "Running checks…"}</div> : cs?.filter((c) => c.check_type !== "image_quality" || c.status !== "PASS").map((c) => (
                  <div key={c.id} className={"ts-why" + (c.status === "FAIL" && !c.overridden ? " bad" : "")}>{c.status === "REVIEW" ? "Check: " : ""}{c.explanation}{c.overridden ? " (cleared by a reviewer)" : ""}</div>))}
              </div>
              <div className="ts-row">
                {shot && <Thumb e={shot} />}
                {canWork && !locked && <button className="ts-btn ghost" onClick={() => (p === "packing_video" ? setRec(true) : setCam(p))}>{shot ? "Retake" : p === "packing_video" ? "Record" : "Capture"}</button>}
              </div>
            </li>
          );
        })}
      </ul>
      {failed ? (
        <div className="ts-col">
          {onHold ? (
            <>
              {!report ? <button className="ts-btn ghost block" onClick={() => setReport(true)}>I can't fix this. Report to the reviewer</button> : (
                <>
                  <textarea className="ts-input" rows={3} placeholder="What did you find? (box opened, tape torn, label missing…)" value={note} onChange={(e) => setNote(e.target.value)} />
                  <button className="ts-btn red block" disabled={!note.trim()} onClick={() => run(async () => setSnap(await api<Snapshot>(`/checkpoints/${cp.id}/exception`, { body: { note } })))}>Send to the reviewer</button>
                </>
              )}
            </>
          ) : <p className="ts-why">A reviewer has this case. You will get your next step here as soon as they decide.</p>}
        </div>
      ) : (
        <>
          <Slide label="Slide for Good to Go" disabled={!ready}
                 onConfirm={() => { run(async () => setSnap(await api<Snapshot>(`/checkpoints/${cp.id}/lock`, { method: "POST" }))); }} />
          <p className="ts-why" style={{ textAlign: "center" }}>{ready ? "After this, your evidence is locked and cannot be changed." : "Complete every step to unlock."}</p>
        </>
      )}
      <AnimatePresence>
        {cam && <Camera key="cam" hint={cam === "seal_side" ? "Photograph the closed box with the seal in view" : PURPOSE_LABEL[cam]} nonce={nonce()}
          onClose={() => setCam(null)}
          onShot={(b) => { const p = cam; setCam(null); upload(p, b); }}
          onDemo={demo ? async () => { const p = cam; setCam(null); upload(p, await demoCapture(p, role, snap.settings.scenario, serial())); } : undefined} />}
        {rec && <VideoRecorder key="rec" hint="Film the product going into the box, then the sealed box. Show the seal's QR and the barcode to the camera." onClose={() => setRec(false)}
          onDone={(b) => { setRec(false); upload("packing_video", b); }}
          onDemo={demo ? async () => { setRec(false); upload("packing_video", await demoVideo(serial())); } : undefined} />}
        {scan && <Scanner key="scan" onClose={() => setScan(false)}
          onSeal={(code, how) => { setScan(false); sendSeal(code, how); }}
          onPhoto={(b) => { setScan(false); upload("seal_side", b); }}
          onDemo={demo ? () => { setScan(false); sendSeal(serial(), "QR"); } : undefined} />}
      </AnimatePresence>
    </div>
  );
}

const DISPUTE_REASONS: [string, string][] = [["wrong_item", "Wrong item"], ["wrong_colour", "Wrong colour"], ["damaged", "Damaged"], ["seal_broken", "Seal looked broken"], ["other", "Something else"]];

/* the pay button is only live when the delivery person is at the door. Scanning the seal alone never pays. */
function PayAction({ snap, wallet, openWallet, onPay, disabled }: { snap: Snapshot; wallet: WalletData | null; openWallet: () => void; onPay: () => void; disabled?: boolean }) {
  const h = snap.handover, p = snap.payment;
  if (p.state === "RESERVED") return <p className="ts-why">You have paid. Show your handover code (at the top) to {h?.agent_name} once you have the parcel in your hands.</p>;
  if (p.state !== "PENDING") return null;
  if (h?.state !== "OPEN") return (
    <div className="ts-card" style={{ borderColor: "var(--ts-ochre)" }}><span className="ts-chip warn">Waiting for the delivery person</span>
      <div className="ts-why">You can pay only when the delivery person is at your door and has opened the handover. Scanning the seal on the box never pays, so nobody else can pay for you or take your parcel.</div></div>
  );
  if (wallet && wallet.balance < p.amount) return <ShortAlarm short={p.amount - wallet.balance} h={h} role="CONSUMER" onAdd={openWallet} />;
  return <Slide label={`Slide to pay ${money(p.amount)} from wallet`} disabled={disabled} onConfirm={onPay} />;
}

/* at the door: scan the seal to know this is the parcel that was packed for you, then pay. The product itself is checked at home. */
function Consumer({ ctx }: { ctx: Ctx }) {
  const { snap, cp, run, wallet, openWallet } = ctx;
  const [pinFor, setPinFor] = useState<null | { override: boolean }>(null);
  const [scan, setScan] = useState(false);
  const [busy, setBusy] = useState(false);
  const [mode, setMode] = useState<"" | "dispute" | "override">("");
  const [why, setWhy] = useState("");
  const [cat, setCat] = useState("other");
  const v = verdict(cp.latest_checks["seal_side"]);
  const flagged = cp.latest_checks["seal_side"]?.filter((c) => c.check_type !== "image_quality" && c.status !== "PASS") ?? [];
  const lastScan = [...snap.events].reverse().find((e) => e.event_type === "CONSUMER_SCANNED");
  const scanBad = !!lastScan && !lastScan.payload.match && !cp.seal_scanned;
  const onHold = cp.state === "FAILED" && snap.shipment.state === "ON_HOLD";
  const demo = snap.settings.rehearsal;

  const sendSeal = (code: string, how: "QR" | "typed") => run(async () => setSnap(await api<Snapshot>(`/checkpoints/${cp.id}/scan`, { body: { seal_code: code, how } })));
  const photo = (blob: Blob) => run(async () => {
    setBusy(true);
    try {
      const f = new FormData(); f.append("purpose", "seal_side"); f.append("client_sha256", await sha256(blob)); f.append("file", blob, "seal.jpg");
      setSnap(await api<Snapshot>(`/checkpoints/${cp.id}/evidence`, { form: f })); await new Promise((r) => setTimeout(r, 600));
    } finally { setBusy(false); }
  });

  return (
    <div>
      <h2 className="ts-h2"><Wipe text="Is this parcel" /> <em><Wipe text="yours?" delay={0.3} /></em></h2>
      <p className="ts-why">Scan the seal on the box. That proves it is the parcel that was packed for you and that nobody opened it on the way. You open it and check the product itself after the delivery person has gone.</p>
      <ul className="ts-list">
        <li className="ts-item">
          <span className={"ts-dot " + (busy ? "run" : cp.seal_scanned ? "pass" : scanBad ? "fail" : "")}>{busy ? "" : cp.seal_scanned ? "✓" : scanBad ? "✕" : "1"}</span>
          <div>Scan the parcel seal
            {cp.seal_scanned ? <div className="ts-why">This parcel belongs to order {snap.shipment.order_ref}. Seal {snap.seal.hint} matches, and the chain is verified.</div> : null}
            {scanBad && <div className="ts-why bad">This parcel is NOT registered to you ({lastScan!.payload.hint}).</div>}</div>
          {!cp.seal_scanned && (!onHold || (snap.case.attempts_left ?? 0) > 0) && <button className="ts-btn" onClick={() => setScan(true)}>{scanBad ? "Scan again" : "Scan"}</button>}
        </li>
      </ul>

      {cp.seal_scanned && (
        <motion.div initial={{ opacity: 0, y: 14 }} animate={{ opacity: 1, y: 0 }} className="ts-col">
          {flagged.map((c) => (
            <div key={c.id} className="ts-card" style={c.status === "FAIL" ? { borderColor: "var(--ts-red)" } : { borderColor: "var(--ts-ochre)" }}>
              <span className={"ts-chip " + (c.status === "REVIEW" ? "warn" : "bad")}>{c.check_type.replace("_", " ")} · {c.status === "REVIEW" ? "check it yourself" : c.status.toLowerCase()}</span>
              <div className={"ts-why" + (c.status === "FAIL" ? " bad" : "")} style={{ marginTop: 6 }}>{c.explanation}</div>
            </div>
          ))}
          {v !== "fail" ? (
            <>
              <PayAction snap={snap} wallet={wallet} openWallet={openWallet} onPay={() => setPinFor({ override: false })} />
              {snap.payment.state === "PENDING" && <button className="ts-btn ghost block" onClick={() => setMode(mode === "dispute" ? "" : "dispute")}>Something is wrong</button>}
              <div className="ts-card" style={{ background: "var(--ts-mist)" }}><div className="ts-tiny">After the delivery</div>
                <div className="ts-why" style={{ marginTop: 4 }}>Open the box at home and scan the product's barcode. If it is not what you ordered, a complaint is filed for you automatically and you are refunded.</div></div>
            </>
          ) : (
            <>
              <p className="ts-why">The seal check flagged this parcel. You decide: report it, or accept it if you have looked at the box yourself and it is fine.</p>
              <div className="ts-row"><button className="ts-btn red" style={{ flex: 1 }} onClick={() => setMode("dispute")}>Raise a dispute</button>
                <button className="ts-btn ghost" style={{ flex: 1 }} onClick={() => setMode("override")}>Accept anyway</button></div>
            </>
          )}
          {mode === "dispute" && (
            <div className="ts-col">
              <div className="ts-tags">{DISPUTE_REASONS.map(([k, l]) => <button key={k} className={"ts-btn " + (cat === k ? "" : "ghost")} style={{ padding: "8px 12px" }} onClick={() => setCat(k)}>{l}</button>)}</div>
              <textarea className="ts-input" rows={2} placeholder="Tell us what is wrong" value={why} onChange={(e) => setWhy(e.target.value)} />
              <Slide tone="red" label="Slide to open dispute" disabled={!why.trim()} onConfirm={() => { run(async () => setSnap(await api<Snapshot>(`/shipments/${snap.shipment.id}/dispute`, { body: { note: why, category: cat } }))); }} />
            </div>
          )}
          {mode === "override" && (
            <div className="ts-col">
              <textarea className="ts-input" rows={2} placeholder="Why is it fine? (recorded with your decision)" value={why} onChange={(e) => setWhy(e.target.value)} />
              <PayAction snap={snap} wallet={wallet} openWallet={openWallet} disabled={!why.trim()} onPay={() => setPinFor({ override: true })} />
            </div>
          )}
        </motion.div>
      )}
      <AnimatePresence>
        {pinFor && <PinSheet key="pin" amount={snap.payment.amount} balance={wallet?.balance ?? 0} onClose={() => setPinFor(null)}
          onSubmit={async (pin) => { setSnap(await api<Snapshot>(`/shipments/${snap.shipment.id}/accept`, { body: { pin, override: pinFor.override, note: pinFor.override ? why : "" } })); }} />}
        {scan && <Scanner key="scan" onClose={() => setScan(false)}
          onSeal={(code, how) => { setScan(false); sendSeal(code, how); }}
          onPhoto={(b) => { setScan(false); photo(b); }}
          onDemo={demo ? () => { setScan(false); sendSeal(demoSerial(snap.shipment.id), "QR"); } : undefined} />}
      </AnimatePresence>
    </div>
  );
}

/* the complaint, as the buyer, the seller and support all see it: what was filed, what the system knew, and what was decided */
export function ComplaintCard({ snap }: { snap: Snapshot }) {
  const c = snap.complaint;
  if (!c) return null;
  const mark = (s: string) => (s === "PASS" ? "✓" : s === "FAIL" ? "✕" : "?");
  const refunded = snap.payment.state === "REFUNDED";
  return (
    <div className="ts-card" style={{ marginBottom: 16, borderColor: c.status === "OPEN" ? "var(--ts-red)" : "var(--ts-ink-deep)" }}>
      <div className="ts-between"><span className={"ts-chip " + (c.status === "OPEN" ? "bad" : "ok")}>Complaint {c.ticket} · {c.status === "OPEN" ? "open" : "closed"}</span>
        <span className="ts-tiny">{c.auto ? "filed automatically" : "reported by the buyer"}</span></div>
      <p style={{ margin: "10px 0 2px", fontWeight: 600 }}>{c.reason}</p>
      <div className="ts-why" style={{ marginTop: 0 }}>{c.source ? `Source: ${c.source}. ` : ""}{c.paid ? "The seller had already been paid." : ""}</div>
      {c.triage && (
        <>
          <ul className="ts-list" style={{ margin: "10px 0 6px" }}>
            {c.triage.facts.map((f) => (
              <li key={f.label} className="ts-item" style={{ gridTemplateColumns: "26px 1fr", padding: "9px 0" }}>
                <span className={"ts-dot " + (f.status === "PASS" ? "pass" : f.status === "FAIL" ? "fail" : "")} style={{ width: 22, height: 22, fontSize: 11 }}>{mark(f.status)}</span>
                <div><div className="ts-tiny">{f.label}</div><div className="ts-why" style={{ marginTop: 1 }}>{f.text}</div></div></li>))}
          </ul>
          <div className="ts-why"><b>What this means:</b> {c.triage.cause}</div>
        </>
      )}
      {c.decision ? (
        <div className="ts-card" style={{ marginTop: 10, background: "var(--ts-mist)" }}>
          <div className="ts-tiny">{c.decision.auto ? "Decided automatically" : "Decided by support"} · {c.decision.decision}</div>
          <div style={{ marginTop: 4 }}>{c.decision.reason}</div>
          {refunded && <div style={{ marginTop: 6, fontWeight: 700 }}>{money(snap.payment.amount)} was returned to the buyer's wallet.</div>}
        </div>
      ) : <div className="ts-why" style={{ marginTop: 10 }}>{c.status === "OPEN" ? "A person in support is reading the evidence now. Nobody is blamed until they have." : ""}</div>}
    </div>
  );
}

/* at home: the buyer opens the parcel and scans the product. A match closes the order; a difference files the complaint for them. */
function Unbox({ ctx }: { ctx: Ctx }) {
  const { snap, run } = ctx;
  const id = snap.shipment.id, bc = snap.shipment.identity.barcode;
  const last = [...snap.events].reverse().find((e) => e.event_type === "UNBOX_VERIFIED" || e.event_type === "UNBOX_UNSURE");
  const [cam, setCam] = useState<"" | "barcode" | "front">("");
  const [barcode, setBarcode] = useState<{ blob: Blob; code?: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [report, setReport] = useState(false);
  const [why, setWhy] = useState("");
  const [cat, setCat] = useState("wrong_item");
  const mock = snap.settings.ai_mode === "mock";
  const submit = (front: Blob, b: typeof barcode) => run(async () => {
    setBusy(true);
    try {
      const f = new FormData(); f.append("front", front, "product.jpg");
      if (b) { f.append("barcode", b.blob, "barcode.jpg"); if (b.code) f.append("hint_code", b.code); }
      setSnap(await api<Snapshot>(`/shipments/${id}/unbox`, { form: f }));
    } finally { setBusy(false); setBarcode(null); }
  });
  const shot = (blob: Blob, code?: string) => { const c = cam; setCam(""); if (c === "barcode") { setBarcode({ blob, code }); setCam("front"); } else submit(blob, barcode); };
  const demoBlob = (p: string) => demoCapture(p, "CONSUMER", snap.settings.scenario, "");

  if (last?.event_type === "UNBOX_VERIFIED") return (
    <div className="ts-card" style={{ borderColor: "var(--ts-ink)" }}><span className="ts-chip ok">Product verified</span>
      <p style={{ margin: "8px 0 0" }}>{last.payload.how === "scan" ? (bc ? "The barcode you scanned matches the product the seller registered." : "The product matches what the seller registered.") : "You confirmed the product is the right one."} The order is complete.</p></div>
  );
  return (
    <div className="ts-col">
      <div className="ts-card">
        <div className="ts-tiny">Step 2 · at home</div>
        <div style={{ fontWeight: 700, margin: "4px 0" }}>Open the parcel and check the product</div>
        <div className="ts-why" style={{ marginTop: 0 }}>{bc ? `Take the product out and scan its barcode. It must match ${snap.shipment.product_name}. If it does not, we file the complaint for you and refund you.` : "Take the product out and photograph it. We compare it with what the seller registered."}</div>
      </div>
      {last?.event_type === "UNBOX_UNSURE" && !busy && (
        <div className="ts-card" style={{ borderColor: "var(--ts-ochre)" }}><span className="ts-chip warn">We could not be sure</span>
          <p style={{ margin: "8px 0" }}>You are holding it. Is this the product you ordered?</p>
          <div className="ts-row"><button className="ts-btn" style={{ flex: 1 }} onClick={() => run(async () => setSnap(await api<Snapshot>(`/shipments/${id}/unbox/confirm`, { body: {} })))}>Yes, it is right</button>
            <button className="ts-btn red" style={{ flex: 1 }} onClick={() => setReport(true)}>No, report it</button></div></div>
      )}
      <ul className="ts-list" style={{ margin: 0 }}>
        {bc && <li className="ts-item"><span className={"ts-dot " + (barcode ? "pass" : "")}>{barcode ? "✓" : "1"}</span><div>Scan the product's barcode{barcode?.code ? <div className="ts-why">Read {barcode.code}</div> : null}</div>
          <button className="ts-btn" onClick={() => setCam("barcode")}>{barcode ? "Again" : "Scan"}</button></li>}
        <li className="ts-item" style={{ opacity: bc && !barcode ? .45 : 1 }}><span className={"ts-dot " + (busy ? "run" : "")}>{busy ? "" : bc ? "2" : "1"}</span><div>Photograph the product{busy ? <div className="ts-why">Comparing it with the seller's product…</div> : null}</div>
          <button className="ts-btn" disabled={busy || (!!bc && !barcode)} onClick={() => setCam("front")}>Capture</button></li>
      </ul>
      {!report ? <button className="ts-btn ghost block" onClick={() => setReport(true)}>Something else is wrong with it</button> : (
        <div className="ts-col">
          <div className="ts-tags">{DISPUTE_REASONS.map(([k, l]) => <button key={k} className={"ts-btn " + (cat === k ? "" : "ghost")} style={{ padding: "8px 12px" }} onClick={() => setCat(k)}>{l}</button>)}</div>
          <textarea className="ts-input" rows={2} placeholder="Tell us what is wrong" value={why} onChange={(e) => setWhy(e.target.value)} />
          <Slide tone="red" label="Slide to file a complaint" disabled={!why.trim()} onConfirm={() => { run(async () => setSnap(await api<Snapshot>(`/shipments/${id}/dispute`, { body: { note: why, category: cat } }))); }} />
        </div>
      )}
      <AnimatePresence>
        {cam && <Camera key="cam" hint={cam === "barcode" ? "Fit the product's barcode inside the box. Hold it flat, steady, 15 to 25 cm away." : "Photograph the product you took out of the box"} nonce={nonce()} readBarcode={cam === "barcode"}
          onClose={() => setCam("")} onShot={shot}
          onDemo={mock || (cam === "front" && snap.settings.rehearsal) ? async () => { const c = cam; setCam(""); const img = await demoBlob(c === "barcode" ? "unbox_barcode" : "unbox_front"); if (c === "barcode") { setBarcode({ blob: img }); setCam("front"); } else submit(img, barcode); } : undefined} />}
      </AnimatePresence>
    </div>
  );
}

function Outcome({ ctx }: { ctx: Ctx }) {
  const { snap } = ctx;
  const s = snap.shipment.state;
  const accepted = s === "ACCEPTED";
  const ev = [...snap.events].reverse().find((e) => e.event_type === "ACCEPTED");
  const stamp = ({ ACCEPTED: ["DELIVERED", "& PAID"], DISPUTED: ["COMPLAINT", "OPEN"], UNDER_REVIEW: ["IN", "REVIEW"], RESOLVED_REFUND: [snap.payment.state === "REFUNDED" ? "REFUNDED" : "NOT", snap.payment.state === "REFUNDED" ? "IN FULL" : "CHARGED"],
    RESOLVED_REPLACEMENT: ["REPLACE", "MENT"], RESOLVED_REJECTED: ["COMPLAINT", "CLOSED"], RETURNED: ["RETURNED", "TO SELLER"] } as Record<string, string[]>)[s] ?? ["", ""];
  const head = accepted ? "Delivered and paid" : s === "RESOLVED_REFUND" ? (snap.payment.state === "REFUNDED" ? "You were refunded" : "You were not charged") : s === "RETURNED" ? "You were not charged" : s === "RESOLVED_REJECTED" ? "Complaint closed" : s === "RESOLVED_REPLACEMENT" ? "A replacement is coming" : "We are looking into it";
  return (
    <div>
      <motion.div className={"ts-stamp" + (accepted ? "" : " red")} initial={{ scale: 3, opacity: 0 }} animate={{ scale: 1, opacity: 1, rotate: -8 }}
                  transition={{ type: "spring", stiffness: 280, damping: 16 }} style={{ marginBottom: 16 }}>{stamp[0]}<br />{stamp[1]}</motion.div>
      <h2 className="ts-h2"><Wipe text={head} /></h2>
      {accepted && <p className="ts-why">{`${money(snap.payment.amount)} was paid from your wallet and the seller was paid when ${snap.handover?.agent_name ?? "the delivery person"} confirmed the handover.${ev?.payload.override ? " You accepted it after a flagged check, and that is recorded." : ""}`}</p>}
      <div style={{ marginTop: 14 }}>
        {accepted ? <Unbox ctx={ctx} /> : <ComplaintCard snap={snap} />}
      </div>
      <p className="ts-hash" style={{ marginTop: 14 }}>Demo wallet only. A real wallet needs a licensed payment partner.</p>
    </div>
  );
}
