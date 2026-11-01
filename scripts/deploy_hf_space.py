#!/usr/bin/env python3
"""Deploy the app to a Hugging Face Docker Space as a public guest-mode site.

    HF_TOKEN=hf_... OPENROUTER_API_KEY=sk-or-... python scripts/deploy_hf_space.py

GitHub Actions runs this after CI passes on main (.github/workflows/deploy.yml).
It can also be run by hand with the same environment.

Environment:
    HF_TOKEN             required. A Hugging Face token with write access.
    OPENROUTER_API_KEY   required on first deploy; stored as a Space secret.
    VOYAGE_API_KEY       optional; stored as a Space secret.
    HF_SPACE             optional. "owner/name"; default "<your user>/infinity".
    SPEND_CAP_USD        optional monthly model spend limit; default 20.
    LOCAL_MODEL_URL, LOCAL_MODEL   optional local fallback, passed through.

What it sets on the Space: guest sessions on, data in /tmp (Spaces run the
container as a non-root user and give it no persistent disk, which suits guest
mode: guest data is meant to vanish), and proxy headers trusted, because the
Spaces proxy is the only thing that talks to the container.

Exits non-zero if the build fails or the live site is not in guest mode.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from huggingface_hub import HfApi

REPO = Path(__file__).resolve().parents[1]
PORT = 8000

SPACE_README = """---
title: Infinity
emoji: ♾️
colorFrom: indigo
colorTo: purple
sdk: docker
app_port: {port}
pinned: false
license: mit
short_description: An AI workspace that cites its sources.
---

This Space is deployed automatically from
[github.com/Anuj-Attri/infinity](https://github.com/Anuj-Attri/infinity).
Each browser tab gets a private session that is deleted when the tab closes.
"""

# What the image build needs, relative to the repo root. Nothing else ships.
INCLUDE = ["backend", "frontend", ".dockerignore"]
SKIP = {"node_modules", "dist", "__pycache__", ".venv", ".pytest_cache",
        ".mypy_cache", "e2e", ".data", "vault"}


def die(msg: str) -> None:
    print(f"::error::{msg}" if os.environ.get("GITHUB_ACTIONS") else f"ERROR: {msg}")
    sys.exit(1)


def stage() -> Path:
    out = Path(tempfile.mkdtemp(prefix="space-"))
    ignore = shutil.ignore_patterns(*SKIP)
    for name in INCLUDE:
        src = REPO / name
        if src.is_dir():
            shutil.copytree(src, out / name, ignore=ignore)
        elif src.exists():
            shutil.copy2(src, out / name)
    shutil.copy2(REPO / "infra" / "Dockerfile", out / "Dockerfile")
    (out / "README.md").write_text(SPACE_README.format(port=PORT), encoding="utf-8")
    return out


def space_url(space_id: str) -> str:
    return "https://" + space_id.replace("/", "-").replace("_", "-").replace(".", "-").lower() + ".hf.space"


def get_json(url: str) -> tuple[int, dict]:
    req = urllib.request.Request(url, headers={"User-Agent": "infinity-deploy/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, {}
    except Exception:
        return 0, {}


def main() -> None:
    token = os.environ.get("HF_TOKEN", "").strip()
    if not token:
        die("HF_TOKEN is not set. Create a write token at https://huggingface.co/settings/tokens")
    api = HfApi(token=token)
    user = api.whoami()["name"]
    space_id = os.environ.get("HF_SPACE", "").strip() or f"{user}/infinity"
    print(f"deploying to space {space_id}")

    try:
        api.create_repo(space_id, repo_type="space", space_sdk="docker",
                        exist_ok=True, private=False)
    except Exception as e:
        # The status alone ("402 Payment Required") does not say what the
        # account is missing; the body does. Print it before giving up.
        resp = getattr(e, "response", None)
        detail = getattr(resp, "text", "") if resp is not None else ""
        die(f"could not create the Space {space_id}: {e}\nserver said: {detail[:500]}")

    for name in ("OPENROUTER_API_KEY", "VOYAGE_API_KEY"):
        val = os.environ.get(name, "").strip()
        if val:
            api.add_space_secret(space_id, name, val)
            print(f"  secret {name}: set")
    variables = {
        "GUEST_SESSIONS": "1",
        "GUEST_IDLE_MINUTES": os.environ.get("GUEST_IDLE_MINUTES", "30"),
        "SPEND_CAP_USD": os.environ.get("SPEND_CAP_USD", "").strip() or "20",
        "DATA_DIR": "/tmp/infinity-data",
        "TRUST_PROXY_HEADERS": "1",
    }
    for name in ("LOCAL_MODEL_URL", "LOCAL_MODEL"):
        if os.environ.get(name, "").strip():
            variables[name] = os.environ[name].strip()
    for k, v in variables.items():
        api.add_space_variable(space_id, k, v)
        print(f"  variable {k}={v}")

    folder = stage()
    commit = api.upload_folder(folder_path=str(folder), repo_id=space_id, repo_type="space",
                               commit_message=f"Deploy {os.environ.get('GITHUB_SHA', 'local')[:12]}",
                               delete_patterns=["*"])
    shutil.rmtree(folder, ignore_errors=True)
    oid = getattr(commit, "oid", "") or ""
    print(f"uploaded {oid[:12]}; waiting for the build")

    # On a redeploy the OLD version keeps reporting RUNNING until the new build
    # starts, so RUNNING alone would declare success early. Accept RUNNING only
    # for the uploaded commit when the runtime reports one, or else only after
    # the build has visibly started (or 3 minutes passed with no rebuild, which
    # is what an upload with no changes looks like).
    deadline = time.time() + 30 * 60
    started = time.time()
    saw_build = False
    stage_name = ""
    while time.time() < deadline:
        rt = api.get_space_runtime(space_id)
        stage_name = rt.stage
        live_sha = (getattr(rt, "raw", None) or {}).get("sha") or ""
        if stage_name in ("BUILDING", "RUNNING_BUILDING", "APP_STARTING", "RUNNING_APP_STARTING"):
            saw_build = True
        if stage_name == "RUNNING":
            if live_sha and oid:
                if live_sha == oid:
                    break
            elif saw_build or time.time() - started > 180:
                break
        if stage_name in ("BUILD_ERROR", "RUNTIME_ERROR", "CONFIG_ERROR", "NO_APP_FILE"):
            die(f"space failed: {stage_name}. Logs: https://huggingface.co/spaces/{space_id}?logs=build")
        time.sleep(20)
    else:
        die(f"space did not start within 30 minutes (last stage {stage_name})")

    url = space_url(space_id)
    for _ in range(30):
        code, status = get_json(f"{url}/auth/status")
        if code == 200:
            break
        time.sleep(10)
    if code != 200:
        die(f"{url}/auth/status answered {code}")
    if status.get("guest_mode") is not True:
        die(f"{url} is up but guest mode is OFF: {status}")

    print(f"\nLIVE: {url}")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(f"### Deployed\n\n{url}\n")


if __name__ == "__main__":
    main()
