import { useEffect, useRef, useState } from "react";
import { Scene } from "../art/Scene";
import type { Snapshot } from "../lib/types";

export function deriveScene(snap: Snapshot | null) {
  const cps = snap?.checkpoints ?? [];
  const locked = cps.filter((c) => c.state === "LOCKED").length;
  const failedIdx = cps.findIndex((c) => c.state === "FAILED");
  const disputed = snap?.shipment.state === "DISPUTED" || snap?.shipment.state === "RESOLVED_REFUND";
  return { locked, failed: failedIdx >= 0 ? failedIdx : disputed ? 3 : null, parcelAt: locked };
}

/* Birds scatter from the parcel every time a handover lands (not on first load). */
function useBurst(snap: Snapshot | null) {
  const seen = useRef<number | null>(null);
  const [n, setN] = useState(0);
  const count = snap?.events.filter((e) => e.event_type === "HANDOVER_CONFIRMED" || e.event_type === "ACCEPTED").length ?? 0;
  useEffect(() => {
    if (seen.current !== null && count > seen.current) setN((v) => v + 1);
    seen.current = count;
  }, [count, snap?.shipment.id]);
  return n;
}

export function LiveScene({ snap, still, fit, onSpan }: { snap: Snapshot | null; still?: boolean; fit?: "cover" | "meet" | "strip"; onSpan?: (i: number) => void }) {
  const burst = useBurst(snap);
  const d = deriveScene(snap);
  return <Scene className="ts-scene" locked={d.locked} failed={d.failed} parcelAt={d.parcelAt} burst={burst} still={still} fit={fit} onSpan={onSpan} />;
}

/* Portrait phones cannot show a panorama edge to edge, so they get the whole picture, anchored to the bottom. */
export function usePortrait() {
  const q = "(max-aspect-ratio: 1/1)";
  const [p, setP] = useState(() => matchMedia(q).matches);
  useEffect(() => { const m = matchMedia(q); const f = () => setP(m.matches); m.addEventListener("change", f); return () => m.removeEventListener("change", f); }, []);
  return p;
}
