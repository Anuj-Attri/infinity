// Todo tile — persistent task list in localStorage. Add, check off, delete.

import { useEffect, useState, type KeyboardEvent } from "react";
import { onTileWrite } from "../../state/tileActions";
import TileGlyph from "../TileGlyph";

type Task = { id: string; text: string; done: boolean; ts: number };

const KEY = "supernova.todo.v1";

function load(): Task[] {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(
      (t) => t && typeof t.id === "string" && typeof t.text === "string"
    );
  } catch { return []; }
}

function save(tasks: Task[]) {
  try { localStorage.setItem(KEY, JSON.stringify(tasks)); } catch { /* quota */ }
}

export default function TodoTile() {
  const [tasks, setTasks] = useState<Task[]>(() => load());
  const [draft, setDraft] = useState<string>("");

  useEffect(() => { save(tasks); }, [tasks]);

  // Reflect external writes (Nova's write_tile) live.
  useEffect(() => onTileWrite("tasks", () => setTasks(load())), []);

  const add = () => {
    const text = draft.trim();
    if (!text) return;
    const t: Task = { id: crypto.randomUUID(), text, done: false, ts: Date.now() };
    setTasks((prev) => [...prev, t]);
    setDraft("");
  };

  const toggle = (id: string) => {
    setTasks((prev) => prev.map((t) => (t.id === id ? { ...t, done: !t.done } : t)));
  };

  const remove = (id: string) => {
    setTasks((prev) => prev.filter((t) => t.id !== id));
  };

  const onKey = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") {
      e.preventDefault();
      add();
    }
  };

  const open   = tasks.filter((t) => !t.done).length;
  const closed = tasks.length - open;

  return (
    <div className="tile">
      <div className="tile-head">
        <span className="tile-title"><TileGlyph /> TASKS</span>
        <span className="tile-title-meta">{open} open · {closed} done</span>
      </div>
      <div
        className="tile-body ttodo-body no-drag"
        onMouseDown={(e) => e.stopPropagation()}
        onTouchStart={(e) => e.stopPropagation()}
      >
        <div className="ttodo-add">
          <input
            className="ttodo-add-input"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={onKey}
            placeholder="Add a task here"
            spellCheck={false}
          />
        </div>
        <ul className="ttodo-list">
          {tasks.length === 0 && <li className="ttodo-empty">nothing yet</li>}
          {tasks.map((t) => (
            <li className={`ttodo-item ${t.done ? "done" : ""}`} key={t.id}>
              <button
                type="button"
                className="ttodo-check"
                onClick={() => toggle(t.id)}
                aria-label={t.done ? "Mark open" : "Mark done"}
              >
                {t.done ? (
                  <svg viewBox="0 0 10 10" width="10" height="10" fill="none" stroke="currentColor" strokeWidth="1.4">
                    <path d="M2 5 L4 7 L8 3" />
                  </svg>
                ) : null}
              </button>
              <span className="ttodo-label" onClick={() => toggle(t.id)}>{t.text}</span>
              <button type="button" className="ttodo-rm" onClick={() => remove(t.id)} title="Delete" aria-label="Delete task">
                <svg viewBox="0 0 10 10" width="9" height="9" fill="none" stroke="currentColor" strokeWidth="1.2">
                  <path d="M2 2 L8 8 M8 2 L2 8" />
                </svg>
              </button>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
