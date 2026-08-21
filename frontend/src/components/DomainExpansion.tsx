// Domain expansion — landing → whiteboard transition.
// Streaks rush outward from a screen-center origin: ~130 ink streaks plus a
// few accent "fibers." ~900ms. Triggers between landing fade-out and
// dashboard fade-in.

import { useEffect, useMemo, type CSSProperties } from "react";

type Props = {
  duration?: number;
  onComplete?: () => void;
};

export default function DomainExpansion({ duration = 900, onComplete }: Props) {
  const streaks = useMemo(() => {
    const COUNT = 130;
    return Array.from({ length: COUNT }, (_, i) => {
      const angle = (i / COUNT) * 360 + (Math.random() - 0.5) * 2.4;
      const width = 0.5 + Math.random() * 1.2;
      const length = 60 + Math.random() * 60;
      const delay = Math.random() * 140;
      const spectral = Math.random() < 0.12;
      return {
        angle,
        width: spectral ? width + 0.4 : width,
        length,
        delay,
        spectral,
      };
    });
  }, []);

  useEffect(() => {
    if (!onComplete) return;
    const t = window.setTimeout(onComplete, duration);
    return () => window.clearTimeout(t);
  }, [duration, onComplete]);

  return (
    <div className="expansion" aria-hidden="true">
      <div className="expansion-streaks">
        {streaks.map((s, i) => {
          const style = {
            "--angle": `${s.angle}deg`,
            "--width": `${s.width}px`,
            "--length": `${s.length}vmax`,
            "--delay": `${s.delay}ms`,
          } as CSSProperties;
          return (
            <div
              key={i}
              className={`expansion-streak ${s.spectral ? "spectral" : ""}`}
              style={style}
            />
          );
        })}
      </div>
      <div className="expansion-whiteout" />
    </div>
  );
}
