// Infinity orb — light mode, monochrome ink + single-accent particles.
// Three orbital rings as dark hairlines; particles pick from accent
// stops at different phases so they read as one threaded fiber.
//
// Optional `trail` prop adds a subtle additive layer that occasionally
// emits a violet particle with a short trail — used on the fixed bubble's
// orb to read as gently alive. Does NOT touch the orbital structure.

import { useEffect, useState, type CSSProperties } from "react";

type Props = {
  size?: number;
  breath?: string;
  spin?: string;
  state?: "idle" | "thinking" | "speaking";
  trail?: boolean;
};

type Spark = { id: number; angle: number; path: string };

// Build a short wavy SVG path. Starts at (0,0), extends right ~22–32px,
// undulates around y=0 with a sine wave at a random phase + amplitude.
// Stays in the SVG viewBox so the squiggle reads at all angles.
function makeSquigglePath(): string {
  const len = 22 + Math.random() * 10;
  const segs = 6;
  const amp = 1.6 + Math.random() * 1.4;
  const phase = Math.random() * Math.PI * 2;
  const freq = 1.1 + Math.random() * 0.6;
  let d = "M 0 0";
  for (let i = 1; i <= segs; i++) {
    const x = (i / segs) * len;
    const y = Math.sin(i * freq + phase) * amp;
    d += ` L ${x.toFixed(1)} ${y.toFixed(1)}`;
  }
  return d;
}

export default function Orb({
  size = 64,
  breath = "4.4s",
  spin = "14s",
  state = "idle",
  trail = false,
}: Props) {
  const baseSpin = parseFloat(spin) || 14;
  const style = {
    "--orb-size": `${size}px`,
    "--orb-breath": state === "thinking" ? "2.2s" : breath,
    "--orb-spin-1": `${baseSpin * 0.55}s`,
    "--orb-spin-2": `${baseSpin * 0.85}s`,
    "--orb-spin-3": `${baseSpin * 1.25}s`,
    "--orb-spin-4": `${baseSpin * 1.6}s`,
    "--orb-spin-5": `${baseSpin * 2.1}s`,
  } as CSSProperties;

  const [sparks, setSparks] = useState<Spark[]>([]);

  useEffect(() => {
    if (!trail) return;
    let nextId = 1;
    let cancelled = false;
    let timer: number | undefined;

    const emit = () => {
      if (cancelled) return;
      // 1, 2, or 3 sparks per emission — randomised.
      const count = 1 + Math.floor(Math.random() * 3);
      const batch: Spark[] = [];
      // Pick angles spread around the orb so multi-spark emissions don't
      // pile on top of each other (random offset around evenly-spaced anchors).
      const anchor = Math.random() * 360;
      for (let i = 0; i < count; i++) {
        const id = nextId++;
        const spread = 360 / count;
        const angle = anchor + i * spread + (Math.random() - 0.5) * (spread * 0.45);
        batch.push({ id, angle, path: makeSquigglePath() });
      }
      setSparks((prev) => [...prev, ...batch]);
      // Cull after the fade animation finishes.
      window.setTimeout(() => {
        setSparks((prev) => prev.filter((s) => !batch.some((b) => b.id === s.id)));
      }, 1700);

      // Irregular cadence: 2.8 – 5.0s. Occasional, not constant.
      timer = window.setTimeout(emit, 2800 + Math.random() * 2200);
    };

    // Stagger the first emission so multiple orbs on screen don't sync.
    timer = window.setTimeout(emit, 1400 + Math.random() * 1800);

    return () => {
      cancelled = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [trail]);

  const cls = ["lorb", size <= 22 ? "mini" : ""].filter(Boolean).join(" ");

  return (
    <div className={cls} style={style} data-state={state}>
      <div className="lorb-volume">
        <div className="lorb-core" />
        <div className="lorb-orbit o1"><div className="lorb-particle" /><div className="lorb-particle alt" /></div>
        <div className="lorb-orbit o2"><div className="lorb-particle" /><div className="lorb-particle alt" /></div>
        <div className="lorb-orbit o3"><div className="lorb-particle" /></div>
        <div className="lorb-orbit o4"><div className="lorb-particle" /></div>
        <div className="lorb-orbit o5"><div className="lorb-particle" /></div>
      </div>
      {trail && (
        <div className="lorb-trail" aria-hidden="true">
          {sparks.map((s) => (
            <span
              key={s.id}
              className="lorb-spark"
              style={{ "--angle": `${s.angle}deg` } as CSSProperties}
            >
              <svg viewBox="0 -6 36 12" width="36" height="12" overflow="visible">
                <path d={s.path} />
              </svg>
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
