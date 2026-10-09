/**
 * Paper grain: fixed, behind content (z-0), ~2% opacity, dark noise on the porcelain canvas.
 * Mount ONCE in the shell. White cards sit above it, so only the canvas reads as paper.
 */
export function Grain() {
  return <div aria-hidden className="grain" />;
}

/** Grain inside a positioned element (parent needs `relative overflow-hidden`). Light noise inside .inset-night. */
export function GrainLayer({ className = "" }: { className?: string }) {
  return <div aria-hidden className={`grain-layer ${className}`} />;
}
