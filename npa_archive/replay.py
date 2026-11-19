"""Print everything saved for one archived agent directory."""

from __future__ import annotations

import html
import json
import re
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import quote

from npa_archive.paths import run_dirname

_RUN_ID_RE = re.compile(r"^[A-Za-z0-9:._-]{8,240}$")
_MCPR_NAME = re.compile(r"^task-\d+\.mcpr$")
RUN_PAGE = "https://dashboard.neverplayalone.ai/runs/"


def _read(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as stream:
        return json.load(stream)


def find_agent_root(path: Path) -> Path:
    """Accept the agent folder or a nested code/runs path."""
    current = path.resolve()
    if current.is_file():
        current = current.parent
    for candidate in (current, *current.parents):
        if (candidate / "manifest.json").is_file() or (candidate / "detail.json").is_file():
            return candidate
        if candidate.parent == candidate:
            break
    return path.resolve()


def _section(title: str) -> str:
    return f"\n{title}\n{'-' * len(title)}\n"


def _bytes(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def _tree(directory: Path, limit: int = 400) -> list[str]:
    if not directory.is_dir():
        return []
    lines: list[str] = []
    files = sorted(item for item in directory.rglob("*") if item.is_file())
    for item in files[:limit]:
        lines.append(f"  {item.relative_to(directory)}  ({_bytes(item.stat().st_size)})")
    if len(files) > limit:
        lines.append(f"  ... {len(files) - limit} more files")
    return lines


def _format_run(agent_root: Path, run_dir: Path) -> list[str]:
    payload_path = run_dir / "run.json"
    if not payload_path.is_file():
        return [f"missing {payload_path.name}"]
    payload = _read(payload_path)
    if not isinstance(payload, dict):
        return ["run.json was not an object"]
    lines = [
        f"agent_run_id: {payload.get('agent_run_id')}",
        f"status: {payload.get('status')}  success: {payload.get('success')}",
        f"score: {payload.get('score')} / {payload.get('max_score')}",
        f"task_score: {payload.get('task_score')}  ranking_score: {payload.get('ranking_score')}",
        f"consensus_score: {payload.get('consensus_score')}  rank: {payload.get('rank')}",
        f"mission: {(payload.get('mission') or {}).get('name') if isinstance(payload.get('mission'), dict) else payload.get('mission')}",
        f"validator: {payload.get('validator_name')} uid {payload.get('validator_uid')}",
        f"model: {payload.get('model')}  provider: {payload.get('provider')}",
        f"seed: {payload.get('seed')}",
        f"task_seed: {payload.get('task_seed')}",
        f"minecraft_seed: {payload.get('minecraft_seed')}",
        f"elapsed_seconds: {payload.get('elapsed_seconds')}",
        f"deaths: {payload.get('deaths')}  alive: {payload.get('alive')}  final_health: {payload.get('final_health')}",
        f"deploy_ok: {payload.get('deploy_ok')}  tokens: {payload.get('tokens')}  cost_usd: {payload.get('cost_usd')}",
        f"started_at: {payload.get('started_at')}",
        f"task_prompt: {payload.get('task_prompt')}",
        f"submission_id: {payload.get('submission_id')}",
        f"source_round_id: {payload.get('source_round_id')}",
        f"entry_kind: {payload.get('entry_kind')}",
    ]
    resources = payload.get("resources") or []
    if isinstance(resources, list) and resources:
        lines.append("resources:")
        for resource in resources:
            if isinstance(resource, dict):
                lines.append(
                    "  {display} observed {observed} / {target}  points {points}/{max_points}".format(
                        display=resource.get("display_name") or resource.get("key"),
                        observed=resource.get("observed"),
                        target=resource.get("target_count"),
                        points=resource.get("points"),
                        max_points=resource.get("max_points"),
                    )
                )
    enclosure = payload.get("enclosure")
    if isinstance(enclosure, dict) and enclosure:
        lines.append(
            "enclosure: valid={valid} {width}x{depth} issues={issues}".format(
                valid=payload.get("enclosure_valid"),
                width=enclosure.get("width"),
                depth=enclosure.get("depth"),
                issues=enclosure.get("issue_count"),
            )
        )
    tasks = payload.get("tasks") or []
    if isinstance(tasks, list):
        lines.append(f"tasks: {len(tasks)}")
        for task in tasks:
            if not isinstance(task, dict):
                continue
            index = task.get("task_index")
            mcpr = run_dir / f"task-{index}.mcpr"
            mcpr_note = _bytes(mcpr.stat().st_size) if mcpr.is_file() else "not saved"
            lines.append(
                f"  task {index}: status={task.get('status')} score={task.get('score')} "
                f"elapsed={task.get('elapsed_seconds')} mcpr={mcpr_note}"
            )
            prompt = task.get("task_prompt")
            if isinstance(prompt, str) and prompt:
                lines.append(f"    prompt: {prompt}")
            task_resources = task.get("resources") or []
            if isinstance(task_resources, list):
                for resource in task_resources:
                    if isinstance(resource, dict):
                        lines.append(
                            "    {name}: {observed}/{target} ({points}/{max_points})".format(
                                name=resource.get("display_name") or resource.get("key"),
                                observed=resource.get("observed"),
                                target=resource.get("target_count"),
                                points=resource.get("points"),
                                max_points=resource.get("max_points"),
                            )
                        )
    report = run_dir / "report.json"
    if report.is_file():
        lines.append(f"report: {report} ({_bytes(report.stat().st_size)})")
    lines.append(f"run file: {payload_path}")
    return lines


def valid_run_id(agent_run_id: str) -> bool:
    return bool(_RUN_ID_RE.fullmatch(agent_run_id))


def find_saved_run(root: Path, agent_run_id: str) -> Path | None:
    """Return the local run directory saved for a public run id."""
    wanted = run_dirname(agent_run_id)
    agents = root / "agents"
    if not agents.is_dir():
        return None
    for run_dir in agents.glob("*/*/runs/*"):
        if not run_dir.is_dir() or run_dir.name != wanted:
            continue
        run_file = run_dir / "run.json"
        if run_file.is_file():
            payload = _read(run_file)
            if isinstance(payload, dict) and payload.get("agent_run_id") not in (None, agent_run_id):
                continue
        return run_dir
    return None


_EXTRACT_VIEW = (
    Path(__file__).resolve().parents[1]
    / "neverplayalone_bench"
    / "dashboard"
    / "player"
    / "extract_view.js"
)


def _current_view(target: Path, recording: Path) -> bool:
    if not target.is_file() or target.stat().st_size < 16 or target.stat().st_mtime < recording.stat().st_mtime:
        return False
    with target.open("rb") as stream:
        return stream.read(16).startswith(b'{"version":2')


def build_browser_view(recording: Path) -> Path:
    """Decode one .mcpr into the compact JSON the browser player renders."""
    target = recording.with_name(f"{recording.stem}.view.json")
    if _current_view(target, recording):
        return target
    if not _EXTRACT_VIEW.is_file():
        raise RuntimeError("browser player extractor is missing")
    try:
        completed = subprocess.run(
            ["node", str(_EXTRACT_VIEW), str(recording), str(target)],
            check=False,
            capture_output=True,
            text=True,
            timeout=180,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("reading the recording took too long") from exc
    if completed.returncode != 0 or not target.is_file():
        detail = (completed.stderr or completed.stdout or "could not read the recording").strip()
        raise RuntimeError(detail[-1500:])
    return target


def resolve_recording(root: Path, agent_run_id: str, filename: str) -> Path | None:
    """Return one saved task recording, rejecting any other filename."""
    if not valid_run_id(agent_run_id) or not _MCPR_NAME.fullmatch(filename):
        return None
    run_dir = find_saved_run(root, agent_run_id)
    if run_dir is None:
        return None
    path = (run_dir / filename).resolve()
    if path.parent != run_dir.resolve() or not path.is_file():
        return None
    return path


def find_standing(root: Path, agent_run_id: str) -> tuple[dict[str, Any], dict[str, Any]] | None:
    rounds = root / "data" / "rounds"
    if not rounds.is_dir():
        return None
    for path in rounds.glob("*.json"):
        row = _read(path)
        if not isinstance(row, dict):
            continue
        for standing in row.get("standings") or []:
            if not isinstance(standing, dict):
                continue
            for validator in standing.get("per_validator") or []:
                if isinstance(validator, dict) and validator.get("agent_run_id") == agent_run_id:
                    return row, standing
    return None


def _sections(report: str) -> list[tuple[str, str]]:
    lines = report.splitlines()
    sections: list[tuple[str, list[str]]] = [("Replay", [])]
    index = 0
    while index < len(lines):
        title = lines[index]
        underline = lines[index + 1] if index + 1 < len(lines) else ""
        if title and set(underline) == {"-"} and len(underline) == len(title):
            sections.append((title, []))
            index += 2
            continue
        sections[-1][1].append(title)
        index += 1
    return [(title, "\n".join(body).strip()) for title, body in sections if title or any(body)]


def render_run_page(root: Path, agent_run_id: str) -> str | None:
    """HTML replay for one Open run URL. None when the id is unknown."""
    if not valid_run_id(agent_run_id):
        return None
    public_url = RUN_PAGE + agent_run_id
    saved = find_saved_run(root, agent_run_id)
    listed = find_standing(root, agent_run_id)
    if saved is None and listed is None:
        return None
    if saved is not None:
        report = render_replay(saved)
        agent_root = find_agent_root(saved)
        miner = agent_root.name
        manifest_path = agent_root / "manifest.json"
        if manifest_path.is_file():
            manifest = _read(manifest_path)
            if isinstance(manifest, dict) and manifest.get("miner_name"):
                miner = str(manifest["miner_name"])
    else:
        row, standing = listed or ({}, {})
        miner = str(standing.get("miner_name") or "Agent")
        report = "\n".join(
            [
                "Agent replay",
                f"run: {agent_run_id}",
                "",
                "Private crown",
                "-------------",
                f"miner: {miner} uid {standing.get('miner_uid')}",
                f"round: {row.get('round_id')}",
                "This run is listed on the scoreboard. Its code and replay files were not saved.",
            ]
        )
    recordings = sorted(saved.glob("task-*.mcpr")) if saved is not None else []
    run_base = "/runs/" + quote(agent_run_id, safe="")
    if recordings:
        buttons = []
        items = []
        for recording in recordings:
            view_url = run_base + "/" + recording.name.replace(".mcpr", ".view.json")
            task_label = recording.stem.replace("task-", "Task ")
            buttons.append(
                f'<button type="button" class="task" data-src="{html.escape(view_url)}">'
                f"{html.escape(task_label)}</button>"
            )
            size = recording.stat().st_size
            label = recording.name
            if size >= 1024 * 1024:
                label = f"{recording.name}  ({size / (1024 * 1024):.1f} MB)"
            items.append(
                f'<li><a href="{html.escape(run_base + "/" + recording.name)}">{html.escape(label)}</a></li>'
            )
        watch = (
            "<section class=\"card\" data-player>"
            "<h2>Replay</h2>"
            f"<div class=\"tasks\">{''.join(buttons)}</div>"
            "<canvas id=\"stage\"></canvas>"
            "<div class=\"bar\">"
            "<button type=\"button\" id=\"play\">Play</button>"
            "<input id=\"scrub\" type=\"range\" min=\"0\" max=\"1000\" value=\"0\" />"
            "<span id=\"clock\" class=\"mono\">0:00</span>"
            "<label>Speed <select id=\"speed\">"
            "<option value=\"1\">1x</option><option value=\"2\">2x</option>"
            "<option value=\"4\">4x</option><option value=\"8\">8x</option>"
            "</select></label>"
            "</div>"
            "<p id=\"status\" class=\"muted\">Loading the recording.</p>"
            "<details>"
            "<summary>Download for Minecraft</summary>"
            "<p>The browser player shows the validator's recorded blocks and follows the agent. "
            "For the full block world, download a task file into "
            "<span class=\"mono\">.minecraft/replay_recordings/</span> and open "
            "<b>Replay Viewer</b> in Minecraft <b>1.21.11</b> with Fabric.</p>"
            "<ol>"
            "<li>Install Fabric Loader for Minecraft 1.21.11.</li>"
            "<li>Put ReplayMod <span class=\"mono\">1.21.11-2.6.26</span> and Fabric API "
            "<span class=\"mono\">0.141.4+1.21.11</span> in <span class=\"mono\">.minecraft/mods/</span>.</li>"
            "<li>Launch Fabric 1.21.11, open Replay Viewer, and load the downloaded <span class=\"mono\">.mcpr</span>.</li>"
            "<li>Press <b>B</b>, spectate the agent, then press <b>F5</b> for the view behind the agent. "
            "Left Shift returns to the free camera.</li>"
            "</ol>"
            f"<ul>{''.join(items)}</ul>"
            "</details>"
            "</section>"
            "<script type=\"module\" src=\"/player/viewer.js\"></script>"
        )
    elif saved is not None:
        watch = (
            "<section class=\"card\"><h2>Watch in Minecraft</h2>"
            "<p>No <span class=\"mono\">.mcpr</span> recording was saved for this run.</p></section>"
        )
    else:
        watch = ""
    cards = [watch]
    for title, body in _sections(report):
        if not body and title == "Replay":
            continue
        cards.append(
            "<section class=\"card\">"
            f"<h2>{html.escape(title)}</h2>"
            f"<pre>{html.escape(body)}</pre>"
            "</section>"
        )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Replay {html.escape(agent_run_id)}</title>
  <style>
    :root {{
      color-scheme: dark;
      --bg: #141611; --bg-2: #1c2018; --line: #31382c;
      --ink: #f3f1e6; --muted: #b7b39a; --accent: #e2f07a;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Segoe UI", "Helvetica Neue", sans-serif;
      background: radial-gradient(1200px 500px at 10% -10%, #2a331c 0%, transparent 55%), var(--bg);
      color: var(--ink);
    }}
    main {{ width: min(1100px, calc(100% - 32px)); margin: 0 auto; padding: 28px 0 48px; }}
    .tasks {{ display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 12px; }}
    .task, #play, select {{
      background: #262b20; color: var(--ink); border: 1px solid var(--line);
      border-radius: 8px; padding: 6px 10px; font: inherit; cursor: pointer;
    }}
    .task.active {{ background: var(--accent); color: #1a1e12; border-color: var(--accent); }}
    #stage {{
      display: block; width: 100%; height: 520px; border-radius: 10px;
      background: #10140e; border: 1px solid var(--line);
    }}
    .bar {{ display: flex; align-items: center; gap: 12px; margin-top: 12px; }}
    #scrub {{ flex: 1; accent-color: #c5d86a; }}
    details {{ margin-top: 14px; }}
    summary {{ cursor: pointer; color: var(--muted); }}
    a {{ color: var(--accent); }}
    h1 {{ margin: 12px 0 8px; font-size: 28px; letter-spacing: -0.03em; }}
    h2 {{ margin: 0 0 10px; font-size: 16px; }}
    .muted {{ color: var(--muted); }}
    .mono {{ font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }}
    .url {{ overflow-wrap: anywhere; }}
    ol, ul {{ margin: 8px 0 0; padding-left: 20px; }}
    li {{ margin: 6px 0; }}
    .card {{
      background: var(--bg-2);
      border: 1px solid var(--line);
      border-radius: 14px;
      padding: 16px 18px;
      margin-top: 14px;
    }}
    pre {{
      margin: 0;
      white-space: pre-wrap;
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      font-size: 13px;
      line-height: 1.45;
    }}
  </style>
</head>
<body>
  <main>
    <a href="/">Rounds</a>
    <h1>{html.escape(miner)}</h1>
    <p class="muted url">{html.escape(public_url)}</p>
    {"".join(cards)}
  </main>
</body>
</html>
"""


def render_replay(path: Path) -> str:
    root = find_agent_root(path)
    if not root.exists():
        raise FileNotFoundError(f"agent directory does not exist: {path}")
    lines = ["Agent replay", f"directory: {root}"]
    manifest_path = root / "manifest.json"
    manifest: dict[str, Any] = {}
    if manifest_path.is_file():
        loaded = _read(manifest_path)
        if isinstance(loaded, dict):
            manifest = loaded
    if manifest.get("code_private") or manifest.get("skipped"):
        lines.append(_section("Private crown"))
        lines.append(f"miner: {manifest.get('miner_name')} uid {manifest.get('miner_uid')}")
        lines.append(str(manifest.get("skipped_reason") or "Source was not saved."))
        return "\n".join(lines) + "\n"

    lines.append(_section("Identity"))
    lines.append(f"round: {manifest.get('round_id')}")
    lines.append(f"miner: {manifest.get('miner_name')} uid {manifest.get('miner_uid')}")
    lines.append(f"hotkey: {manifest.get('hotkey')}")
    lines.append(f"rank: {manifest.get('rank')}")
    lines.append(f"consensus_score: {manifest.get('consensus_score')}")
    lines.append(f"entry_kind: {manifest.get('entry_kind')}")
    lines.append(f"archive_key: {manifest.get('archive_key')}")

    detail_path = root / "detail.json"
    if detail_path.is_file():
        detail = _read(detail_path)
        if isinstance(detail, dict):
            miner = detail.get("miner") if isinstance(detail.get("miner"), dict) else {}
            stats = detail.get("stats") if isinstance(detail.get("stats"), dict) else {}
            lines.append(_section("Agent detail"))
            lines.append(f"name: {miner.get('name')}  tone: {miner.get('tone')}")
            lines.append(f"hotkey: {miner.get('hotkey')}")
            lines.append(f"joined_at: {miner.get('joined_at')}")
            lines.append(f"model: {miner.get('model')}  version: {miner.get('version')}")
            lines.append(f"is_king: {detail.get('is_king')}")
            if stats:
                lines.append(
                    "stats: rounds={rounds} wins={wins} best={best} avg={avg} "
                    "podiums={podiums} success_rate={rate}".format(
                        rounds=stats.get("rounds_played"),
                        wins=stats.get("wins"),
                        best=stats.get("best_score"),
                        avg=stats.get("avg_score"),
                        podiums=stats.get("podiums"),
                        rate=stats.get("success_rate"),
                    )
                )
            history = detail.get("history") or []
            if isinstance(history, list):
                lines.append(f"history: {len(history)} rounds")
                for item in history:
                    if not isinstance(item, dict):
                        continue
                    lines.append(
                        "  {round_id} rank {rank} score {score} {kind} success={success}".format(
                            round_id=item.get("round_id"),
                            rank=item.get("rank"),
                            score=item.get("consensus_score"),
                            kind=item.get("entry_kind"),
                            success=item.get("success"),
                        )
                    )

    standing_path = root / "standing.json"
    if standing_path.is_file():
        standing = _read(standing_path)
        lines.append(_section("Round standing"))
        lines.append(json.dumps(standing, indent=2, ensure_ascii=False))

    code_dir = root / "code"
    lines.append(_section("Code"))
    lines.append(f"files: {manifest.get('code_files')}  bytes: {manifest.get('code_bytes')}")
    lines.extend(_tree(code_dir) or ["  (no code directory)"])
    archive = root / "submission.tar.gz"
    if archive.is_file():
        lines.append(f"submission archive: {archive} ({_bytes(archive.stat().st_size)})")

    lines.append(_section("Runs"))
    run_parent = root / "runs"
    run_dirs = sorted(path for path in run_parent.iterdir() if path.is_dir()) if run_parent.is_dir() else []
    if not run_dirs:
        lines.append("no runs saved")
    for run_dir in run_dirs:
        lines.append(f"[{run_dir.name}]")
        lines.extend(_format_run(root, run_dir))
        lines.append("")
    if not manifest and not detail_path.is_file():
        lines.append(_section("Directory listing"))
        lines.append("This folder has no archive manifest. Listing files instead.")
        lines.extend(_tree(root) or ["  (empty)"])
    return "\n".join(lines).rstrip() + "\n"
