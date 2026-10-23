import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Brand } from "../components/Brand";
import { ChainBadge } from "../components/Ledger";
import { api, deviceId, loadSession, saveSession, signOut } from "../lib/api";
import { setSnap, useLive } from "../lib/store";
import type { Notice, Session, Snapshot } from "../lib/types";

export function Admin() {
  const nav = useNavigate();
  const { snap, list } = useLive();
  const [msg, setMsg] = useState("");
  const [reason, setReason] = useState("");
  const [bal, setBal] = useState(10000);
  const [door, setDoor] = useState(900);
  const [codeW, setCodeW] = useState(300);
  const [log, setLog] = useState<Notice[]>([]);
  const { pulse } = useLive();
  useEffect(() => { api<Notice[]>("/admin/notifications").then(setLog).catch(() => {}); }, [pulse, snap?.shipment.state]);
  const [authed, setAuthed] = useState(loadSession()?.participant.role === "ADMIN");
  const [pin, setPin] = useState("");
  const login = async () => {
    try { saveSession(await api<Session>("/auth/join", { body: { account_id: "acc-admin", pin, device_id: deviceId() } })); setAuthed(true); setMsg(""); }
    catch (e: any) { setMsg(e.message); }
  };
  const act = async (label: string, f: () => Promise<unknown>) => {
    try { const r = await f(); if (r && typeof r === "object" && "shipment" in r) setSnap(r as Snapshot); setMsg(label + " done"); } catch (e: any) { setMsg(e.message); }
  };
  const sid = snap?.shipment.id;
  if (!authed) return (
    <div style={{ padding: 24, maxWidth: 420, margin: "12vh auto 0" }}>
      <Brand /><h2 className="ts-h2" style={{ marginTop: 28 }}>Presenter <em>sign in</em></h2>
      <form className="ts-col" onSubmit={(e) => { e.preventDefault(); login(); }}>
        <input className="ts-input" type="password" inputMode="numeric" placeholder="Presenter PIN" value={pin} onChange={(e) => setPin(e.target.value)} autoFocus />
        <button className="ts-btn block" type="submit">Sign in</button><div className="ts-err">{msg}</div>
      </form>
    </div>
  );
  const B = ({ t, f, cls = "" }: { t: string; f: () => Promise<unknown>; cls?: string }) => <button className={"ts-btn " + cls} onClick={() => act(t, f)}>{t}</button>;
  return (
    <div style={{ padding: 20, maxWidth: 720, margin: "0 auto" }}>
      <div className="ts-between"><Brand /><button className="ts-chip" onClick={async () => { await signOut(); nav("/"); }}>sign out</button></div>
      <h2 className="ts-h2" style={{ marginTop: 18 }}>Presenter <em>controls</em></h2>
      <p className="ts-why">{msg || "Presenter only. Every button here writes through the same API the phones use."}</p>

      <div className="ts-card ts-col" style={{ marginBottom: 12 }}>
        <div className="ts-tiny">Scenario (what the checks will find)</div>
        <div className="ts-row" style={{ flexWrap: "wrap" }}>
          {([["A", "A · all good"], ["B", "B · seal tampered"], ["C", "C · wrong product at home"]] as const).map(([k, l]) => (
            <button key={k} className={"ts-btn " + (snap?.settings.scenario === k ? "" : "ghost")} onClick={() => act("Scenario " + k, () => api("/admin/scenario", { body: { scenario: k } }))}>{l}</button>))}
        </div>
        <div className="ts-row" style={{ flexWrap: "wrap" }}>
          <B t="Clear the current order" cls="ghost" f={() => api("/admin/reset", { method: "POST" })} />
        </div>
        <div className="ts-why">Scenario B: the warehouse seal photo fails its integrity check (or scan a different sticker at any stage). Scenario C: in mock mode, the product the buyer scans at home is not the one registered, so a complaint is filed.</div>
      </div>

      <div className="ts-card ts-col" style={{ marginBottom: 12 }}>
        <div className="ts-tiny">Checks and devices</div>
        <div className="ts-row" style={{ flexWrap: "wrap" }}>
          {(["live", "mock"] as const).map((m) => (
            <button key={m} className={"ts-btn " + (snap?.settings.ai_mode === m ? "" : "ghost")} onClick={() => act("AI " + m, () => api("/admin/ai-mode", { body: { scenario: m } }))}>
              {m === "live" ? "AI live (local models)" : "AI mock (scripted)"}</button>))}
        </div>
        <div className="ts-row" style={{ flexWrap: "wrap" }}>
          <button className={"ts-btn " + (snap?.settings.rehearsal ? "" : "ghost")} onClick={() => act("Rehearsal mode", async () => { await api("/admin/rehearsal", { body: { on: !snap?.settings.rehearsal } }); })}>
            Rehearsal on one laptop: {snap?.settings.rehearsal ? "ON" : "off"}</button>
          <B t="Sign everyone out" cls="ghost" f={() => api("/admin/signout-all", { method: "POST" })} />
        </div>
        <div className="ts-why">Normally one device is one person. Turn rehearsal on to open each person in its own browser tab on this laptop, then turn it off and sign everyone out before the real demo. Refresh open tabs after switching.</div>
      </div>

      <div className="ts-card ts-col" style={{ marginBottom: 12 }}>
        <div className="ts-tiny">Seal stickers</div>
        <div className="ts-why">The system never gives out a seal code: it reads the serial printed on the sticker on the parcel. For a rehearsal, print a sheet of stickers (a stand-in for a supplier's roll) and stick one on the box.</div>
        <div className="ts-row"><Link className="ts-btn" to="/seals" target="_blank" style={{ textDecoration: "none" }}>Open the sticker sheet</Link></div>
        {snap?.seal.registered && <div className="ts-why">This parcel's registered seal ends in {snap.seal.hint} (read {snap.seal.how || "from the parcel"}).</div>}
      </div>

      <div className="ts-card ts-col" style={{ marginBottom: 12 }}>
        <div className="ts-tiny">Wallet and timing (to show the alarm and the timeout quickly)</div>
        <label className="ts-field"><span className="ts-why">Buyer's wallet balance (₹)</span><input className="ts-input" inputMode="numeric" value={bal} onChange={(e) => setBal(Number(e.target.value.replace(/\D/g, "")) || 0)} /></label>
        <div className="ts-row">
          <label className="ts-field" style={{ flex: 1 }}><span className="ts-why">Door visit lasts (seconds)</span><input className="ts-input" inputMode="numeric" value={door} onChange={(e) => setDoor(Number(e.target.value.replace(/\D/g, "")) || 0)} /></label>
          <label className="ts-field" style={{ flex: 1 }}><span className="ts-why">Handover code lasts (seconds)</span><input className="ts-input" inputMode="numeric" value={codeW} onChange={(e) => setCodeW(Number(e.target.value.replace(/\D/g, "")) || 0)} /></label></div>
        <B t="Apply" f={() => api("/admin/wallet", { body: { balance: bal, door_window_sec: door, code_window_sec: codeW } })} />
        <div className="ts-why">Try 150 to see the short-wallet alarm. Try 30 seconds for the code to watch the money go back by itself.</div>
      </div>

      {snap && (
        <div className="ts-card ts-col" style={{ marginBottom: 12 }}>
          <div className="ts-between"><div className="ts-tiny">Ledger ({list.length} shipments)</div><ChainBadge snap={snap} /></div>
          <div className="ts-row" style={{ flexWrap: "wrap" }}>
            <B t="Simulate DB tampering" cls="red" f={() => api(`/admin/tamper-demo/${sid}`, { method: "POST" })} />
            <B t="Restore" cls="ghost" f={() => api(`/admin/tamper-restore/${sid}`, { method: "POST" })} />
          </div>
          <div className="ts-tiny" style={{ marginTop: 6 }}>Reviewer decision ({snap.shipment.state.toLowerCase().replace("_", " ")})</div>
          <input className="ts-card" placeholder="Reason (required)" value={reason} onChange={(e) => setReason(e.target.value)} />
          <div className="ts-row" style={{ flexWrap: "wrap" }}>
            {(["resume", "refund", "reject"] as const).map((d) => <B key={d} t={d} cls="ghost" f={() => api(`/review/${sid}/decision`, { body: { decision: d, reason } })} />)}
          </div>
        </div>
      )}
      <div className="ts-card" style={{ marginBottom: 12 }}>
        <div className="ts-between"><div className="ts-tiny">Who was told what ({log.length})</div><span className="ts-hash">emails saved in api/data/outbox</span></div>
        <div className="ts-why" style={{ marginBottom: 6 }}>Only the people who must act or are affected get a message. Each row says why.</div>
        {log.slice(0, 40).map((n) => (
          <div key={n.id} className="ts-txn" style={{ gridTemplateColumns: "1fr auto" }}>
            <span><b>{n.to_name}</b> <span className="ts-tiny">{n.to_role.toLowerCase()} · {n.channel === "SMS" ? "SMS" : "email"} · {n.severity}</span><br />{n.subject}<br /><span className="ts-why" style={{ fontSize: 12 }}>why: {n.why}</span></span>
            <span className="ts-hash">{new Date(n.created_at).toLocaleTimeString()}</span></div>))}
      </div>
      <div className="ts-row ts-small"><Link to="/screen" target="_blank">Open big screen</Link></div>
    </div>
  );
}
