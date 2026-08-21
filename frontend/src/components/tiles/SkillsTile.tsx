// Skills tile — the node list of what Nova knows how to do.
// One row per SKILL.md folder: name, trigger chips, fired count, toggle.
// Edit the files on disk; ↻ re-reads them (mtime does it automatically too).

import { useCallback, useEffect, useState } from "react";
import TileGlyph from "../TileGlyph";
import { getSkills, toggleSkill, reindexSkills, type SkillInfo } from "../../api";

export default function SkillsTile() {
  const [items, setItems] = useState<SkillInfo[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const { items } = await getSkills();
      setItems(items);
      setErr(null);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => { void refresh(); }, [refresh]);

  const onToggle = useCallback(async (s: SkillInfo) => {
    try {
      const { items } = await toggleSkill(s.name, !s.enabled);
      setItems(items);
    } catch { /* keep old state visible */ }
  }, []);

  return (
    <div className="tile skills-tile">
      <div className="tile-head">
        <span className="tile-title"><TileGlyph />Skills</span>
        <span className="vault-metrics">{items.filter((s) => s.enabled).length}/{items.length} armed</span>
      </div>
      <div className="tile-body skills-body no-drag" onMouseDown={(e) => e.stopPropagation()}>
        <div className="skills-bar">
          <span className="skills-hint">skills/&lt;name&gt;/SKILL.md — 2 max inject per turn</span>
          <button className="vault-act" disabled={busy} title="Reindex"
                  onClick={() => { setBusy(true); reindexSkills().finally(() => { setBusy(false); void refresh(); }); }}>↻</button>
        </div>
        {err && <div className="vault-err">{err}</div>}
        {items.length === 0 && !err && (
          <div className="vault-hint">No skills found — add a folder under skills/ with a SKILL.md.</div>
        )}
        {items.map((s) => (
          <div key={s.name} className={`skill-row ${s.enabled ? "" : "skill-off"}`}>
            <div className="skill-main">
              <div className="skill-name">
                {s.name}
                {s.fired > 0 && <span className="skill-fired" title="times fired">×{s.fired}</span>}
              </div>
              <div className="skill-desc">{s.description}</div>
              {s.triggers.length > 0 && (
                <div className="skill-triggers">
                  {s.triggers.map((t) => <span key={t} className="skill-chip">{t}</span>)}
                </div>
              )}
            </div>
            <button className={`skill-toggle ${s.enabled ? "on" : ""}`}
                    onClick={() => void onToggle(s)}
                    title={s.enabled ? "Disarm" : "Arm"}>
              {s.enabled ? "on" : "off"}
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}
