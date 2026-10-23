import { AnimatePresence, motion } from "motion/react";
import { useEffect, useState } from "react";
import { dismissToast, markAllRead, refreshInbox, unreadCount, useInbox } from "../lib/inbox";
import type { Notice } from "../lib/types";

const when = (iso: string) => new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

export function Bell({ onOpen }: { onOpen: () => void }) {
  const inbox = useInbox(), n = unreadCount(inbox);
  useEffect(() => { refreshInbox(); }, []);
  return (
    <button className="ts-bell" onClick={onOpen} aria-label={`Messages, ${n} unread`}>
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9M13.7 21a2 2 0 0 1-3.4 0" /></svg>
      {n > 0 && <motion.i key={n} initial={{ scale: 0 }} animate={{ scale: 1 }} transition={{ type: "spring", stiffness: 500, damping: 18 }}>{n}</motion.i>}
    </button>
  );
}

/* a message as it would look on the phone: SMS in a bubble, email as a mail card */
export function Message({ n, compact }: { n: Notice; compact?: boolean }) {
  return n.channel === "SMS" ? (
    <div className={"ts-msg sms " + n.severity}><div className="ts-tiny">SMS · {when(n.created_at)}</div><div className="bubble">{n.body}</div>
      {!compact && <div className="ts-why" style={{ fontSize: 12 }}>Why you got this: {n.why}</div>}</div>
  ) : (
    <div className={"ts-msg mail " + n.severity}><div className="ts-tiny">Email · {when(n.created_at)} · from no-reply@trustseal.demo</div>
      <div style={{ fontWeight: 700, margin: "4px 0" }}>{n.subject}</div><div style={{ whiteSpace: "pre-line", fontSize: 14 }}>{n.body}</div>
      {!compact && <div className="ts-why" style={{ fontSize: 12, marginTop: 8 }}>Why you got this: {n.why}</div>}</div>
  );
}

export function InboxSheet({ onClose }: { onClose: () => void }) {
  const inbox = useInbox();
  const [tab, setTab] = useState<"ALL" | "SMS" | "EMAIL">("ALL");
  useEffect(() => { const t = setTimeout(markAllRead, 1200); return () => clearTimeout(t); }, []);
  const shown = inbox.items.filter((n) => tab === "ALL" || n.channel === tab);
  return (
    <motion.div className="ts-sheet" initial="h" animate="s" exit="h">
      <motion.div className="back" variants={{ h: { opacity: 0 }, s: { opacity: 1 } }} onClick={onClose} />
      <motion.div className="panel" variants={{ h: { y: "100%" }, s: { y: 0 } }} transition={{ type: "spring", damping: 32, stiffness: 300 }}>
        <div className="ts-between"><h3 className="ts-h2">Your <em>messages</em></h3><button className="ts-chip" onClick={onClose}>Close</button></div>
        <p className="ts-why" style={{ marginTop: 0 }}>You only get a message when it affects you or you have to act. Each one says why.</p>
        <div className="ts-row" style={{ margin: "10px 0" }}>{(["ALL", "SMS", "EMAIL"] as const).map((t) => <button key={t} className={"ts-chip" + (tab === t ? " ok" : "")} onClick={() => setTab(t)}>{t === "ALL" ? "All" : t === "SMS" ? "SMS" : "Email"}</button>)}</div>
        {!shown.length ? <p className="ts-why">Nothing yet. Messages for you will appear here the moment they are sent.</p> : shown.map((n) => <Message key={n.id} n={n} />)}
      </motion.div>
    </motion.div>
  );
}

/* push-style banners that slide in from the top when a new message arrives */
export function Toasts({ onOpen }: { onOpen: () => void }) {
  const { toasts } = useInbox();
  return (
    <div className="ts-toasts">
      <AnimatePresence>
        {toasts.map((n) => (
          <motion.button key={n.id} className={"ts-toast " + n.severity} layout initial={{ y: -80, opacity: 0 }} animate={{ y: 0, opacity: 1 }} exit={{ opacity: 0, x: 60 }}
                         transition={{ type: "spring", stiffness: 380, damping: 30 }} onClick={() => { dismissToast(n.id); onOpen(); }}>
            <span className="ts-tiny" style={{ color: "inherit", opacity: .75 }}>{n.channel === "SMS" ? "New SMS" : "New email"} · TrustSeal</span>
            <span style={{ fontWeight: 600, display: "block" }}>{n.channel === "SMS" ? n.body.replace(/^TrustSeal( ALERT)?: /, "").replace(/^./, (c) => c.toUpperCase()) : n.subject}</span>
          </motion.button>
        ))}
      </AnimatePresence>
    </div>
  );
}
