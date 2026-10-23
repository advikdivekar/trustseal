import { useSyncExternalStore } from "react";
import { api } from "./api";
import { receive, refreshInbox } from "./inbox";
import type { ShipmentListItem, Snapshot } from "./types";

type State = { snap: Snapshot | null; list: ShipmentListItem[]; online: boolean; pulse: number; lastEvent: string };
let state: State = { snap: null, list: [], online: false, pulse: 0, lastEvent: "" };
const subs = new Set<() => void>();
const set = (p: Partial<State>) => { state = { ...state, ...p }; subs.forEach((f) => f()); };

async function refetch() {
  try {
    const list = await api<ShipmentListItem[]>("/shipments");
    const snap = list.length ? await api<Snapshot>(`/shipments/${list[0].id}`) : null;
    set({ list, snap });
  } catch { /* server not up yet; the socket retry will call us again */ }
}

let started = false;
export function startLive() {
  if (started) return;
  started = true;
  const connect = () => {
    const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
    ws.onopen = () => { set({ online: true }); refetch(); refreshInbox(); };
    ws.onmessage = (m) => {
      const msg = JSON.parse(m.data);
      if (msg.type === "notify") return receive(msg.items);
      const list: ShipmentListItem[] = msg.list ?? state.list;
      const newest = list[0]?.id;
      if (!newest) return set({ list, snap: null });
      if (msg.shipment && msg.shipment.shipment.id === newest) {
        const prev = state.snap?.events.length ?? 0;
        const grew = msg.shipment.events.length > prev;
        set({ list, snap: msg.shipment, pulse: grew ? state.pulse + 1 : state.pulse,
              lastEvent: grew ? msg.shipment.events.at(-1).event_type : state.lastEvent });
      } else if (!state.snap || state.snap.shipment.id !== newest || msg.shipment === null) {
        set({ list }); refetch();
      } else set({ list });
    };
    ws.onclose = () => { set({ online: false }); setTimeout(connect, 1000); };  // reconnect, then refetch on open
  };
  connect();
}

export const useLive = () => useSyncExternalStore(
  (f) => { subs.add(f); return () => subs.delete(f); }, () => state);
export const setSnap = (snap: Snapshot) => set({ snap });
