import { animate, motion, useMotionValue, useTransform } from "motion/react";
import { useEffect, useRef, useState } from "react";

/* Slide to commit. Deliberately effortful: "Good to Go" locks a stage forever. */
export function Slide({ label, disabled, tone = "ink", onConfirm }: {
  label: string; disabled?: boolean; tone?: "ink" | "red"; onConfirm: () => Promise<void> | void;
}) {
  const box = useRef<HTMLDivElement>(null);
  const x = useMotionValue(0);
  const [busy, setBusy] = useState(false);
  const fill = useTransform(x, (v) => v + 55);
  const labelOpacity = useTransform(x, [0, 120], [1, 0]);
  const [max, setMax] = useState(240);
  useEffect(() => {
    const measure = () => setMax(Math.max(80, (box.current?.clientWidth ?? 300) - 60));
    measure(); addEventListener("resize", measure); return () => removeEventListener("resize", measure);
  }, []);
  return (
    <div ref={box} className={"ts-slide" + (disabled ? " off" : "")} style={tone === "red" ? { ["--ts-ink" as string]: "var(--ts-red)" } : undefined}>
      <motion.div className="fill" style={{ width: fill }} />
      <motion.div className="lab" style={{ opacity: labelOpacity }}>{label}</motion.div>
      <motion.div className="knob" drag="x" dragConstraints={{ left: 0, right: max }} dragElastic={0} dragMomentum={false}
        style={{ x }} whileTap={{ scale: 1.06 }}
        onDragEnd={async () => {
          if (x.get() > max * 0.86 && !busy) {
            setBusy(true);
            animate(x, max, { duration: 0.12 });
            try { await onConfirm(); } finally { setBusy(false); animate(x, 0, { type: "spring", stiffness: 260, damping: 26 }); }
          } else animate(x, 0, { type: "spring", stiffness: 380, damping: 30 });
        }}>
        {busy ? "…" : "→"}
      </motion.div>
    </div>
  );
}
