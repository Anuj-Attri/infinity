"""Internal 'App' hand — Nova's gated control over its OWN app.

Like mcp_shell.py this is an in-process MCP-shaped server (transport
"internal"), so its tools ride the normal registry → prompt addendum →
consent gate pipeline. classify_risk hardcodes internal tools to "gated",
and mcp_call never remembers "Always" — so every one of these actions is a
one-tap human approval, every time, by construction.

EXECUTION MODEL (important): these tools change CLIENT state (the chat
model picker, the TTS toggle, which panel is open). The backend cannot
reach into the phone's UI, so call() here is the VALIDATE + AUDIT + ACK
leg: the frontend routes the approved call through /mcp/call (which lands
here and writes the audit line via mcp_client.call_tool), then applies the
actual state change locally and reports the real outcome back to Nova.
Nothing is applied client-side unless this ack succeeds (fail-closed).
"""

from __future__ import annotations

from typing import Any

# Panels the client knows how to surface. Mobile opens them as inline
# sheets/deep-dives (no tile canvas there); desktop spawns the real tile.
PANELS = ["usage", "status", "model", "weather", "portfolio", "news",
          "tasks", "notes", "vault", "chat",
          # First-class on mobile too (v0.1.11): indexed-docs list, the
          # blueprint.am hardware-design surface, and the RAG-brain graph.
          "docs", "blueprint", "brain"]


def workflow_commands() -> list[str]:
    """The app's built-in pipelines, from the REAL registries: generative
    kinds come from main.MEDIA (image/audio today); research + summarize are
    dedicated routes. Lazy import — main imports mcp_client imports us."""
    cmds = ["research", "summarize"]
    try:
        from .main import MEDIA  # noqa: PLC0415
        cmds = sorted(MEDIA.keys()) + cmds
    except Exception:
        cmds = ["image", "audio"] + cmds
    return cmds


def get_tools() -> list[dict[str, Any]]:
    return [
        {
            "name": "set_model",
            "description": (
                "Switch the main chat model (the same setting as the model "
                "picker). Pass a model id or a fuzzy name like 'fable 5' — "
                "the app resolves it against the live catalog."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {"model": {"type": "string", "description": "Model id or name fragment."}},
                "required": ["model"],
            },
            "annotations": {"readOnlyHint": False, "title": "Switch chat model"},
        },
        {
            "name": "toggle_voice_output",
            "description": (
                "Turn Nova's SPOKEN replies (TTS) on or off. Output only — "
                "never touches the microphone or push-to-talk."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {"on": {"type": "boolean"}},
                "required": ["on"],
            },
            "annotations": {"readOnlyHint": False, "title": "Toggle voice replies"},
        },
        {
            "name": "run_workflow",
            "description": (
                "Run one of the app's built-in workflows from natural language: "
                + ", ".join(workflow_commands())
                + ". Exactly what the user's typed /commands do — image/audio "
                "generate media, research does cited web research, summarize "
                "synthesizes the open tiles. These SPEND (OpenRouter), so they "
                "are always approved per-call."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "enum": workflow_commands()},
                    "prompt": {"type": "string", "description": "What to generate/research/summarize."},
                },
                "required": ["command", "prompt"],
            },
            "annotations": {"readOnlyHint": False, "title": "Run a workflow"},
        },
        {
            "name": "show_panel",
            "description": (
                "Open one of the app's panels for the user: "
                + ", ".join(PANELS)
                + ". On the phone this opens an inline sheet (there is no "
                "tile canvas); on desktop it opens the tile."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {"name": {"type": "string", "enum": PANELS}},
                "required": ["name"],
            },
            "annotations": {"readOnlyHint": False, "title": "Open a panel"},
        },
    ]


async def call(name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Validate + ack. The frontend applies the state after this returns ok
    (the audit line is written by mcp_client.call_tool around this call)."""
    args = args or {}
    if name == "set_model":
        model = str(args.get("model") or "").strip()
        if not model:
            return {"content": [{"type": "text", "text": "missing model"}], "isError": True}
        return {"content": [{"type": "text", "text": f"ack set_model({model}) — applying client-side"}]}
    if name == "toggle_voice_output":
        on = bool(args.get("on"))
        return {"content": [{"type": "text", "text": f"ack toggle_voice_output({'on' if on else 'off'}) — applying client-side"}]}
    if name == "run_workflow":
        cmd = str(args.get("command") or "").strip().lower()
        prompt = str(args.get("prompt") or "").strip()
        if cmd not in workflow_commands():
            return {"content": [{"type": "text", "text": f"unknown workflow: {cmd} (valid: {', '.join(workflow_commands())})"}], "isError": True}
        if not prompt:
            return {"content": [{"type": "text", "text": "missing prompt"}], "isError": True}
        return {"content": [{"type": "text", "text": f"ack run_workflow(/{cmd}) — dispatching client-side"}]}
    if name == "show_panel":
        # Models drift between {"name": ...} and {"panel": ...} — accept both.
        panel = str(args.get("name") or args.get("panel") or "").strip().lower()
        if panel not in PANELS:
            return {"content": [{"type": "text", "text": f"unknown panel: {panel} (valid: {', '.join(PANELS)})"}], "isError": True}
        return {"content": [{"type": "text", "text": f"ack show_panel({panel}) — opening client-side"}]}
    return {"content": [{"type": "text", "text": f"unknown tool: {name}"}], "isError": True}
