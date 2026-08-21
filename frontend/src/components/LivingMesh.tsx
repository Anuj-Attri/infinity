// Living mesh — a canvas-based grid of ink dots that crest near the cursor.
// Slow ambient breath when the cursor is away. Sits behind the whiteboard.
//
// Aliveness layers — all SELF-GENERATED (no feeds, no APIs). The field is
// alive because of the user and the app, not the internet:
//   · Breath     — sine swell, always on. Slows a touch at night.
//   · Weather    — slow drifting bright/dark regions, cycling over minutes.
//                  The field has its own weather; it is never the same twice.
//   · Heartbeat  — while the model generates ("infinity:activity"), the
//                  breath quickens and deepens. The app visibly thinks.
//   · Wake       — the cursor leaves a decaying liquid trail proportional
//                  to its speed. Dragging a tile carves a wake for free.
//   · Meniscus   — a faint ring of light where each glass tile rests on
//                  the field, like a pane sitting on water.
//   · Shiver     — typing anywhere raises the field's energy slightly.
//   · Ripples    — discrete events ("infinity:pulse") expand as one soft
//                  ring and fade.
//   · Drifter    — every few minutes a faint crest crosses the canvas on
//                  its own. Rare enough to be a surprise.

import { useEffect, useRef } from "react";

type Props = {
  spacing?: number;
  baseOpacity?: number;
  baseSize?: number;
  influenceRadius?: number;
};

type Ripple = { x: number; y: number; born: number; strength: number; tint: number };
type Drifter = { x: number; y: number; vx: number; vy: number } | null;
type TrailPt = { x: number; y: number; t: number; s: number };
type Rect4 = { l: number; t: number; r: number; b: number };

export default function LivingMesh({
  spacing = 28,
  baseOpacity = 0.10,
  baseSize = 1.0,
  influenceRadius = 220,
}: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const cursorRef = useRef({ x: -9999, y: -9999, active: false });

  useEffect(() => {
    const canvas = canvasRef.current;
    const wrap = wrapRef.current;
    if (!canvas || !wrap) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const dpr = Math.max(1, Math.min(2, window.devicePixelRatio || 1));
    let raf = 0;
    let stopped = false;

    const resize = () => {
      const rect = wrap.getBoundingClientRect();
      canvas.width = Math.max(1, Math.floor(rect.width * dpr));
      canvas.height = Math.max(1, Math.floor(rect.height * dpr));
      canvas.style.width = `${rect.width}px`;
      canvas.style.height = `${rect.height}px`;
    };
    resize();
    const ro = new ResizeObserver(resize);
    ro.observe(wrap);

    // ── Cursor + wake trail ────────────────────────────────────────
    const trail: TrailPt[] = [];
    let lastTrail = { x: -9999, y: -9999, t: 0 };
    const onMove = (e: MouseEvent) => {
      const rect = wrap.getBoundingClientRect();
      const x = e.clientX - rect.left;
      const y = e.clientY - rect.top;
      cursorRef.current.x = x;
      cursorRef.current.y = y;
      cursorRef.current.active = true;
      // Sample the trail when the cursor has moved a real distance; the
      // point's strength scales with speed, so slow mousing leaves almost
      // nothing and a flick leaves a visible wake.
      const now = performance.now();
      const dx = x - lastTrail.x, dy = y - lastTrail.y;
      const d = Math.hypot(dx, dy);
      if (d > 14) {
        const dtms = Math.max(8, now - lastTrail.t);
        const speed = d / dtms; // px per ms
        lastTrail = { x, y, t: now };
        const s = Math.min(1, speed / 1.6);
        if (s > 0.12) {
          if (trail.length >= 22) trail.shift();
          trail.push({ x, y, t: now, s });
        }
      }
    };
    const onLeave = () => {
      cursorRef.current.active = false;
      cursorRef.current.x = -9999;
      cursorRef.current.y = -9999;
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseleave", onLeave);

    // ── Typing shiver ──────────────────────────────────────────────
    let energy = 0;
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      const typing =
        !!t &&
        (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable);
      if (typing) energy = Math.min(1, energy + 0.10);
    };
    window.addEventListener("keydown", onKey, true);

    // ── Pointer-down tracking (tile drags need per-frame rect refresh) ─
    let pointerDown = false;
    const onDown = () => { pointerDown = true; };
    const onUp = () => { pointerDown = false; };
    window.addEventListener("pointerdown", onDown, true);
    window.addEventListener("pointerup", onUp, true);

    // Resolve the accent for spectral crest tinting (read once; we don't
    // animate accent here so a static read is fine).
    const cs = getComputedStyle(document.documentElement);
    const parseHex = (h: string): [number, number, number] => {
      const s = h.trim().replace(/^#/, "");
      if (s.length !== 6) return [60, 47, 122];
      return [
        parseInt(s.slice(0, 2), 16),
        parseInt(s.slice(2, 4), 16),
        parseInt(s.slice(4, 6), 16),
      ];
    };
    const accent = parseHex(cs.getPropertyValue("--accent") || "#3d2f7a");

    // ── App-event aliveness state ──────────────────────────────────
    let activityTarget = 0;   // 1 while the model is generating
    let activity = 0;         // smoothed toward target each frame
    const ripples: Ripple[] = [];
    let drifter: Drifter = null;
    let nextDrifterAt = performance.now() + (60_000 + Math.random() * 150_000);

    const onActivity = (e: Event) => {
      const d = (e as CustomEvent).detail;
      activityTarget = d?.active ? 1 : 0;
    };
    const onPulse = (e: Event) => {
      const kind = (e as CustomEvent).detail?.kind as string | undefined;
      const rect = wrap.getBoundingClientRect();
      // Soft cap so a burst of events can't turn the mesh into a pond.
      if (ripples.length >= 4) ripples.shift();
      ripples.push({
        x: rect.width * (0.2 + Math.random() * 0.6),
        y: rect.height * (0.2 + Math.random() * 0.6),
        born: performance.now(),
        strength: kind === "subagent" ? 1.0 : 0.65,
        tint: kind === "subagent" ? 0.6 : 0.35,
      });
    };
    window.addEventListener("infinity:activity", onActivity);
    window.addEventListener("infinity:pulse", onPulse);

    const spawnDrifter = (W: number, H: number) => {
      const fromLeft = Math.random() < 0.5;
      const speed = 90 + Math.random() * 60; // px/s
      const angle = (Math.random() - 0.5) * 0.9; // mostly horizontal
      drifter = {
        x: fromLeft ? -120 : W + 120,
        y: H * (0.15 + Math.random() * 0.7),
        vx: (fromLeft ? 1 : -1) * speed * Math.cos(angle),
        vy: speed * Math.sin(angle),
      };
    };

    // ── Tile meniscus: cached tile rects in mesh coordinates ───────
    let tileRects: Rect4[] = [];
    let rectFrame = 0;
    const refreshTileRects = () => {
      const wrapRect = wrap.getBoundingClientRect();
      const els = document.querySelectorAll<HTMLElement>(".tile");
      const next: Rect4[] = [];
      els.forEach((el) => {
        const r = el.getBoundingClientRect();
        if (r.width < 2 || r.height < 2) return;
        next.push({
          l: r.left - wrapRect.left,
          t: r.top - wrapRect.top,
          r: r.right - wrapRect.left,
          b: r.bottom - wrapRect.top,
        });
      });
      tileRects = next;
    };
    refreshTileRects();

    let clock = 0;            // breath phase accumulator (speed-aware)
    let last = performance.now();

    const draw = () => {
      raf = 0;
      if (stopped) return;
      const now = performance.now();
      const dt = Math.min(0.05, (now - last) / 1000);
      last = now;

      const W = canvas.width / dpr;
      const H = canvas.height / dpr;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, W, H);

      // Time-of-day mood: nightness peaks ~2am, fades by midday.
      const dNow = new Date();
      const hour = dNow.getHours() + dNow.getMinutes() / 60;
      const nightness = 0.5 * (1 + Math.cos(((hour - 2) / 24) * Math.PI * 2));

      // Heartbeat + typing energy modulate the breath.
      activity += (activityTarget - activity) * Math.min(1, dt * 2.5);
      energy *= Math.max(0, 1 - dt * 1.1); // typing energy decays in ~1s
      const breathSpeed =
        0.4 * (1 - 0.25 * nightness) * (1 + 1.1 * activity + 0.4 * energy);
      clock += dt * breathSpeed;
      const breathDepth = 1 + 0.7 * activity + 0.3 * energy;

      // Internal weather clock — full character change over ~7 minutes.
      const wT = now / 70_000;

      // Tile rects: every frame while dragging, every ~12 frames at rest.
      rectFrame++;
      if (pointerDown || rectFrame % 12 === 0) refreshTileRects();

      // Advance drifter / schedule the next one.
      if (drifter) {
        drifter.x += drifter.vx * dt;
        drifter.y += drifter.vy * dt;
        if (drifter.x < -160 || drifter.x > W + 160 || drifter.y < -160 || drifter.y > H + 160) {
          drifter = null;
          nextDrifterAt = now + (90_000 + Math.random() * 150_000);
        }
      } else if (now >= nextDrifterAt) {
        spawnDrifter(W, H);
      }

      // Cull dead ripples (3s) and trail points (700ms).
      for (let i = ripples.length - 1; i >= 0; i--) {
        if (now - ripples[i].born > 3000) ripples.splice(i, 1);
      }
      while (trail.length && now - trail[0].t > 700) trail.shift();

      const cur = cursorRef.current;
      const radius = influenceRadius;
      const dark = document.documentElement.dataset.theme === "dark";
      const baseR = dark ? 232 : 16, baseG = dark ? 230 : 14, baseB = dark ? 227 : 20;
      // Night bias: nudge the base ink a touch toward the accent. Keep it
      // a whisper — the field should read black-with-violet-memory, not purple.
      const nb = nightness * 0.035;
      const inkR = baseR * (1 - nb) + accent[0] * nb;
      const inkG = baseG * (1 - nb) + accent[1] * nb;
      const inkB = baseB * (1 - nb) + accent[2] * nb;

      for (let gy = spacing * 0.5; gy < H; gy += spacing) {
        for (let gx = spacing * 0.5; gx < W; gx += spacing) {
          const phase = Math.sin(clock + gx * 0.012 + gy * 0.009);

          // Weather: two overlapping low-frequency waves drifting at
          // different speeds — bright and dim regions wander the field.
          const weather =
            Math.sin(gx * 0.004 + wT) * Math.sin(gy * 0.0035 - wT * 0.7) +
            0.5 * Math.sin((gx + gy) * 0.002 + wT * 0.43);

          let size = baseSize + phase * 0.08 * breathDepth + weather * 0.05;
          let alpha = baseOpacity + phase * 0.03 * breathDepth + weather * 0.022;
          let crest = 0;
          let lift = 0;

          if (cur.active) {
            const dx = gx - cur.x;
            const dy = gy - cur.y;
            const dist = Math.hypot(dx, dy);
            if (dist < radius) {
              const u = 1 - dist / radius;
              const e = u * u * (3 - 2 * u);
              size += e * 3.0;
              alpha += e * 0.55;
              crest = Math.max(crest, e * 0.7);
              lift = e * -3.0;
            }
          }

          // Wake: decaying crests along the cursor's recent path.
          for (let i = 0; i < trail.length; i++) {
            const tp = trail[i];
            const ddx = gx - tp.x;
            if (ddx > 130 || ddx < -130) continue;
            const ddy = gy - tp.y;
            if (ddy > 130 || ddy < -130) continue;
            const dist = Math.hypot(ddx, ddy);
            if (dist < 130) {
              const fade = 1 - (now - tp.t) / 700;
              const u = 1 - dist / 130;
              const e = u * u * (3 - 2 * u) * tp.s * fade * 0.55;
              size += e * 2.2;
              alpha += e * 0.38;
              crest = Math.max(crest, e * 0.5);
              lift += e * -1.6;
            }
          }

          // Meniscus: a soft ring of light hugging each glass tile's edge.
          for (let i = 0; i < tileRects.length; i++) {
            const rc = tileRects[i];
            // Signed distance OUTSIDE the rect; dots under the tile skip.
            const ox = gx < rc.l ? rc.l - gx : gx > rc.r ? gx - rc.r : 0;
            const oy = gy < rc.t ? rc.t - gy : gy > rc.b ? gy - rc.b : 0;
            if (ox === 0 && oy === 0) continue; // under the pane
            if (ox > 26 || oy > 26) continue;
            const d = Math.hypot(ox, oy);
            if (d < 26) {
              const e = 1 - d / 26;
              alpha += e * 0.10;
              size += e * 0.4;
              crest = Math.max(crest, e * 0.12);
            }
          }

          // Drifter crest — a smaller, fainter cursor that isn't yours.
          if (drifter) {
            const dx = gx - drifter.x;
            const dy = gy - drifter.y;
            const dist = Math.hypot(dx, dy);
            const dr = 150;
            if (dist < dr) {
              const u = 1 - dist / dr;
              const e = u * u * (3 - 2 * u) * 0.45;
              size += e * 2.6;
              alpha += e * 0.4;
              crest = Math.max(crest, e * 0.8);
              lift += e * -2.0;
            }
          }

          // Ripples — a single expanding ring per event, fading with age.
          for (let i = 0; i < ripples.length; i++) {
            const rp = ripples[i];
            const age = (now - rp.born) / 1000;        // 0..3
            const R = age * 300;                        // ring radius
            const band = 80;
            const d = Math.abs(Math.hypot(gx - rp.x, gy - rp.y) - R);
            if (d < band) {
              const fade = Math.max(0, 1 - age / 3);
              const u = 1 - d / band;
              const e = u * u * (3 - 2 * u) * rp.strength * fade * 0.5;
              size += e * 2.4;
              alpha += e * 0.45;
              crest = Math.max(crest, e * rp.tint * 2);
              lift += e * -1.5;
            }
          }

          if (crest > 1) crest = 1;
          if (size < 0.2) size = 0.2;
          const r = inkR * (1 - crest) + accent[0] * crest;
          const g = inkG * (1 - crest) + accent[1] * crest;
          const b = inkB * (1 - crest) + accent[2] * crest;

          ctx.beginPath();
          ctx.arc(gx, gy + lift, size, 0, Math.PI * 2);
          ctx.fillStyle = `rgba(${r | 0}, ${g | 0}, ${b | 0}, ${alpha})`;
          ctx.fill();
        }
      }

      raf = requestAnimationFrame(draw);
    };
    raf = requestAnimationFrame(draw);

    return () => {
      stopped = true;
      if (raf) cancelAnimationFrame(raf);
      ro.disconnect();
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseleave", onLeave);
      window.removeEventListener("keydown", onKey, true);
      window.removeEventListener("pointerdown", onDown, true);
      window.removeEventListener("pointerup", onUp, true);
      window.removeEventListener("infinity:activity", onActivity);
      window.removeEventListener("infinity:pulse", onPulse);
    };
  }, [spacing, baseOpacity, baseSize, influenceRadius]);

  return (
    <div className="mesh-wrap" ref={wrapRef} aria-hidden="true">
      <canvas ref={canvasRef} className="mesh-canvas" />
    </div>
  );
}
