import { motion } from "motion/react";
import { useEffect, useState } from "react";

/* Words are drawn in from the left edge, the way the reference footer clips mid-word while it animates. */
export function Wipe({ text, delay = 0, step = 0.06, className }: { text: string; delay?: number; step?: number; className?: string }) {
  return (
    <span className={className} aria-label={text}>
      {text.split(" ").map((w, i) => (
        <motion.span key={i + w} aria-hidden style={{ display: "inline-block", marginRight: "0.26em" }}
          initial={{ clipPath: "inset(0 100% 0 0)", x: -10, opacity: 0 }}
          animate={{ clipPath: "inset(0 0% 0 0)", x: 0, opacity: 1 }}
          transition={{ duration: 0.9, delay: delay + i * step, ease: [0.65, 0, 0.35, 1] }}>
          {w}
        </motion.span>
      ))}
    </span>
  );
}

/* Hex that resolves from noise, so a new ledger hash visibly "settles". */
export function Scramble({ text, run = true }: { text: string; run?: boolean }) {
  const [shown, setShown] = useState(run ? "" : text);
  useEffect(() => {
    if (!run) return setShown(text);
    let i = 0;
    const id = setInterval(() => {
      i += 2;
      setShown(text.slice(0, i) + Array.from({ length: Math.max(0, text.length - i) }, () => "0123456789abcdef"[(Math.random() * 16) | 0]).join(""));
      if (i >= text.length) clearInterval(id);
    }, 24);
    return () => clearInterval(id);
  }, [text, run]);
  return <>{shown || text}</>;
}
