from pathlib import Path

import pytest

from npa_archive.catalog import build_catalog, read_json, write_json
from npa_archive.paths import safe_relative
from npa_archive.replay import (
    find_saved_run,
    render_replay,
    render_run_page,
    resolve_recording,
)
from npa_archive.sync import archive_agent, refresh_public, select_round_ids


def test_safe_relative_rejects_escape() -> None:
    assert safe_relative("./src/index.js") == Path("src/index.js")
    with pytest.raises(ValueError):
        safe_relative("../secret.js")


def _husbandry(round_id: str, status: str) -> dict:
    return {
        "round_id": round_id,
        "status": status,
        "mission": {"name": "Animal Husbandry", "slug": "animal_husbandry"},
    }


def test_select_previous_completed_rounds() -> None:
    rounds = [
        _husbandry("2026-11-16", "evaluating"),
        _husbandry("2026-11-15", "completed"),
        _husbandry("2026-11-14", "completed"),
        {"round_id": "2026-11-13", "status": "completed", "mission": {"name": "Combat", "slug": "combat"}},
    ]
    assert select_round_ids(rounds, round_id=None, previous=1, include_all=False) == ["2026-11-15"]
    assert select_round_ids(rounds, round_id="2026-11-14", previous=1, include_all=False) == ["2026-11-14"]
    assert select_round_ids(rounds, round_id=None, previous=None, include_all=True) == [
        "2026-11-15",
        "2026-11-14",
    ]
    with pytest.raises(ValueError):
        select_round_ids(rounds, round_id="2026-11-13", previous=1, include_all=False)


def test_crown_agent_is_not_written(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    destination = tmp_path / "crown"

    def _dir(round_id: str, miner_uid: int, miner_name: str) -> Path:
        return destination

    monkeypatch.setattr("npa_archive.sync.agent_dir", _dir)

    class ExplodingClient:
        def agent(self, miner_uid: int) -> dict:
            raise AssertionError("crown detail must not be requested")

        def agent_code(self, miner_uid: int) -> dict:
            raise AssertionError("crown code must not be requested")

    result = archive_agent(
        ExplodingClient(),
        "2026-11-15",
        {"miner_uid": 244, "miner_name": "Miner 244"},
        244,
        with_mcpr=True,
    )
    assert result["skipped"] is True
    assert not destination.exists()


def test_empty_code_listing_uses_submission_archive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import io
    import tarfile

    destination = tmp_path / "agent"
    monkeypatch.setattr("npa_archive.sync.agent_dir", lambda *_args: destination)
    bundle = io.BytesIO()
    with tarfile.open(fileobj=bundle, mode="w:gz") as archive:
        payload = b"console.log('from-tar')\n"
        info = tarfile.TarInfo("index.js")
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))
    archive_bytes = bundle.getvalue()

    class Client:
        def agent(self, miner_uid: int) -> dict:
            return {"miner": {"uid": miner_uid, "name": "Miner 174"}}

        def agent_code(self, miner_uid: int) -> dict:
            return {
                "uid": miner_uid,
                "code_private": False,
                "files": [],
                "download_url": "https://example.test/submission.tar.gz",
                "archive_key": "submissions/demo.tar.gz",
            }

        def download(self, url: str, dest_tmp) -> None:
            dest_tmp.write(archive_bytes)

        def agent_run(self, agent_run_id: str) -> dict:
            return {"agent_run_id": agent_run_id, "score": 1, "tasks": [], "report": {}}

    result = archive_agent(
        Client(),
        "2026-11-15",
        {
            "miner_uid": 174,
            "miner_name": "Miner 174",
            "rank": 8,
            "consensus_score": 1,
            "entry_kind": "submission",
            "per_validator": [{"agent_run_id": "2026-11-15:0:demo"}],
        },
        crown_uid=244,
        with_mcpr=False,
    )
    assert result["code_private"] is False
    assert (destination / "code" / "index.js").read_text(encoding="utf-8") == "console.log('from-tar')\n"


def test_catalog_marks_round_crown(tmp_path: Path) -> None:
    write_json(
        tmp_path / "data" / "state.json",
        {"crown": {"uid": 244, "name": "Miner 244"}, "current_round": {"round_id": "2026-11-16", "status": "evaluating"}},
    )
    write_json(
        tmp_path / "data" / "rounds" / "2026-11-15.json",
        {
            "round_id": "2026-11-15",
            "round": 101,
            "label": "2026-11-15",
            "status": "completed",
            "mission": {"name": "Animal Husbandry", "slug": "animal_husbandry"},
            "winner_uid": 244,
            "winner_name": "Miner 244",
            "winner_score": 50.8,
            "champion_uid_after": 244,
            "champion_name_after": "Miner 244",
            "standings": [
                {
                    "miner_uid": 244,
                    "miner_name": "Miner 244",
                    "rank": 1,
                    "consensus_score": 50.8,
                    "entry_kind": "submission",
                    "per_validator": [{"status": "timeout"}],
                },
                {
                    "miner_uid": 182,
                    "miner_name": "Miner 182",
                    "rank": 2,
                    "consensus_score": 45.8,
                    "entry_kind": "submission",
                    "per_validator": [
                        {
                            "status": "timeout",
                            "agent_run_id": "2026-11-15:0:8458a9801e434d2192127dd32253482c",
                        }
                    ],
                },
            ],
        },
    )
    write_json(
        tmp_path / "agents" / "2026-11-15" / "uid-182-miner-182" / "manifest.json",
        {
            "miner_uid": 182,
            "code_files": 3,
            "runs": [{"dir": "runs/one", "mcpr": ["runs/one/task-0.mcpr"]}],
        },
    )
    write_json(
        tmp_path / "agents" / "2026-11-15" / "uid-182-miner-182" / "runs" / "one" / "run.json",
        {
            "tasks": [
                {"task_index": 0, "score": 25.0, "status": "timeout"},
                {"task_index": 1, "score": 66.6, "status": "timeout"},
            ]
        },
    )
    catalog = build_catalog(tmp_path)
    agents = {agent["uid"]: agent for agent in catalog["rounds"][0]["agents"]}
    assert agents[244]["is_round_champion"] is True
    assert agents[244]["is_reigning_crown"] is True
    assert agents[244]["code"] == "private"
    assert agents[182]["code"] == "saved"
    assert agents[182]["mcpr_count"] == 1
    assert agents[182]["is_round_champion"] is False
    assert agents[182]["run_url"] == (
        "https://dashboard.neverplayalone.ai/runs/2026-11-15:0:8458a9801e434d2192127dd32253482c"
    )
    assert [task["score"] for task in agents[182]["tasks"]] == [25.0, 66.6]


def test_refresh_public_replaces_finished_round(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rounds = tmp_path / "data" / "rounds"
    rounds.mkdir(parents=True)
    write_json(
        rounds / "2026-11-16.json",
        {
            "round_id": "2026-11-16",
            "status": "evaluating",
            "mission": {"name": "Animal Husbandry", "slug": "animal_husbandry"},
            "standings": [],
            "artifact_sync": "summary",
        },
    )
    write_json(
        rounds / "2026-11-15.json",
        {
            "round_id": "2026-11-15",
            "status": "completed",
            "mission": {"name": "Animal Husbandry", "slug": "animal_husbandry"},
            "standings": [],
            "artifact_sync": "full",
        },
    )
    write_json(
        rounds / "2026-10-07.json",
        {"round_id": "2026-10-07", "status": "completed", "mission": {"name": "Farming", "slug": "farming"}},
    )
    monkeypatch.setattr("npa_archive.sync.ROUNDS_DIR", rounds)
    monkeypatch.setattr("npa_archive.sync.STATE_PATH", tmp_path / "data" / "state.json")
    monkeypatch.setattr("npa_archive.sync.AGENTS_DIR", tmp_path / "agents")

    class Client:
        base_url = "https://api.example"

        def current_round(self) -> dict:
            return {
                "round_id": "2026-11-17",
                "round": 103,
                "label": "2026-11-17",
                "status": "evaluating",
                "mission": {"name": "Animal Husbandry", "slug": "animal_husbandry"},
                "current_block": 10,
                "start_block": 1,
                "end_block": 20,
            }

        def overview(self) -> dict:
            return {"king": {"uid": 124, "name": "Miner 124", "rounds_as_king": 1, "score": 60.8}}

        def rounds(self) -> list:
            return [
                {
                    "round_id": "2026-11-17",
                    "status": "evaluating",
                    "mission": {"name": "Animal Husbandry", "slug": "animal_husbandry"},
                    "standings": [],
                },
                {
                    "round_id": "2026-11-16",
                    "status": "completed",
                    "mission": {"name": "Animal Husbandry", "slug": "animal_husbandry"},
                    "winner_name": "Miner 124",
                    "winner_score": 60.8,
                    "standings": [{"miner_uid": 124, "miner_name": "Miner 124", "rank": 1, "consensus_score": 60.8}],
                },
                {
                    "round_id": "2026-11-15",
                    "status": "completed",
                    "mission": {"name": "Animal Husbandry", "slug": "animal_husbandry"},
                    "standings": [],
                },
            ]

        def agent_code(self, miner_uid: int) -> dict:
            return {"code_private": True, "reason": "reigning"}

    result = refresh_public(Client())
    finished = read_json(rounds / "2026-11-16.json")
    assert result["current_round"] == "2026-11-17"
    assert result["crown_uid"] == 124
    assert result["latest_completed"] == "2026-11-16"
    assert finished["status"] == "completed"
    assert finished["winner_name"] == "Miner 124"
    assert read_json(rounds / "2026-11-15.json")["artifact_sync"] == "full"
    assert not (rounds / "2026-10-07.json").exists()
    state = read_json(tmp_path / "data" / "state.json")
    assert state["current_round"]["round_id"] == "2026-11-17"
    assert state["crown"]["uid"] == 124


def test_catalog_omits_other_missions(tmp_path: Path) -> None:
    write_json(
        tmp_path / "data" / "rounds" / "2026-11-15.json",
        {
            "round_id": "2026-11-15",
            "status": "completed",
            "mission": {"name": "Animal Husbandry", "slug": "animal_husbandry"},
            "standings": [],
        },
    )
    write_json(
        tmp_path / "data" / "rounds" / "2026-10-07.json",
        {
            "round_id": "2026-10-07",
            "status": "completed",
            "mission": {"name": "Farming", "slug": "farming"},
            "standings": [{"miner_uid": 1, "miner_name": "Miner 1", "rank": 1}],
        },
    )
    catalog = build_catalog(tmp_path)
    assert [row["round_id"] for row in catalog["rounds"]] == ["2026-11-15"]


def test_replay_prints_saved_agent(tmp_path: Path) -> None:
    agent = tmp_path / "uid-182-miner-182"
    code = agent / "code"
    code.mkdir(parents=True)
    (code / "index.js").write_text("console.log('hi')\n", encoding="utf-8")
    write_json(
        agent / "manifest.json",
        {
            "round_id": "2026-11-15",
            "miner_uid": 182,
            "miner_name": "Miner 182",
            "hotkey": "5abc",
            "rank": 2,
            "consensus_score": 45.8,
            "entry_kind": "submission",
            "code_files": 1,
            "code_bytes": 20,
        },
    )
    write_json(
        agent / "detail.json",
        {
            "miner": {"name": "Miner 182", "hotkey": "5abc", "joined_at": "2026-11-15T00:00:00Z"},
            "is_king": False,
            "stats": {"rounds_played": 2, "wins": 0, "best_score": 45.8, "avg_score": 40, "podiums": 1, "success_rate": 0.5},
            "history": [{"round_id": "2026-11-15", "rank": 2, "consensus_score": 45.8, "entry_kind": "submission", "success": False}],
        },
    )
    run = agent / "runs" / "run-1"
    run.mkdir(parents=True)
    write_json(
        run / "run.json",
        {
            "agent_run_id": "2026-11-15:0:abc",
            "status": "timeout",
            "success": False,
            "score": 45.8,
            "max_score": 100,
            "task_prompt": "Raise animals",
            "mission": {"name": "Animal Husbandry"},
            "tasks": [{"task_index": 0, "status": "timeout", "score": 45.8, "task_prompt": "Raise animals", "resources": []}],
        },
    )
    report = render_replay(code)
    assert "Miner 182" in report
    assert "45.8" in report
    assert "index.js" in report
    assert "Raise animals" in report


def test_open_run_page_replays_saved_run(tmp_path: Path) -> None:
    run_id = "2026-11-15:0:6749b72d292c4a1694828c2c0375ea46"
    run = tmp_path / "agents" / "2026-11-15" / "uid-181-miner-181" / "runs" / "2026-11-15_0_6749b72d292c4a1694828c2c0375ea46"
    run.mkdir(parents=True)
    write_json(run / "run.json", {"agent_run_id": run_id, "score": 37.5, "max_score": 100, "tasks": []})
    (run / "task-0.mcpr").write_bytes(b"PK replay")
    write_json(
        run.parents[1] / "manifest.json",
        {"round_id": "2026-11-15", "miner_uid": 181, "miner_name": "Miner 181", "code_files": 0},
    )
    assert find_saved_run(tmp_path, run_id) == run
    page = render_run_page(tmp_path, run_id)
    assert page is not None
    assert "Miner 181" in page
    assert f"https://dashboard.neverplayalone.ai/runs/{run_id}" in page
    assert "37.5" in page
    assert "task-0.mcpr" in page
    assert "/player/viewer.js" in page
    assert "task-0.view.json" in page
    assert "Replay Viewer" in page
    assert resolve_recording(tmp_path, run_id, "task-0.mcpr") == run / "task-0.mcpr"
    assert resolve_recording(tmp_path, run_id, "../secret.mcpr") is None
    assert render_run_page(tmp_path, "../secret") is None
