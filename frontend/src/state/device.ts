// Device-class detection for the responsive web pass.
//
// Ported from Mark 3's mobile/useIsMobile.ts with the Capacitor branch removed:
// Mark 4 is a web app, so "is this a phone" is a pure viewport-and-pointer
// question. The dedicated phone shell (MobileShell / VoiceRemote) is deferred,
// so nothing here switches layouts wholesale — it only lets individual
// components adapt.
//
//   isMobileRuntime()  — true on a genuinely small, touch-first viewport.
//   useDeviceMode()    — reactive { isMobile, isTablet, orientation }.
//
// Tablet vs phone: a tablet is a large touch screen (min edge >= 600px), which
// can carry the desktop-style free canvas; a phone cannot.

import { useEffect, useState } from "react";

const PHONE_MAX = 820;     // px — at/under this width we treat as a phone-class layout
const TABLET_MIN_EDGE = 600; // px — min(w,h) at/over this on touch ⇒ tablet

function coarsePointer(): boolean {
  try { return window.matchMedia("(pointer: coarse)").matches; } catch { return false; }
}

// Dev/preview override: ?phone=1 forces the phone layer, ?phone=0 forces it
// off — lets a desktop browser preview the voice remote (mouse ≠ coarse
// pointer, so it would otherwise never mount off-device).
function phoneOverride(): boolean | null {
  try {
    const v = new URLSearchParams(window.location.search).get("phone");
    if (v === "1") return true;
    if (v === "0") return false;
  } catch { /* ignore */ }
  return null;
}

/** True on a genuinely small, touch-first viewport. Desktop browsers stay on
 *  the core path. */
export function isMobileRuntime(): boolean {
  const ov = phoneOverride();
  if (ov !== null) return ov;
  if (typeof window === "undefined") return false;
  return window.innerWidth <= PHONE_MAX && coarsePointer();
}

export type Orientation = "portrait" | "landscape";
export type DeviceMode = {
  isMobile: boolean;
  isTablet: boolean;
  orientation: Orientation;
};

function read(): DeviceMode {
  if (typeof window === "undefined") {
    return { isMobile: false, isTablet: false, orientation: "portrait" };
  }
  const w = window.innerWidth;
  const h = window.innerHeight;
  const ov = phoneOverride();
  const isMobile = ov !== null ? ov : (w <= PHONE_MAX && coarsePointer());
  if (ov === true) {
    // Forced phone preview: never classify as tablet, whatever the window size.
    return { isMobile: true, isTablet: false, orientation: w >= h ? "landscape" : "portrait" };
  }
  // Tablet = a roomy touch surface. Use the shorter edge so rotation does not
  // flip phone <-> tablet mid-session.
  const minEdge = Math.min(w, h);
  const isTablet = isMobile && minEdge >= TABLET_MIN_EDGE;
  const orientation: Orientation = w >= h ? "landscape" : "portrait";
  return { isMobile, isTablet, orientation };
}

/** Reactive device mode — re-reads on resize / orientation change. */
export function useDeviceMode(): DeviceMode {
  const [mode, setMode] = useState<DeviceMode>(() => read());
  useEffect(() => {
    let raf = 0;
    const onChange = () => {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(() => setMode(read()));
    };
    window.addEventListener("resize", onChange);
    window.addEventListener("orientationchange", onChange);
    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener("resize", onChange);
      window.removeEventListener("orientationchange", onChange);
    };
  }, []);
  return mode;
}
