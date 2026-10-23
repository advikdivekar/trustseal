import { AnimatePresence, motion } from "motion/react";
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import { useLive } from "../lib/store";
import type { Handover, Snapshot, WalletTxn } from "../lib/types";

export const rs = (n: number) => (n < 0 ? "-" : "") + "₹" + Math.abs(n).toLocaleString("en-IN", { maximumFractionDigits: 0 });
export type WalletData = { balance: number; txns: WalletTxn[]; linked: string[] };
const KIND: Record<string, string> = { TOPUP: "Money added", RESERVE: "Reserved for delivery", REVERSAL: "Returned to your wallet", PAYOUT: "Payout received", DUE: "Charged after review", REFUND: "Refunded to you", CLAWBACK: "Refunded to the buyer" };

/* the wallet balance, refreshed whenever anything in the shipment changes */
export function useWallet(enabled = true) {
  const { pulse } = useLive();
  const [w, setW] = useState<WalletData | null>(null);
  const load = useCallback(() => { if (enabled) api<WalletData>("/wallet").then(setW).catch(() => {}); }, [enabled]);
  useEffect(() => { load(); }, [load, pulse]);
  return { wallet: w, reload: load };
}

/* seconds left until an ISO time, using the server's clock so phones with a wrong clock still agree */
export function useCountdown(target: string | undefined, serverNow: string | undefined) {
  const off = useRef(0);
  useEffect(() => { if (serverNow) off.current = Date.parse(serverNow) - Date.now(); }, [serverNow]);
  const calc = () => (target ? Math.max(0, Math.round((Date.parse(target) - (Date.now() + off.current)) / 1000)) : 0);
  const [s, setS] = useState(calc);
  useEffect(() => { setS(calc()); const id = setInterval(() => setS(calc()), 500); return () => clearInterval(id); }, [target, serverNow]); // eslint-disable-line react-hooks/exhaustive-deps
  return s;
}
export const mmss = (s: number) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;

export const Call = ({ phone, label }: { phone: string; label: string }) => (
  <a className="ts-btn ghost block" style={{ textAlign: "center", textDecoration: "none", display: "block" }} href={`tel:${phone.replace(/\s/g, "")}`}>{label} · {phone}</a>
);

export function WalletChip({ onOpen, balance }: { onOpen: () => void; balance?: number }) {
  return <button className="ts-chip" onClick={onOpen} aria-label="Open wallet">{balance === undefined ? "Wallet" : rs(balance)}</button>;
}

export function WalletSheet({ role, wallet, reload, onClose }: { role: string; wallet: WalletData | null; reload: () => void; onClose: () => void }) {
  const buyer = role === "CONSUMER";
  const [amount, setAmount] = useState(1000);
  const [source, setSource] = useState("");
  const [pin, setPin] = useState("");
  const [err, setErr] = useState("");
  const [ok, setOk] = useState("");
  useEffect(() => { if (wallet && !source) setSource(wallet.linked[0]); }, [wallet, source]);
  const add = async () => {
    setErr(""); setOk("");
    try { const r = await api<{ balance: number; reference: string }>("/wallet/topup", { body: { amount, source, pin } }); setOk(`${rs(amount)} added. Balance ${rs(r.balance)}. Ref ${r.reference}`); setPin(""); reload(); }
    catch (e: any) { setErr(e.message); }
  };
  return (
    <motion.div className="ts-sheet" style={{ zIndex: 35 }} initial="h" animate="s" exit="h">
      <motion.div className="back" variants={{ h: { opacity: 0 }, s: { opacity: 1 } }} onClick={onClose} />
      <motion.div className="panel" variants={{ h: { y: "100%" }, s: { y: 0 } }} transition={{ type: "spring", damping: 32, stiffness: 300 }}>
        <div className="ts-between"><span className="ts-tiny">TrustSeal wallet</span><button className="ts-chip" onClick={onClose}>Close</button></div>
        <motion.div key={wallet?.balance} initial={{ scale: 0.96, opacity: 0.4 }} animate={{ scale: 1, opacity: 1 }} style={{ fontSize: 44, fontWeight: 700, letterSpacing: "-.02em", margin: "6px 0 2px" }}>{wallet ? rs(wallet.balance) : "…"}</motion.div>
        <div className="ts-why" style={{ marginTop: 0 }}>{buyer ? "Pre-loaded money you pay with at your door. Nothing leaves it until you pay for a parcel." : "Money you have earned from delivered parcels."}</div>

        {buyer && (
          <div className="ts-card ts-col" style={{ marginTop: 14 }}>
            <div className="ts-tiny">Add money</div>
            <div className="ts-tags">{[500, 1000, 2000, 5000].map((a) => <button key={a} className={"ts-btn " + (amount === a ? "" : "ghost")} style={{ padding: "8px 12px" }} onClick={() => setAmount(a)}>{rs(a)}</button>)}</div>
            <input className="ts-input" inputMode="numeric" value={amount || ""} placeholder="Amount" onChange={(e) => setAmount(Number(e.target.value.replace(/\D/g, "")) || 0)} />
            <div className="ts-col" style={{ gap: 6 }}>{wallet?.linked.map((l) => <button key={l} className={"ts-option" + (source === l ? " on" : "")} onClick={() => setSource(l)}>From {l}</button>)}</div>
            <input className="ts-input" type="password" inputMode="numeric" maxLength={4} placeholder="Wallet PIN (demo: 2580)" value={pin} onChange={(e) => setPin(e.target.value.replace(/\D/g, ""))} />
            <button className="ts-btn block" disabled={!amount || pin.length < 4} onClick={add}>Add {rs(amount)}</button>
            {ok && <div className="ts-why" style={{ color: "var(--ts-ink-deep)" }}>✓ {ok}</div>}
            {err && <div className="ts-err" role="alert">{err}</div>}
            <div className="ts-hash">Demo money only. A real wallet needs a licensed payment partner.</div>
          </div>
        )}

        <div className="ts-tiny" style={{ margin: "16px 0 4px" }}>Recent activity</div>
        {!wallet?.txns.length ? <div className="ts-why">No activity yet.</div> : wallet.txns.slice(0, 12).map((t) => (
          <div key={t.id} className="ts-txn"><span>{KIND[t.kind]}</span><span style={{ fontWeight: 600, color: t.amount < 0 ? "var(--ts-ink-deep)" : "var(--ts-ink)" }}>{t.amount > 0 ? "+" : ""}{rs(t.amount)}</span>
            <span className="ts-hash">{t.reference}</span><span className="ts-hash" style={{ textAlign: "right" }}>{new Date(t.created_at).toLocaleTimeString()}</span></div>))}
      </motion.div>
    </motion.div>
  );
}

/* the red alarm: the wallet cannot cover the parcel and someone is waiting at the door */
export function ShortAlarm({ short, h, role, onAdd }: { short: number; h: Handover; role: "CONSUMER" | "DELIVERY"; onAdd?: () => void }) {
  return (
    <motion.div className="ts-alarm" initial={{ opacity: 0, scale: .96 }} animate={{ opacity: 1, scale: 1 }}>
      <motion.div className="ts-alarm-pulse" animate={{ opacity: [0.2, 0.7, 0.2] }} transition={{ duration: 1.4, repeat: Infinity }} />
      <div style={{ position: "relative" }}>
        <span className="ts-chip bad">{role === "CONSUMER" ? "Wallet too low" : "Buyer can't pay yet"}</span>
        <div style={{ fontWeight: 700, fontSize: 22, margin: "8px 0 2px" }}>Short by {rs(short)}</div>
        <div className="ts-why bad" style={{ marginTop: 0, marginBottom: 12 }}>
          {role === "CONSUMER" ? `${h.agent_name} is waiting at your door. Add money now, or call them.` : "The buyer has been alerted by SMS, email and in the app. You can call them to hurry things up, or wait a few minutes."}
        </div>
        <div className="ts-col" style={{ gap: 8 }}>
          {role === "CONSUMER" && onAdd && <button className="ts-btn red block" onClick={onAdd}>Add money now</button>}
          <Call phone={role === "CONSUMER" ? h.agent_phone : h.buyer_phone} label={role === "CONSUMER" ? `Call ${h.agent_name}` : "Call the buyer"} />
        </div>
      </div>
    </motion.div>
  );
}

/* PIN entry before the money is reserved */
export function PinSheet({ amount, balance, onSubmit, onClose }: { amount: number; balance: number; onSubmit: (pin: string) => Promise<void>; onClose: () => void }) {
  const [pin, setPin] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const go = async () => { setBusy(true); setErr(""); try { await onSubmit(pin); onClose(); } catch (e: any) { setErr(e.message); setPin(""); } finally { setBusy(false); } };
  return (
    <motion.div className="ts-sheet" style={{ zIndex: 35 }} initial="h" animate="s" exit="h">
      <motion.div className="back" variants={{ h: { opacity: 0 }, s: { opacity: 1 } }} onClick={onClose} />
      <motion.div className="panel" variants={{ h: { y: "100%" }, s: { y: 0 } }} transition={{ type: "spring", damping: 32, stiffness: 300 }}>
        <div className="ts-between"><h3 className="ts-h2">Pay from your <em>wallet</em></h3><button className="ts-chip" onClick={onClose}>Cancel</button></div>
        <div className="ts-card" style={{ margin: "10px 0" }}>
          <div className="ts-sum"><span>Wallet balance</span><span>{rs(balance)}</span></div>
          <div className="ts-sum"><span>This parcel</span><span>-{rs(amount)}</span></div>
          <div className="ts-sum total"><span>Balance after</span><span>{rs(balance - amount)}</span></div>
        </div>
        <p className="ts-why">The money is set aside now, but the seller is only paid when the delivery person confirms the handover with the code you will see next.</p>
        <input className="ts-input" type="password" inputMode="numeric" maxLength={4} autoFocus placeholder="Wallet PIN (demo: 2580)" value={pin} onChange={(e) => setPin(e.target.value.replace(/\D/g, ""))} />
        {err && <div className="ts-err" role="alert">{err}</div>}
        <button className="ts-btn block" style={{ marginTop: 10 }} disabled={pin.length < 4 || busy} onClick={go}>{busy ? "Paying…" : `Pay ${rs(amount)}`}</button>
      </motion.div>
    </motion.div>
  );
}

/* the one-time code the buyer shows the delivery person: proof the right person is holding the parcel */
export function CodeCard({ snap }: { snap: Snapshot }) {
  const h = snap.handover!;
  const [code, setCode] = useState("");
  const left = useCountdown(h.reserve_expires_at, snap.now);
  useEffect(() => { api<{ code: string }>(`/shipments/${snap.shipment.id}/handover/code`).then((r) => setCode(r.code)).catch(() => setCode("")); }, [snap.shipment.id, h.state, h.reserve_expires_at]);
  return (
    <div className="ts-card" style={{ borderColor: "var(--ts-ink)", textAlign: "center" }}>
      <span className="ts-chip ok">{rs(snap.payment.amount)} reserved</span>
      <div className="ts-tiny" style={{ marginTop: 12 }}>Your handover code</div>
      <div className="ts-code">{code ? code.split("").map((d, i) => <motion.span key={i} initial={{ y: 14, opacity: 0 }} animate={{ y: 0, opacity: 1 }} transition={{ delay: i * 0.06 }}>{d}</motion.span>) : "······"}</div>
      <div style={{ fontWeight: 600 }}>{left > 0 ? `Valid for ${mmss(left)}` : "Time is up"}</div>
      <p className="ts-why">Hold the parcel first, then show this code to <b>{h.agent_name}</b>. Never read it out to anyone else, and never before you have the parcel in your hands. If nobody confirms it in time, your money goes back to your wallet automatically.</p>
      <Call phone={h.agent_phone} label={`Call ${h.agent_name}`} />
    </div>
  );
}
