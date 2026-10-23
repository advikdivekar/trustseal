import { memo, useMemo } from "react";
import { motion } from "motion/react";

/* The delivery route as an illustration: shop, warehouse, van, your door, home.
   One road segment per handover. A segment inks in when its stage locks, the parcel rides to the next stop,
   and a failed segment cracks and turns red. Same indigo and cream as the rest of the app. */

export const STOPS = [190, 495, 800, 1105, 1410];
const LABELS = ["SHOP", "WAREHOUSE", "DELIVERY", "YOUR DOOR", "DELIVERED"];
const ROAD = 668, W = 1600, H = 900, INK = "#5b43d6", DEEP = "#2a1b85", LIGHT = "#e4defa", PAPER = "#fbfcef";

function rng(seed: number) {
  return () => {
    seed |= 0; seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/* ---------- static backdrop: skyline, trees ---------- */
const Backdrop = memo(function Backdrop() {
  const city = useMemo(() => {
    const r = rng(11), out: { x: number; w: number; h: number }[] = [];
    for (let x = -20; x < W + 20; ) { const w = 60 + r() * 90, h = 70 + r() * 230; out.push({ x, w, h }); x += w + 4 + r() * 10; }
    return out;
  }, []);
  const trees = useMemo(() => { const r = rng(5); return Array.from({ length: 16 }, () => ({ x: 40 + r() * 1520, s: 0.7 + r() * 0.6 })); }, []);
  return (
    <g>
      {city.map((b, i) => (
        <g key={i}>
          <rect x={b.x} y={ROAD - 50 - b.h} width={b.w} height={b.h} fill={i % 3 ? LIGHT : "#d6cffa"} stroke={DEEP} strokeWidth="1.2" opacity=".8" />
          <rect x={b.x} y={ROAD - 50 - b.h} width={b.w} height={b.h} fill="url(#grid)" opacity=".7" />
        </g>
      ))}
      {trees.map((t, i) => (
        <g key={i} transform={`translate(${t.x} ${ROAD - 52}) scale(${t.s})`}>
          <path d="M0 0 V-34" stroke={DEEP} strokeWidth="3" />
          <circle cx="0" cy="-50" r="22" fill={INK} stroke={DEEP} strokeWidth="1.4" opacity=".92" />
          <circle cx="-7" cy="-57" r="9" fill="#cfc7f7" opacity=".7" />
        </g>
      ))}
    </g>
  );
});

/* ---------- the five stops ---------- */
const Shop = () => (
  <g stroke={DEEP} strokeWidth="2" strokeLinejoin="round">
    <rect x="-84" y="-176" width="168" height="124" fill={PAPER} />
    <rect x="-84" y="-176" width="168" height="124" fill="url(#hatch)" opacity=".35" />
    <path d="M-96 -176 H96 L84 -130 H-84 Z" fill={INK} />
    {[-72, -40, -8, 24, 56].map((x) => <path key={x} d={`M${x} -176 L${x + 16} -130 H${x + 32} L${x + 32 - 16} -176 Z`} fill={PAPER} stroke="none" opacity=".92" />)}
    <path d="M-84 -130 q14 16 28 0 q14 16 28 0 q14 16 28 0 q14 16 28 0 q14 16 28 0 q14 16 28 0" fill="none" />
    <rect x="-66" y="-112" width="62" height="62" fill={LIGHT} /><path d="M-35 -112 V-50 M-66 -81 H-4" fill="none" />
    <rect x="18" y="-112" width="48" height="62" fill={INK} /><circle cx="56" cy="-80" r="3" fill={PAPER} stroke="none" />
    <rect x="-40" y="-204" width="80" height="22" fill={PAPER} /><text x="0" y="-188" textAnchor="middle" fontSize="14" fontWeight="700" fill={DEEP} stroke="none" letterSpacing="2">SELLER</text>
  </g>
);
const Warehouse = () => (
  <g stroke={DEEP} strokeWidth="2" strokeLinejoin="round">
    <rect x="-104" y="-130" width="208" height="78" fill={PAPER} />
    <path d="M-104 -130 L-104 -168 L-52 -130 L-52 -168 L0 -130 L0 -168 L52 -130 L52 -168 L104 -130 Z" fill={INK} />
    {[-70, 6].map((x) => (
      <g key={x}><rect x={x} y="-108" width="64" height="56" fill={LIGHT} />{[-96, -84, -72, -60].map((y) => <path key={y} d={`M${x} ${y} H${x + 64}`} fill="none" strokeWidth="1.4" />)}</g>
    ))}
    <rect x="80" y="-94" width="18" height="42" fill={INK} />
    <rect x="-96" y="-152" width="56" height="16" fill={PAPER} stroke="none" /><text x="-68" y="-140" textAnchor="middle" fontSize="11" fontWeight="700" fill={DEEP} stroke="none" letterSpacing="1.5">HUB</text>
  </g>
);
const Van = () => (
  <g stroke={DEEP} strokeWidth="2" strokeLinejoin="round">
    <g className="ts-idle">
      <path d="M-96 -52 V-132 Q-96 -142 -86 -142 H34 V-52 Z" fill={PAPER} />
      <path d="M34 -142 H68 Q78 -142 86 -126 L102 -92 V-52 H34 Z" fill={INK} />
      <path d="M44 -132 H66 Q72 -132 76 -122 L88 -98 H44 Z" fill={LIGHT} />
      <rect x="-96" y="-112" width="130" height="14" fill={INK} stroke="none" /><text x="-31" y="-101" textAnchor="middle" fontSize="11" fontWeight="700" fill={PAPER} stroke="none" letterSpacing="2">TRUSTSEAL</text>
      <path d="M-96 -86 H34" fill="none" strokeDasharray="4 5" />
      {[-62, 64].map((x) => <g key={x}><circle cx={x} cy="-50" r="19" fill={DEEP} /><circle cx={x} cy="-50" r="8" fill={LIGHT} /></g>)}
    </g>
  </g>
);
const Door = () => (
  <g stroke={DEEP} strokeWidth="2" strokeLinejoin="round">
    <rect x="-90" y="-170" width="180" height="118" fill={PAPER} />
    <rect x="-90" y="-170" width="180" height="118" fill="url(#hatch)" opacity=".3" />
    <path d="M-104 -170 L0 -226 L104 -170 Z" fill={INK} />
    <path d="M-34 -52 V-132 Q-34 -150 0 -150 Q34 -150 34 -132 V-52 Z" fill={INK} />
    <circle cx="20" cy="-96" r="4" fill={PAPER} stroke="none" />
    <rect x="-72" y="-128" width="28" height="30" fill={LIGHT} /><rect x="44" y="-128" width="28" height="30" fill={LIGHT} />
    <rect x="-40" y="-52" width="80" height="9" fill={LIGHT} /><text x="0" y="-168" textAnchor="middle" fontSize="13" fontWeight="700" fill={PAPER} stroke="none" letterSpacing="1">No. 12</text>
  </g>
);
const Home = ({ done }: { done: boolean }) => (
  <g stroke={DEEP} strokeWidth="2" strokeLinejoin="round">
    <rect x="-96" y="-150" width="192" height="98" fill={PAPER} />
    <rect x="-96" y="-150" width="192" height="98" fill="url(#hatch)" opacity=".3" />
    <path d="M-112 -150 L0 -230 L112 -150 Z" fill={INK} />
    <rect x="52" y="-218" width="22" height="46" fill={INK} />
    <rect x="-68" y="-124" width="44" height="44" fill={LIGHT} /><path d="M-46 -124 V-80 M-68 -102 H-24" fill="none" />
    <rect x="12" y="-124" width="44" height="72" fill={INK} /><circle cx="46" cy="-90" r="3.5" fill={PAPER} stroke="none" />
    <motion.g initial={false} animate={done ? { scale: 1, opacity: 1 } : { scale: 0.3, opacity: 0 }} style={{ transformOrigin: "0px -182px" }}
              transition={{ type: "spring", stiffness: 300, damping: 14 }}>
      <circle cx="0" cy="-182" r="26" fill={PAPER} stroke={INK} strokeWidth="4" />
      <path d="M-11 -182 L-3 -173 L13 -192" fill="none" stroke={INK} strokeWidth="5" strokeLinecap="round" strokeLinejoin="round" />
    </motion.g>
  </g>
);

/* ---------- road, nodes, parcel ---------- */
type SceneProps = {
  fit?: "cover" | "meet" | "strip"; locked: number; failed: number | null; parcelAt: number; burst: number;
  still?: boolean; className?: string; onSpan?: (i: number) => void;
};

function Road({ locked, failed, onSpan }: Pick<SceneProps, "locked" | "failed" | "onSpan">) {
  return (
    <g>
      <rect x="-10" y={ROAD - 20} width={W + 20} height="40" fill={LIGHT} stroke={DEEP} strokeWidth="1.6" />
      <path d={`M-10 ${ROAD} H${W + 10}`} stroke={DEEP} strokeWidth="2" strokeDasharray="14 12" opacity=".5" />
      {STOPS.slice(0, 4).map((a, i) => {
        const b = STOPS[i + 1], on = i < locked, bad = failed === i;
        return (
          <g key={i} style={{ cursor: onSpan ? "pointer" : undefined }} onClick={() => onSpan?.(i)}>
            <clipPath id={`road${i}`}>
              <motion.rect x={a} y={ROAD - 24} height="48" initial={false} animate={{ width: on ? b - a : 0 }} transition={{ duration: 1.2, ease: [0.65, 0, 0.35, 1] }} />
            </clipPath>
            <g clipPath={`url(#road${i})`}>
              <rect x={a} y={ROAD - 20} width={b - a} height="40" fill={INK} stroke={DEEP} strokeWidth="1.6" />
              <path d={`M${a} ${ROAD} H${b}`} stroke={PAPER} strokeWidth="3" strokeDasharray="14 12" />
            </g>
            {bad && (
              <g>
                <rect x={a} y={ROAD - 20} width={b - a} height="40" fill="#d8452f" opacity=".35" />
                <motion.path d={`M${(a + b) / 2 - 12} ${ROAD - 26} l12 14 l-14 12 l14 14 l-8 12`} fill="none" stroke="#d8452f" strokeWidth="5" strokeLinecap="round"
                  initial={{ pathLength: 0 }} animate={{ pathLength: 1 }} transition={{ duration: 0.5 }} />
              </g>
            )}
          </g>
        );
      })}
    </g>
  );
}

function Nodes({ locked }: { locked: number }) {
  return (
    <g>
      {STOPS.map((x, i) => {
        const reached = i <= locked;
        return (
          <g key={x} transform={`translate(${x} ${ROAD})`}>
            <circle r="22" fill={reached ? INK : PAPER} stroke={DEEP} strokeWidth="2.4" />
            {reached ? <path d="M-9 0 L-2 8 L11 -8" fill="none" stroke={PAPER} strokeWidth="4" strokeLinecap="round" strokeLinejoin="round" />
              : <text y="6" textAnchor="middle" fontSize="18" fontWeight="700" fill={DEEP} style={{ fontFamily: "var(--ts-sans)" }}>{i + 1}</text>}
            <text y="62" textAnchor="middle" fontSize="19" fontWeight="700" letterSpacing="3" fill={DEEP} style={{ fontFamily: "var(--ts-sans)" }}>{LABELS[i]}</text>
          </g>
        );
      })}
    </g>
  );
}

function Parcel({ at, failed }: { at: number; failed: boolean }) {
  const x = STOPS[Math.min(at, 4)];
  return (
    <motion.g initial={false} animate={{ x }} transition={{ type: "tween", duration: 1.5, ease: [0.45, 0.05, 0.4, 1] }}>
      <motion.g key={at} animate={{ y: [0, -46, 0] }} transition={{ duration: 1.5, ease: "easeInOut", times: [0, 0.5, 1] }}>
        <ellipse cx="0" cy={ROAD + 38} rx="30" ry="6" fill={INK} opacity=".2" />
        <g transform={`translate(0 ${ROAD - 108})`} stroke={DEEP} strokeWidth="2.2" strokeLinejoin="round">
          <path d="M-26 8 L0 -4 L26 8 L26 38 L0 50 L-26 38 Z" fill={failed ? "#f6d9d2" : PAPER} />
          <path d="M0 -4 V50 M-26 8 L0 20 L26 8" fill="none" />
          <path d="M-26 8 L0 20 V50 L-26 38 Z" fill="url(#hatch)" />
          <circle cx="11" cy="32" r="7.5" fill="#d8452f" stroke="none" />
          <path d="M7.5 32 h7 M11 28.5 v7" stroke={PAPER} strokeWidth="1.6" />
        </g>
      </motion.g>
    </motion.g>
  );
}

/* A ring and a handful of paper squares leave the stop where a stage just locked. */
const Burst = memo(function Burst({ n, from }: { n: number; from: number }) {
  const bits = useMemo(() => { const r = rng(n * 77 + 3); return Array.from({ length: 14 }, () => ({ dx: (r() - 0.5) * 360, dy: -(80 + r() * 220), s: 6 + r() * 8, rot: r() * 360, d: r() * 0.25 })); }, [n]);
  if (!n) return null;
  return (
    <g>
      {[0, 0.18, 0.36].map((d) => (
        <motion.circle key={`${n}r${d}`} cx={from} cy={ROAD} fill="none" stroke={INK} strokeWidth="3" initial={{ r: 20, opacity: 0.9 }}
          animate={{ r: 150, opacity: 0 }} transition={{ duration: 1.3, delay: d, ease: "easeOut" }} />
      ))}
      {bits.map((b, i) => (
        <motion.rect key={`${n}b${i}`} width={b.s} height={b.s} fill={i % 3 ? INK : "#8d7fe0"} initial={{ x: from, y: ROAD - 40, opacity: 1, rotate: 0 }}
          animate={{ x: from + b.dx, y: ROAD + b.dy, opacity: 0, rotate: b.rot }} transition={{ duration: 1.6, delay: b.d, ease: "easeOut" }} />
      ))}
    </g>
  );
});

export function Scene({ fit = "cover", locked, failed, parcelAt, burst, still, className, onSpan }: SceneProps) {
  const stripy = useMemo(() => Array.from({ length: 12 }, (_, i) => ROAD + 90 + i * 16), []);
  return (
    <svg className={className} viewBox={fit === "strip" ? `0 370 ${W} 440` : `0 0 ${W} ${H}`} preserveAspectRatio={fit === "cover" ? "xMidYMax slice" : "xMidYMax meet"}
         role="img" aria-label="The delivery route from the shop to your door, one road segment per handover">
      <defs>
        <pattern id="hatch" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(35)"><path d="M0 0 V6" stroke={INK} strokeWidth="1.5" /></pattern>
        <pattern id="grid" width="16" height="18" patternUnits="userSpaceOnUse"><rect x="4" y="5" width="7" height="8" fill="#fbfcef" opacity=".85" /></pattern>
      </defs>
      <rect width={W} height={H} fill={PAPER} />
      {!still && (
        <g opacity=".7">
          {[[200, 140, 1], [820, 90, 0.8], [1330, 170, 1.2]].map(([x, y, s], i) => (
            <g key={i} className="ts-cloud" style={{ animationDelay: `-${i * 11}s` }}>
              <path transform={`translate(${x} ${y}) scale(${s})`} d="M-70 0 q-6 -30 24 -30 q10 -30 44 -22 q40 -12 50 22 q30 0 24 30 Z" fill={LIGHT} stroke={DEEP} strokeWidth="1.4" />
            </g>
          ))}
        </g>
      )}
      <Backdrop />
      <rect x="0" y={ROAD + 20} width={W} height={H - ROAD} fill={PAPER} />
      {stripy.map((y) => <path key={y} d={`M0 ${y} H${W}`} stroke={LIGHT} strokeWidth="2" />)}
      <g transform={`translate(${STOPS[0]} ${ROAD - 20})`}><Shop /></g>
      <g transform={`translate(${STOPS[1]} ${ROAD - 20})`}><Warehouse /></g>
      <g transform={`translate(${STOPS[2]} ${ROAD - 20})`}><Van /></g>
      <g transform={`translate(${STOPS[3]} ${ROAD - 20})`}><Door /></g>
      <g transform={`translate(${STOPS[4]} ${ROAD - 20})`}><Home done={locked >= 4} /></g>
      <Road locked={locked} failed={failed} onSpan={onSpan} />
      <Nodes locked={locked} />
      <Parcel at={parcelAt} failed={failed !== null} />
      <Burst n={burst} from={STOPS[Math.min(parcelAt, 4)]} />
    </svg>
  );
}
