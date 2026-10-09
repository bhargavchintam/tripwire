import { useEffect, useRef } from "react";

/**
 * Decorative night sky for the ONE dark panel (.inset-night). By default it fills its positioned
 * parent (absolute inset-0) and sizes itself with a ResizeObserver; `fixed` restores the old
 * full-viewport canvas (don't: the page is light now).
 * - pointer-events: none, aria-hidden; never encodes data (neutral white/cream, low alpha, no drift
 *   by default so nothing reads as moving agents).
 * - devicePixelRatio aware (capped at 2). Pauses while hidden/off-screen; one static frame under
 *   prefers-reduced-motion. Throttled to ~24 fps.
 */
export function Starfield({
  count = 70,
  drift = 0,
  fixed = false,
  className = "",
}: {
  count?: number;
  /** Sideways drift in px/s (default 0 = still sky, twinkle only). */
  drift?: number;
  fixed?: boolean;
  className?: string;
}) {
  const ref = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = ref.current;
    const ctx = canvas?.getContext("2d");
    if (!canvas || !ctx) return;

    // Deterministic pseudo-random so the sky doesn't reshuffle on every mount.
    let seed = 1337;
    const rnd = () => {
      seed = (seed * 16807) % 2147483647;
      return (seed - 1) / 2147483646;
    };
    const stars = Array.from({ length: count }, () => {
      const big = rnd() > 0.92;
      return {
        x: rnd(),
        y: rnd(),
        r: big ? 0.8 + rnd() * 0.5 : 0.3 + rnd() * 0.45,
        a: big ? 0.4 + rnd() * 0.25 : 0.12 + rnd() * 0.3,
        speed: 0.2 + rnd() * 0.6, // rad/s
        phase: rnd() * Math.PI * 2,
        warm: rnd() > 0.8,
      };
    });

    let w = 0;
    let h = 0;
    const resize = () => {
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      if (fixed) {
        w = window.innerWidth;
        h = window.innerHeight;
      } else {
        const box = (canvas.parentElement ?? canvas).getBoundingClientRect();
        w = Math.max(1, Math.round(box.width));
        h = Math.max(1, Math.round(box.height));
      }
      canvas.width = Math.round(w * dpr);
      canvas.height = Math.round(h * dpr);
      canvas.style.width = `${w}px`;
      canvas.style.height = `${h}px`;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    };

    const draw = (tSec: number) => {
      ctx.clearRect(0, 0, w, h);
      for (const s of stars) {
        const x = (((s.x * w - tSec * drift * s.r) % w) + w) % w;
        const y = s.y * h;
        const tw = 0.6 + 0.4 * Math.sin(tSec * s.speed + s.phase);
        const alpha = s.a * tw;
        const rgb = s.warm ? "255 238 218" : "226 231 255";
        ctx.fillStyle = `rgb(${rgb} / ${alpha})`;
        ctx.beginPath();
        ctx.arc(x, y, s.r, 0, Math.PI * 2);
        ctx.fill();
      }
    };

    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)");
    let raf = 0;
    let last = 0;
    let visible = true;
    const start = performance.now();
    const frame = (now: number) => {
      raf = requestAnimationFrame(frame);
      if (now - last < 42) return;
      last = now;
      draw((now - start) / 1000);
    };
    const stop = () => {
      cancelAnimationFrame(raf);
      raf = 0;
    };
    const play = () => {
      stop();
      if (reduce.matches) draw(0);
      else if (!document.hidden && visible) raf = requestAnimationFrame(frame);
    };
    const redraw = () => {
      resize();
      draw(reduce.matches ? 0 : (performance.now() - start) / 1000);
    };
    const onVisibility = () => (document.hidden ? stop() : play());

    resize();
    play();
    let ro: ResizeObserver | undefined;
    let io: IntersectionObserver | undefined;
    if (fixed) window.addEventListener("resize", redraw);
    else if (canvas.parentElement) {
      ro = new ResizeObserver(redraw);
      ro.observe(canvas.parentElement);
      io = new IntersectionObserver(([entry]) => {
        visible = !!entry?.isIntersecting;
        if (visible) play();
        else stop();
      });
      io.observe(canvas.parentElement);
    }
    document.addEventListener("visibilitychange", onVisibility);
    reduce.addEventListener("change", play);
    return () => {
      stop();
      ro?.disconnect();
      io?.disconnect();
      if (fixed) window.removeEventListener("resize", redraw);
      document.removeEventListener("visibilitychange", onVisibility);
      reduce.removeEventListener("change", play);
    };
  }, [count, drift, fixed]);

  return (
    <canvas
      ref={ref}
      aria-hidden
      className={`pointer-events-none ${fixed ? "fixed" : "absolute"} inset-0 -z-10 animate-fade-in ${className}`}
    />
  );
}
