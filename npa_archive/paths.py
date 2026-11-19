"""Filesystem layout and path checks for the local archive."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AGENTS_DIR = ROOT / "agents"
DATA_DIR = ROOT / "data"
ROUNDS_DIR = DATA_DIR / "rounds"
STATE_PATH = DATA_DIR / "state.json"
DASHBOARD_STATIC = ROOT / "neverplayalone_bench" / "dashboard" / "static"
BENCH_DIR = ROOT / "neverplayalone_bench"

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def agent_dirname(miner_uid: int, miner_name: str) -> str:
    slug = _SLUG_RE.sub("-", (miner_name or "miner").lower()).strip("-") or "miner"
    return f"uid-{int(miner_uid)}-{slug}"


def agent_dir(round_id: str, miner_uid: int, miner_name: str) -> Path:
    return AGENTS_DIR / round_id / agent_dirname(miner_uid, miner_name)


def safe_relative(raw: str) -> Path:
    """Return a relative path that cannot escape its destination directory."""
    text = str(raw or "").replace("\\", "/").strip()
    if not text:
        raise ValueError("empty archive path")
    parts: list[str] = []
    for part in text.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            raise ValueError(f"archive path escapes its directory: {raw}")
        parts.append(part)
    if not parts:
        raise ValueError(f"archive path is empty after normalization: {raw}")
    return Path(*parts)


def run_dirname(agent_run_id: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", agent_run_id).strip("._")
    return cleaned or "run"
