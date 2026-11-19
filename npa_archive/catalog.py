"""Build the dashboard catalog from the on-disk archive."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from npa_archive.paths import DATA_DIR


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as stream:
        return json.load(stream)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(payload, stream, indent=2, ensure_ascii=False)
        stream.write("\n")
    temporary.replace(path)


ANIMAL_HUSBANDRY = "Animal Husbandry"
ANIMAL_HUSBANDRY_SLUG = "animal_husbandry"
RUN_PAGE = "https://dashboard.neverplayalone.ai/runs/"


def _mission_name(round_row: dict[str, Any]) -> tuple[str, str]:
    mission = round_row.get("mission") or {}
    if isinstance(mission, dict):
        return str(mission.get("name") or mission.get("slug") or "—"), str(mission.get("slug") or "")
    if isinstance(mission, str) and mission:
        return mission, ""
    return "—", ""


def is_animal_husbandry(round_row: dict[str, Any]) -> bool:
    """True when a saved or API round is the Animal Husbandry mission."""
    name, slug = _mission_name(round_row)
    return slug == ANIMAL_HUSBANDRY_SLUG or name == ANIMAL_HUSBANDRY


def _run_links(standing: dict[str, Any]) -> list[dict[str, Any]]:
    links: list[dict[str, Any]] = []
    seen: set[str] = set()
    for validator in standing.get("per_validator") or []:
        if not isinstance(validator, dict):
            continue
        run_id = validator.get("agent_run_id")
        if not isinstance(run_id, str) or not run_id or run_id in seen:
            continue
        seen.add(run_id)
        links.append(
            {
                "agent_run_id": run_id,
                "url": RUN_PAGE + run_id,
                "validator_uid": validator.get("validator_uid"),
                "score": validator.get("score"),
                "status": validator.get("status"),
            }
        )
    return links


def _saved_tasks(project_root: Path, manifest: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not manifest:
        return []
    relative = manifest.get("_dir")
    if not isinstance(relative, str) or not relative:
        return []
    folder = project_root / relative
    tasks: list[dict[str, Any]] = []
    for run in manifest.get("runs") or []:
        if not isinstance(run, dict):
            continue
        run_dir = run.get("dir")
        if not isinstance(run_dir, str) or not run_dir:
            continue
        path = folder / run_dir / "run.json"
        if not path.is_file():
            continue
        payload = read_json(path)
        if not isinstance(payload, dict):
            continue
        for task in payload.get("tasks") or []:
            if not isinstance(task, dict):
                continue
            tasks.append(
                {
                    "index": task.get("task_index"),
                    "score": task.get("score"),
                    "status": task.get("status"),
                }
            )
    return tasks


def _primary_validator(standing: dict[str, Any]) -> dict[str, Any]:
    rows = standing.get("per_validator") or []
    if isinstance(rows, list) and rows and isinstance(rows[0], dict):
        return rows[0]
    return {}


def _find_manifest(agents_dir: Path, project_root: Path, round_id: str, miner_uid: int) -> dict[str, Any] | None:
    day = agents_dir / round_id
    if not day.is_dir():
        return None
    prefix = f"uid-{int(miner_uid)}-"
    matches = [path for path in day.glob(f"{prefix}*") if path.is_dir() and path.name.startswith(prefix)]
    for folder in matches:
        manifest_path = folder / "manifest.json"
        if manifest_path.is_file():
            payload = read_json(manifest_path)
            if isinstance(payload, dict):
                payload["_dir"] = str(folder.relative_to(project_root))
                return payload
    return None


def _agent_row(
    round_row: dict[str, Any],
    standing: dict[str, Any],
    crown_uid: int | None,
    agents_dir: Path,
    project_root: Path,
) -> dict[str, Any]:
    uid = standing.get("miner_uid")
    uid_int = int(uid) if isinstance(uid, int) else None
    champion_uid = round_row.get("champion_uid_after")
    manifest = (
        _find_manifest(agents_dir, project_root, str(round_row.get("round_id")), uid_int)
        if uid_int is not None
        else None
    )
    validator = _primary_validator(standing)
    is_reigning = crown_uid is not None and uid_int == crown_uid
    is_round_champion = isinstance(champion_uid, int) and uid_int == champion_uid
    if is_reigning or (manifest or {}).get("code_private"):
        code_state = "private"
    elif manifest and manifest.get("code_files"):
        code_state = "saved"
    elif manifest and manifest.get("error"):
        code_state = "error"
    else:
        code_state = "not_synced"
    mcpr_count = 0
    if manifest:
        for run in manifest.get("runs") or []:
            if isinstance(run, dict):
                mcpr_count += len(run.get("mcpr") or [])
    links = _run_links(standing)
    primary = links[0] if links else {}
    return {
        "uid": uid_int,
        "name": standing.get("miner_name"),
        "rank": standing.get("rank"),
        "score": standing.get("consensus_score"),
        "entry_kind": standing.get("entry_kind"),
        "success": standing.get("success"),
        "status": validator.get("status"),
        "hotkey": standing.get("miner_hotkey"),
        "is_round_champion": is_round_champion,
        "is_reigning_crown": is_reigning,
        "code": code_state,
        "mcpr_count": mcpr_count,
        "dir": (manifest or {}).get("_dir"),
        "skipped_reason": (manifest or {}).get("skipped_reason"),
        "run_id": primary.get("agent_run_id"),
        "run_url": primary.get("url"),
        "runs": links,
        "tasks": _saved_tasks(project_root, manifest),
    }


def build_catalog(root: Path | None = None) -> dict[str, Any]:
    """Assemble the round table from saved round files and agent manifests."""
    project_root = root or DATA_DIR.parent
    data_dir = project_root / "data"
    agents_dir = project_root / "agents"
    rounds_dir = data_dir / "rounds"
    state_path = data_dir / "state.json"
    state: dict[str, Any] = {}
    if state_path.is_file():
        loaded = read_json(state_path)
        if isinstance(loaded, dict):
            state = loaded
    crown = state.get("crown") if isinstance(state.get("crown"), dict) else {}
    crown_uid = crown.get("uid")
    crown_uid_int = crown_uid if isinstance(crown_uid, int) else None
    rounds: list[dict[str, Any]] = []
    if rounds_dir.is_dir():
        for path in sorted(rounds_dir.glob("*.json"), reverse=True):
            row = read_json(path)
            if not isinstance(row, dict) or not is_animal_husbandry(row):
                continue
            mission_name, mission_slug = _mission_name(row)
            standings = row.get("standings") or []
            agents = [
                _agent_row(row, standing, crown_uid_int, agents_dir, project_root)
                for standing in standings
                if isinstance(standing, dict)
            ]
            agents.sort(key=lambda item: (item.get("rank") is None, item.get("rank") or 0))
            rounds.append(
                {
                    "round_id": row.get("round_id"),
                    "round": row.get("round"),
                    "label": row.get("label") or row.get("round_id"),
                    "status": row.get("status"),
                    "mission": mission_name,
                    "mission_slug": mission_slug,
                    "n_agents": len(agents),
                    "winner_uid": row.get("winner_uid"),
                    "winner_name": row.get("winner_name"),
                    "winner_score": row.get("winner_score"),
                    "champion_uid": row.get("champion_uid_after"),
                    "champion_name": row.get("champion_name_after"),
                    "dethroned": row.get("dethroned"),
                    "start_block": row.get("start_block"),
                    "end_block": row.get("end_block"),
                    "agents": agents,
                }
            )
    rounds.sort(key=lambda item: str(item.get("round_id") or ""), reverse=True)
    current = state.get("current_round") if isinstance(state.get("current_round"), dict) else {}
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "fetched_at": state.get("fetched_at"),
        "crown": crown,
        "current_round": current,
        "rounds": rounds,
    }
