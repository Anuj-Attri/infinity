// Always-on breathing radial pools — the calm landing/canvas presence.
// Three blurred accent-tinted blobs that slowly inhale/exhale across the
// surface. Pure CSS animation; no JS once mounted.

export default function Background() {
  return (
    <div className="landing-bg" aria-hidden="true">
      <div className="landing-bg-pool p1" />
      <div className="landing-bg-pool p2" />
      <div className="landing-bg-pool p3" />
    </div>
  );
}
