import { useEffect, useState } from "react";
import { motion } from "motion/react";
import { useNavigate } from "react-router-dom";
import { LiveScene, usePortrait } from "../components/Live";
import { Wipe } from "../components/Wipe";
import { Brand } from "../components/Brand";
import { api, deviceId, loadConfig, saveSession } from "../lib/api";
import { useLive } from "../lib/store";
import { ROLE_LABEL, type Account, type Config, type Session } from "../lib/types";

export function Join() {
  const nav = useNavigate();
  const { snap } = useLive();
  const portrait = usePortrait();
  const [cfg, setCfg] = useState<Config | null>(null);
  const [pick, setPick] = useState<Account | null>(null);
  const [pin, setPin] = useState("");
  const [err, setErr] = useState("");
  useEffect(() => {
    const load = () => loadConfig().then(setCfg).catch(() => {});
    load(); const t = setInterval(load, 4000); return () => clearInterval(t);
  }, []);
  const signIn = async (acc: Account, p: string) => {
    try {
      const s = await api<Session>("/auth/join", { body: { account_id: acc.id, pin: p, device_id: deviceId() } });
      saveSession(s); nav("/app");
    } catch (e: any) { setErr(e.message); }
  };
  return (
    <div className="ts-stage">
      <div className="ts-join">
        <div className="ts-join-panel">
          <div className="ts-between"><Brand />
            <span className="ts-small"><a href="/screen">Big screen</a> · <a href="/admin">Presenter</a></span></div>
          <h1 className="ts-h1" style={{ marginTop: "7vh", fontSize: "clamp(32px, 4.2vw, 54px)" }}>
            <Wipe text="Pay only when the parcel is" /> <em><Wipe text="verified." delay={0.5} /></em>
          </h1>
          <p className="ts-why" style={{ fontSize: 16, maxWidth: "36ch", marginTop: 14 }}>
            <Wipe text="Every handover is recorded, locked and visible to everyone in the chain." delay={0.9} step={0.03} />
          </p>
          <div className="ts-tiny" style={{ marginTop: 28 }}>Sign in as</div>
          <motion.div className="ts-acct-list" initial="h" animate="s" transition={{ staggerChildren: 0.08, delayChildren: 0.8 }}>
            {(cfg?.accounts ?? []).map((a, i) => (
              <motion.button key={a.id} className={"ts-acct" + (pick?.id === a.id ? " on" : "")} variants={{ h: { opacity: 0, x: -14 }, s: { opacity: 1, x: 0 } }}
                disabled={!cfg?.rehearsal && a.busy && pick?.id !== a.id} onClick={() => { setPick(a); setPin(""); setErr(""); }}>
                <span className="n">{i + 1}</span>
                <span><b>{a.name}</b><br /><span className="ts-tiny">{ROLE_LABEL[a.role]} · {a.org}</span></span>
                <span className="ts-tiny">{a.busy && !cfg?.rehearsal ? "signed in" : ""}</span>
              </motion.button>
            ))}
          </motion.div>
          {pick && (
            <motion.form initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} className="ts-col" style={{ marginTop: 18 }}
                         onSubmit={(e) => { e.preventDefault(); signIn(pick, pin); }}>
              <label className="ts-field"><span className="ts-tiny">PIN for {pick.name}</span>
                <input className="ts-input" value={pin} onChange={(e) => setPin(e.target.value)} inputMode="numeric" type="password" autoFocus /></label>
              <button className="ts-btn block" type="submit" disabled={!pin}>Sign in</button>
            </motion.form>
          )}
          <div className="ts-err" role="alert">{err}</div>
          <p className="ts-why" style={{ marginTop: "auto", paddingTop: 18, fontSize: 12 }}>
            Each person has their own account and PIN. One device can be one person at a time.
          </p>
        </div>
        <div className="ts-join-art"><LiveScene snap={snap} fit={portrait ? "strip" : "meet"} still={portrait} /></div>
      </div>
    </div>
  );
}
