// Gear glyph that opens the Settings panel. Sits in the titlebar area
// to the left of the OS window controls.

type Props = { onClick: () => void };

export default function SettingsButton({ onClick }: Props) {
  return (
    <button
      type="button"
      className="settings-btn"
      onClick={onClick}
      title="Settings"
      aria-label="Settings"
    >
      <svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.2">
        <circle cx="8" cy="8" r="2.4" />
        <path d="M8 1.5 V3.5 M8 12.5 V14.5 M1.5 8 H3.5 M12.5 8 H14.5 M3.5 3.5 L4.9 4.9 M11.1 11.1 L12.5 12.5 M3.5 12.5 L4.9 11.1 M11.1 4.9 L12.5 3.5" />
      </svg>
    </button>
  );
}
