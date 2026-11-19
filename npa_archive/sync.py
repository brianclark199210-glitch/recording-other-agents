"""Download public round artifacts, skipping the reigning crown."""

from __future__ import annotations

import shutil
import tarfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from npa_archive.catalog import is_animal_husbandry, read_json, write_json
from npa_archive.client import ApiError, NpaClient
from npa_archive.paths import (
    AGENTS_DIR,
    ROUNDS_DIR,
    STATE_PATH,
    agent_dir,
    run_dirname,
    safe_relative,
)

_PRINT_LOCK = threading.Lock()


def log(message: str) -> None:
    with _PRINT_LOCK:
        print(message, flush=True)


def _format_bytes(size: int) -> str:
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def redact(value: Any) -> Any:
    """Drop presigned query strings so saved JSON does not keep expiring credentials."""
    if isinstance(value, dict):
        return {key: redact(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str) and ("X-Amz-" in value or "r2.cloudflarestorage.com" in value):
        return value.split("?", 1)[0]
    return value


def select_round_ids(
    rounds: list[dict[str, Any]],
    *,
    round_id: str | None,
    previous: int | None,
    include_all: bool,
) -> list[str]:
    """Choose which completed Animal Husbandry rounds receive code, detail, and replay files."""
    rounds = [row for row in rounds if is_animal_husbandry(row)]
    completed = [row for row in rounds if row.get("status") == "completed" and row.get("round_id")]
    if round_id:
        known = {str(row.get("round_id")) for row in rounds}
        if round_id not in known:
            raise ValueError(f"unknown round {round_id}")
        return [round_id]
    if include_all:
        return [str(row["round_id"]) for row in completed]
    count = 1 if previous is None else previous
    if count < 1:
        return []
    return [str(row["round_id"]) for row in completed[:count]]


def _existing_artifact_sync(round_id: str) -> str:
    path = ROUNDS_DIR / f"{round_id}.json"
    if not path.is_file():
        return "summary"
    loaded = read_json(path)
    if isinstance(loaded, dict) and loaded.get("artifact_sync") == "full":
        return "full"
    return "summary"


def _crown_reason(client: NpaClient, crown_uid: int | None) -> str | None:
    if not isinstance(crown_uid, int):
        return None
    if STATE_PATH.is_file():
        loaded = read_json(STATE_PATH)
        previous = loaded.get("crown") if isinstance(loaded, dict) else None
        if isinstance(previous, dict) and previous.get("uid") == crown_uid and isinstance(previous.get("reason"), str):
            return previous["reason"]
    try:
        code = client.agent_code(crown_uid)
    except Exception as exc:  # noqa: BLE001 - a failed lookup must not hide the public scoreboard
        return f"Crown code lookup failed ({exc}). Artifacts were not saved."
    if code.get("files"):
        return "Reigning champion code was offered by the API and was not saved."
    reason = code.get("reason")
    return reason if isinstance(reason, str) else None


def _current_summary(current: dict[str, Any]) -> dict[str, Any]:
    mission = current.get("mission")
    return {
        "round_id": current.get("round_id"),
        "round": current.get("round"),
        "label": current.get("label"),
        "status": current.get("status"),
        "mission": mission.get("name") if isinstance(mission, dict) else None,
        "mission_slug": mission.get("slug") if isinstance(mission, dict) else None,
        "current_block": current.get("current_block"),
        "start_block": current.get("start_block"),
        "end_block": current.get("end_block"),
    }


def refresh_public(client: NpaClient | None = None) -> dict[str, Any]:
    """Rewrite the live round, crown, and Animal Husbandry standings from the public API."""
    client = client or NpaClient()
    log("refresh live round and standings")
    current = client.current_round()
    overview = client.overview()
    rounds = client.rounds()
    husbandry = [row for row in rounds if is_animal_husbandry(row)]
    if not husbandry:
        raise RuntimeError("the public round list had no Animal Husbandry rounds")
    king = overview.get("king") if isinstance(overview.get("king"), dict) else {}
    crown_uid = king.get("uid") if isinstance(king.get("uid"), int) else None
    crown = crown_from_overview(overview, _crown_reason(client, crown_uid))
    ROUNDS_DIR.mkdir(parents=True, exist_ok=True)
    kept_ids: set[str] = set()
    for row in husbandry:
        round_id = row.get("round_id")
        if not isinstance(round_id, str):
            continue
        saved = dict(row)
        saved["artifact_sync"] = _existing_artifact_sync(round_id)
        write_json(ROUNDS_DIR / f"{round_id}.json", redact(saved))
        kept_ids.add(round_id)
    prune_other_missions(kept_ids)
    write_json(
        STATE_PATH,
        {
            "fetched_at": datetime.now(UTC).isoformat(),
            "api_base": client.base_url,
            "current_round": _current_summary(current),
            "crown": crown,
            "selected_rounds": [round_id for round_id in kept_ids if _existing_artifact_sync(round_id) == "full"],
        },
    )
    completed = [
        str(row["round_id"])
        for row in husbandry
        if row.get("status") == "completed" and isinstance(row.get("round_id"), str)
    ]
    latest_completed = max(completed) if completed else None
    log(f"live round {current.get('round_id')} ({current.get('status')}); crown uid {crown_uid}")
    return {
        "current_round": current.get("round_id"),
        "status": current.get("status"),
        "crown_uid": crown_uid,
        "rounds_indexed": len(kept_ids),
        "latest_completed": latest_completed,
    }


def crown_from_overview(overview: dict[str, Any], code_reason: str | None) -> dict[str, Any]:
    king = overview.get("king") if isinstance(overview.get("king"), dict) else {}
    uid = king.get("uid")
    return {
        "uid": uid if isinstance(uid, int) else None,
        "name": king.get("name"),
        "rounds_as_king": king.get("rounds_as_king"),
        "since_label": king.get("since_label"),
        "score": king.get("score"),
        "code_private": True,
        "reason": code_reason
        or "The reigning champion source stays private until they are dethroned or the privacy window ends.",
    }


def _extract_submission(archive: Path, dest: Path) -> tuple[int, int]:
    count = 0
    total = 0
    with tarfile.open(archive, "r:*") as bundle:
        for member in bundle.getmembers():
            if not member.isfile():
                continue
            relative = safe_relative(member.name)
            target = dest / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            extracted = bundle.extractfile(member)
            if extracted is None:
                continue
            data = extracted.read()
            target.write_bytes(data)
            count += 1
            total += len(data)
    return count, total


def prune_other_missions(kept_round_ids: set[str]) -> None:
    """Delete round files and agent folders that are not Animal Husbandry."""
    if ROUNDS_DIR.is_dir():
        for path in ROUNDS_DIR.glob("*.json"):
            if path.stem in kept_round_ids:
                continue
            path.unlink()
            log(f"removed {path.name}")
    if AGENTS_DIR.is_dir():
        for day in AGENTS_DIR.iterdir():
            if not day.is_dir() or day.name in kept_round_ids:
                continue
            shutil.rmtree(day)
            log(f"removed agents/{day.name}")


def _write_code_tree(payload: dict[str, Any], dest: Path) -> tuple[int, int]:
    files = payload.get("files") or []
    count = 0
    total = 0
    if not isinstance(files, list):
        return 0, 0
    for item in files:
        if not isinstance(item, dict):
            continue
        relative = safe_relative(str(item.get("path") or ""))
        target = dest / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        content = item.get("content")
        if not isinstance(content, str):
            continue
        data = content.encode("utf-8")
        target.write_bytes(data)
        count += 1
        total += len(data)
    return count, total


def _download_file(client: NpaClient, url: str, dest: Path) -> int:
    if dest.is_file() and dest.stat().st_size > 0:
        return dest.stat().st_size
    dest.parent.mkdir(parents=True, exist_ok=True)
    temporary = dest.with_suffix(dest.suffix + ".part")
    with temporary.open("wb") as stream:
        client.download(url, stream)
    temporary.replace(dest)
    return dest.stat().st_size


def _run_records(client: NpaClient, standing: dict[str, Any], folder: Path, *, with_mcpr: bool) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for validator in standing.get("per_validator") or []:
        if not isinstance(validator, dict):
            continue
        agent_run_id = validator.get("agent_run_id")
        if not isinstance(agent_run_id, str) or agent_run_id in seen:
            continue
        seen.add(agent_run_id)
        run_dir = folder / "runs" / run_dirname(agent_run_id)
        run_dir.mkdir(parents=True, exist_ok=True)
        log(f"  run {agent_run_id}")
        payload = client.agent_run(agent_run_id)
        local_files: dict[str, Any] = {}
        mcpr_paths: list[str] = []
        if with_mcpr:
            tasks = payload.get("tasks") or []
            if isinstance(tasks, list):
                for task in tasks:
                    if not isinstance(task, dict):
                        continue
                    url = task.get("recording_download_url")
                    index = task.get("task_index")
                    if isinstance(url, str) and url.startswith("http") and isinstance(index, int):
                        dest = run_dir / f"task-{index}.mcpr"
                        try:
                            size = _download_file(client, url, dest)
                        except Exception as exc:  # noqa: BLE001 - keep the rest of this run
                            local_files[f"task_{index}_mcpr_error"] = str(exc)
                            log(f"    task {index} replay failed: {exc}")
                            continue
                        relative = str(dest.relative_to(folder))
                        mcpr_paths.append(relative)
                        local_files[f"task_{index}_mcpr"] = relative
                        local_files[f"task_{index}_mcpr_bytes"] = size
                        log(f"    task {index} replay {_format_bytes(size)}")
            report = payload.get("report") if isinstance(payload.get("report"), dict) else {}
            report_url = report.get("download_url") if isinstance(report, dict) else None
            if isinstance(report_url, str) and report_url.startswith("http"):
                dest = run_dir / "report.json"
                try:
                    _download_file(client, report_url, dest)
                    local_files["report"] = str(dest.relative_to(folder))
                except Exception as exc:  # noqa: BLE001 - the run JSON is still useful
                    local_files["report_error"] = str(exc)
                    log(f"    report failed: {exc}")
        saved = redact(payload)
        if isinstance(saved, dict):
            saved["local_files"] = local_files
            write_json(run_dir / "run.json", saved)
        else:
            write_json(run_dir / "run.json", {"local_files": local_files})
        records.append(
            {
                "agent_run_id": agent_run_id,
                "dir": str(run_dir.relative_to(folder)),
                "score": payload.get("score") if isinstance(payload, dict) else None,
                "status": payload.get("status") if isinstance(payload, dict) else None,
                "validator_uid": payload.get("validator_uid") if isinstance(payload, dict) else None,
                "mcpr": mcpr_paths,
            }
        )
    return records


def archive_agent(
    client: NpaClient,
    round_id: str,
    standing: dict[str, Any],
    crown_uid: int | None,
    *,
    with_mcpr: bool,
) -> dict[str, Any]:
    miner_uid = standing.get("miner_uid")
    miner_name = str(standing.get("miner_name") or f"Miner {miner_uid}")
    if not isinstance(miner_uid, int):
        return {"miner_name": miner_name, "error": "standing has no miner uid"}
    folder = agent_dir(round_id, miner_uid, miner_name)
    if crown_uid is not None and miner_uid == crown_uid:
        log(f"skip crown {miner_name} uid {miner_uid} ({round_id})")
        return {
            "miner_uid": miner_uid,
            "miner_name": miner_name,
            "skipped": True,
            "code_private": True,
            "skipped_reason": "Reigning champion source, replays, and detail are private.",
        }
    folder.mkdir(parents=True, exist_ok=True)
    log(f"archive {round_id} {miner_name} uid {miner_uid}")
    detail = client.agent(miner_uid)
    code = client.agent_code(miner_uid)
    if code.get("code_private"):
        reason = str(code.get("reason") or "source is private")
        log(f"  private code for uid {miner_uid}: {reason}")
        write_json(
            folder / "manifest.json",
            {
                "round_id": round_id,
                "miner_uid": miner_uid,
                "miner_name": miner_name,
                "code_private": True,
                "skipped_reason": reason,
                "detail_saved": False,
                "runs": [],
            },
        )
        return {
            "miner_uid": miner_uid,
            "miner_name": miner_name,
            "code_private": True,
            "skipped_reason": reason,
        }
    write_json(folder / "detail.json", redact(detail))
    write_json(folder / "standing.json", redact(standing))
    code_dir = folder / "code"
    file_count, file_bytes = _write_code_tree(code, code_dir)
    if file_count == 0 and not code.get("download_url"):
        log(f"  code listing empty for uid {miner_uid}; retrying")
        time.sleep(1.5)
        code = client.agent_code(miner_uid)
        if code.get("code_private"):
            raise RuntimeError(f"uid {miner_uid} became private during download")
        file_count, file_bytes = _write_code_tree(code, code_dir)
    if file_count == 0:
        log(f"  code listing empty for uid {miner_uid}; using submission archive")
    code_meta = {
        "uid": code.get("uid"),
        "name": code.get("name"),
        "archive_key": code.get("archive_key"),
        "version": code.get("version"),
        "total_bytes": code.get("total_bytes"),
        "file_count": file_count,
        "written_bytes": file_bytes,
        "code_private": False,
    }
    archive_url = code.get("download_url")
    if isinstance(archive_url, str) and archive_url.startswith("http"):
        archive_path = folder / "submission.tar.gz"
        try:
            _download_file(client, archive_url, archive_path)
        except ApiError as exc:
            if exc.status != 403:
                raise
            log(f"  submission link expired for uid {miner_uid}; refreshing")
            code = client.agent_code(miner_uid)
            if code.get("code_private"):
                raise RuntimeError(f"uid {miner_uid} became private during download") from exc
            archive_url = code.get("download_url")
            if not isinstance(archive_url, str) or not archive_url.startswith("http"):
                raise
            file_count, file_bytes = _write_code_tree(code, code_dir)
            code_meta["file_count"] = file_count
            code_meta["written_bytes"] = file_bytes
            if file_count == 0:
                _download_file(client, archive_url, archive_path)
        code_meta["archive_key"] = code.get("archive_key")
        if archive_path.is_file():
            code_meta["submission_archive"] = archive_path.name
        if file_count == 0:
            file_count, file_bytes = _extract_submission(archive_path, code_dir)
            code_meta["file_count"] = file_count
            code_meta["written_bytes"] = file_bytes
    if file_count == 0:
        existing = [path for path in code_dir.rglob("*") if path.is_file()]
        if existing:
            file_count = len(existing)
            file_bytes = sum(path.stat().st_size for path in existing)
            code_meta["file_count"] = file_count
            code_meta["written_bytes"] = file_bytes
            log(f"  kept {file_count} code files already on disk for uid {miner_uid}")
        else:
            raise RuntimeError(f"uid {miner_uid} returned no source files and no submission archive")
    write_json(folder / "code_meta.json", code_meta)
    runs = _run_records(client, standing, folder, with_mcpr=with_mcpr)
    manifest = {
        "round_id": round_id,
        "miner_uid": miner_uid,
        "miner_name": miner_name,
        "hotkey": standing.get("miner_hotkey"),
        "rank": standing.get("rank"),
        "consensus_score": standing.get("consensus_score"),
        "entry_kind": standing.get("entry_kind"),
        "code_private": False,
        "detail_saved": True,
        "code_dir": "code",
        "code_files": file_count,
        "code_bytes": file_bytes,
        "archive_key": code.get("archive_key"),
        "runs": runs,
    }
    write_json(folder / "manifest.json", manifest)
    return manifest


def sync_archive(
    *,
    round_id: str | None = None,
    previous: int | None = 1,
    include_all: bool = False,
    with_mcpr: bool = True,
    workers: int = 4,
    client: NpaClient | None = None,
) -> dict[str, Any]:
    """Refresh Animal Husbandry standings and download selected non-crown agents."""
    client = client or NpaClient()
    ROUNDS_DIR.mkdir(parents=True, exist_ok=True)
    AGENTS_DIR.mkdir(parents=True, exist_ok=True)
    log("fetch current round, overview, and round list")
    current = client.current_round()
    overview = client.overview()
    rounds = client.rounds()
    crown_uid = None
    king = overview.get("king") if isinstance(overview.get("king"), dict) else {}
    if isinstance(king.get("uid"), int):
        crown_uid = king["uid"]
    reason = None
    if isinstance(crown_uid, int):
        try:
            code = client.agent_code(crown_uid)
            reason = code.get("reason") if code.get("code_private") else None
            if code.get("files"):
                reason = "Reigning champion code was offered by the API and was not saved."
        except Exception as exc:  # noqa: BLE001 - crown privacy still applies if this lookup fails
            reason = f"Crown code lookup failed ({exc}). Artifacts were not saved."
    crown = crown_from_overview(overview, reason if isinstance(reason, str) else None)
    husbandry = [row for row in rounds if is_animal_husbandry(row)]
    if round_id:
        known = {str(row.get("round_id")) for row in rounds}
        husbandry_ids = {str(row.get("round_id")) for row in husbandry}
        if round_id in known and round_id not in husbandry_ids:
            raise ValueError(f"{round_id} is not an Animal Husbandry round")
    selected = select_round_ids(husbandry, round_id=round_id, previous=previous, include_all=include_all)
    by_id = {str(row.get("round_id")): row for row in husbandry}
    kept_ids: set[str] = set()
    for row in husbandry:
        rid = row.get("round_id")
        if isinstance(rid, str):
            saved = dict(row)
            saved["artifact_sync"] = "full" if rid in selected else "summary"
            write_json(ROUNDS_DIR / f"{rid}.json", redact(saved))
            kept_ids.add(rid)
    prune_other_missions(kept_ids)
    write_json(
        STATE_PATH,
        {
            "fetched_at": datetime.now(UTC).isoformat(),
            "api_base": client.base_url,
            "current_round": {
                "round_id": current.get("round_id"),
                "round": current.get("round"),
                "label": current.get("label"),
                "status": current.get("status"),
                "mission": (current.get("mission") or {}).get("name")
                if isinstance(current.get("mission"), dict)
                else None,
                "mission_slug": (current.get("mission") or {}).get("slug")
                if isinstance(current.get("mission"), dict)
                else None,
                "current_block": current.get("current_block"),
                "start_block": current.get("start_block"),
                "end_block": current.get("end_block"),
            },
            "crown": crown,
            "selected_rounds": selected,
        },
    )
    log(f"current round {current.get('round_id')} ({current.get('status')})")
    log(f"reigning crown uid {crown.get('uid')} {crown.get('name')}")
    log(f"artifact rounds: {', '.join(selected) if selected else '(none)'}")
    errors: list[str] = []
    saved_agents = 0
    for rid in selected:
        row = by_id.get(rid)
        if not row:
            continue
        standings = [item for item in (row.get("standings") or []) if isinstance(item, dict)]
        log(f"round {rid}: {len(standings)} standings")
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            futures = [
                pool.submit(archive_agent, client, rid, standing, crown_uid, with_mcpr=with_mcpr)
                for standing in standings
            ]
            for future in as_completed(futures):
                try:
                    result = future.result()
                except Exception as exc:  # noqa: BLE001 - keep the rest of the round
                    errors.append(str(exc))
                    log(f"  error: {exc}")
                    continue
                if not result.get("skipped") and not result.get("code_private"):
                    saved_agents += 1
    summary = {
        "current_round": current.get("round_id"),
        "crown_uid": crown.get("uid"),
        "rounds_indexed": len(husbandry),
        "rounds_skipped": len(rounds) - len(husbandry),
        "rounds_archived": selected,
        "agents_saved": saved_agents,
        "errors": errors,
    }
    log(
        f"done: indexed {len(husbandry)} Animal Husbandry rounds, "
        f"skipped {len(rounds) - len(husbandry)} other missions, saved {saved_agents} agents "
        f"from {len(selected)} round(s), errors {len(errors)}"
    )
    return summary
