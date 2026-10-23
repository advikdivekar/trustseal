export function SealMark({ size = 38 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 38 38" fill="none" stroke="#231a6b" strokeWidth="2" strokeLinejoin="round" aria-hidden>
      <path d="M19 3 L33 11 V27 L19 35 L5 27 V11 Z" fill="#fbfcef" />
      <path d="M5 11 L19 19 L33 11 M19 19 V35" />
      <circle cx="26" cy="27" r="7" fill="#5b43d6" stroke="#fbfcef" />
      <path d="M22.5 27 l2.6 2.8 l4.4 -5.6" stroke="#fbfcef" strokeWidth="2" strokeLinecap="round" />
    </svg>
  );
}
export const NAME = "TrustSeal"; // one constant, easy to rename
export const Brand = () => <div className="ts-brand"><SealMark /><span>{NAME}</span></div>;
