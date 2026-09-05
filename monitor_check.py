"""Scheduled world-update monitor: anonymous staggered version checks.

Monitors a list of VRChat worlds for version changes using the public API.
For worlds with updates, dispatches decompile-world.yml builds in the same
repository. World checks are staggered (random 2-5s) to avoid rate limits.
"""

from __future__ import annotations

import base64
import json
import os
import random
import subprocess
import time
import urllib.request

USER_AGENT = "vrcw-downloader/0.1 monitor"
PRIVATE_REPO = "SENyiAi/Udon"
BUILD_REPO = "SENyiAi/Udon_Build"
STATE_PATH = "monitor-state.json"


def gh_api(path: str, token: str, method: str = "GET", payload=None):
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "monitor-script",
    }
    data = json.dumps(payload).encode() if payload else None
    req = urllib.request.Request(
        f"https://api.github.com{path}", method=method, data=data, headers=headers)
    with urllib.request.urlopen(req) as resp:
        body = resp.read()
        return json.loads(body) if body else {}


def read_world_list(token: str) -> list[str]:
    data = gh_api("/repos/SENyiAi/Udon/contents/worlds.txt", token)
    raw = base64.b64decode(data["content"]).decode()
    return sorted({line.strip() for line in raw.splitlines()
                   if line.strip().startswith("wrld_")})


def read_state(token: str) -> dict[str, int]:
    try:
        data = gh_api(f"/repos/SENyiAi/Udon/contents/{STATE_PATH}", token)
        content = base64.b64decode(data["content"]).decode()
        return {k: int(v) for k, v in json.loads(content).items()}
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return {}
        raise


def write_state(token: str, state: dict[str, int], existing_sha: str | None) -> None:
    payload: dict = {
        "message": "monitor: update world versions",
        "content": base64.b64encode(json.dumps(state, indent=2, sort_keys=True).encode()).decode(),
    }
    if existing_sha:
        payload["sha"] = existing_sha
    gh_api(f"/repos/SENyiAi/Udon/contents/{STATE_PATH}", token=token, method="PUT", payload=payload)


def anonymous_world_version(world_id: str) -> int:
    """Public metadata version — increments on any world update. Returns 0 when
    the world is inaccessible anonymously."""
    req = urllib.request.Request(
        f"https://api.vrchat.cloud/api/1/worlds/{world_id}",
        headers={"User-Agent": "vrcw-downloader/0.1 monitor"})
    try:
        with urllib.request.urlopen(req) as resp:
            data = json.load(resp)
        return int(data.get("version", 0))
    except Exception:
        return 0


def dispatch_build(world_id: str, dispatch_token: str) -> None:
    req = urllib.request.Request(
        f"https://api.github.com/repos/{BUILD_REPO}/actions/workflows/decompile-world.yml/dispatches",
        method="POST",
        data=json.dumps({"ref": "main", "inputs": {"world_id": world_id}}).encode(),
        headers={"Authorization": f"token {dispatch_token}",
                 "Accept": "application/vnd.github+json",
                 "User-Agent": "monitor-script"},
    )
    with urllib.request.urlopen(req) as resp:
        resp.read()


def main() -> None:
    token = os.environ["PRIVATE_REPO_TOKEN"]
    dispatch_token = os.environ.get("GH_DISPATCH_TOKEN", "")

    worlds = read_world_list(token)
    print(f"monitoring {len(worlds)} worlds")

    state = read_state(token)
    print(f"state entries: {len(state)}")

    interval_min = int(os.environ.get("CHECK_INTERVAL_MIN_SECONDS", "2"))
    interval_max = int(os.environ.get("CHECK_INTERVAL_MAX_SECONDS", "5"))
    checked = unchanged = failed = 0
    updated: list[str] = []

    for index, world in enumerate(worlds):
        if index > 0:
            time.sleep(random.uniform(interval_min, interval_max))
        checked += 1
        try:
            remote_ver = anonymous_world_version(world)
        except Exception as error:
            print(f"{world}: api failed {type(error).__name__}: {error}")
            failed += 1
            continue

        last_seen = state.get(world, 0)
        if remote_ver <= last_seen:
            unchanged += 1
            print(f"{world}: up-to-date (remote v{remote_ver} <= seen v{last_seen})")
            continue

        print(f"{world}: UPDATE remote v{remote_ver} > seen v{last_seen} -> dispatch build")
        try:
            dispatch_build(world, dispatch_token)
            updated.append(world)
            state[world] = remote_ver
        except Exception as error:
            print(f"{world}: dispatch failed {error}")
            failed += 1

    write_state(token, state, None)
    print(f"SUMMARY: checked={checked} updated={len(updated)} unchanged={unchanged} failed={failed}")
    if updated:
        print("updated worlds:", " ".join(updated))


if __name__ == "__main__":
    main()
