import { useSyncExternalStore } from "react";
import { api, loadSession } from "./api";
import type { Notice } from "./types";

/* The messages a person receives (SMS and email), plus the push-style toasts for ones that just arrived. */
type S = { items: Notice[]; toasts: Notice[]; screen: Notice[] };
let st: S = { items: [], toasts: [], screen: [] };
const subs = new Set<() => void>();
const set = (p: Partial<S>) => { st = { ...st, ...p }; subs.forEach((f) => f()); };

export async function refreshInbox() {
  if (!loadSession()) return set({ items: [] });
  try { set({ items: (await api<{ items: Notice[] }>("/notifications")).items }); } catch { /* offline */ }
}
export async function markAllRead() {
  try { await api("/notifications/read", { method: "POST" }); } catch { return; }
  set({ items: st.items.map((n) => ({ ...n, read_at: n.read_at ?? new Date().toISOString() })) });
}
/* urgent messages ring: a short triple beep and a vibration. Browsers may block sound until the person has touched the page. */
function ring() {
  try {
    navigator.vibrate?.([220, 90, 220, 90, 220]);
    const ac = new (window.AudioContext || (window as any).webkitAudioContext)();
    [0, 0.28, 0.56].forEach((t) => {
      const o = ac.createOscillator(), g = ac.createGain();
      o.type = "square"; o.frequency.value = 880; g.gain.value = 0.06; o.connect(g); g.connect(ac.destination);
      o.start(ac.currentTime + t); o.stop(ac.currentTime + t + 0.18);
    });
  } catch { /* no sound available */ }
}
export const dismissToast = (id: number) => set({ toasts: st.toasts.filter((t) => t.id !== id) });

/* called by the live socket for every new notification, to everyone; each device keeps only its own */
export function receive(items: Notice[]) {
  set({ screen: [...items.reverse(), ...st.screen].slice(0, 6) });  // the projector shows who was told what
  const me = loadSession()?.participant.id;
  if (!me) return;
  const mine = items.filter((n) => n.to_id === me && !st.items.some((x) => x.id === n.id));
  if (!mine.length) return;
  const seen = new Set<string>();
  const toasts = mine.filter((n) => (n.channel === "SMS" || n.severity !== "info") && !seen.has(n.subject) && seen.add(n.subject));
  set({ items: [...mine.reverse(), ...st.items], toasts: [...toasts, ...st.toasts].slice(0, 3) });
  if (toasts.some((n) => n.severity === "alert")) ring();
  toasts.forEach((n) => setTimeout(() => dismissToast(n.id), 7000));
}
export const useInbox = () => useSyncExternalStore((f) => { subs.add(f); return () => subs.delete(f); }, () => st);
export const unreadCount = (s: S) => s.items.filter((n) => !n.read_at).length;
