# Never Play Alone round archive

This project downloads public [Never Play Alone](https://dashboard.neverplayalone.ai/rounds) **Animal Husbandry** rounds, stores each day's agents on disk, and opens a local dashboard where a round row expands into that round's agent list. Crafting, Combat, Farming, and every other mission are left out. The reigning crown is marked and is not downloaded: the subnet keeps the current champion's source private.

The Minecraft harness lives in [`neverplayalone_bench`](neverplayalone_bench). This archive does not re-score agents. It saves the public code, run detail, and ReplayMod `.mcpr` files so you can inspect what a validator already ran.

## What it stores

| Source | Used for |
| --- | --- |
| `GET /api/v1/rounds/current` | The live round shown at the top of the dashboard |
| `GET /api/v1/rounds?limit=500` | Animal Husbandry round summaries and their public standings |
| `GET /api/v1/overview/metrics` | The reigning crown (uid, name, rounds as king) |
| `GET /api/v1/agents/{uid}` | Public detail for every agent except the crown |
| `GET /api/v1/agents/{uid}/code` | Source files for every agent except the crown |
| `GET /api/v1/agent-runs/{round}:{validator}:{entry}` | Score breakdown, task list, and `.mcpr` links |

The dashboard site `https://dashboard.neverplayalone.ai/rounds/2026-11-15` and a run page such as `https://dashboard.neverplayalone.ai/runs/2026-11-15:0:44118d313a224478bf5e26a7300eaa50` are the human view of those same API records. Presigned download URLs are used once and are not kept in the saved JSON.

A round's crown is `champion_uid_after` on that round. The reigning crown is the overview king. While that miner reigns, their code, detail file, and replays are skipped. Their public standing (name, rank, score) still appears, because the scoreboard itself is public.

## Layout

```text
recording-agent/
  run.sh                         # sync, dashboard, install, or replay a folder
  npa_archive/                   # Python package (stdlib only)
  agents/<round-id>/uid-<uid>-<name>/
    manifest.json                # index used by replay
    detail.json                  # agent profile and history
    standing.json                # this round's scoreboard row
    code/                        # extracted source
    submission.tar.gz            # original submission archive, when the API offers one
    code_meta.json
    runs/<run-id>/
      run.json
      report.json
      task-0.mcpr ... task-N.mcpr
  data/
    state.json                   # current round + reigning crown
    rounds/<round-id>.json       # public round payload
    environment.json             # versions recorded by ./run.sh install
  neverplayalone_bench/          # upstream harness, installed in .venv
    dashboard/                   # local round browser
```

`./run.sh sync` keeps only **Animal Husbandry** rounds. Crafting, Combat, Farming, and any other mission are not written, and files already on disk for those missions are deleted. By default it downloads code, detail, and `.mcpr` files for the latest completed Animal Husbandry round only. Replay files are large (often 60–80 MB per task, and a round has several tasks per agent).

## Quick start

```bash
./run.sh install
./run.sh current
./run.sh sync
./run.sh dashboard
```

`./run.sh dashboard` starts the page under pm2 as `npa-replay`, bound to `0.0.0.0:8780`. Open http://127.0.0.1:8780 on this machine, or `http://<this-host>:8780` from another machine. The page opens with the live round, mission, and reigning crown, then a paged round table (10 rows at a time). Click a round to expand rank, score, entry kind, code/replay status, and a **Crown** badge on that round's champion. A second **reigning** badge means that agent is the current private champion.

Replay one saved agent (any nested path under that agent's folder works):

```bash
./run.sh agents/2026-11-15/uid-182-miner-182
./run.sh replay agents/2026-11-15/uid-182-miner-182/code
```

The report includes identity, career history, the round standing, the code tree, and every task score plus the local `.mcpr` size.

## Sync options

```bash
./run.sh sync --round 2026-11-15
./run.sh sync --previous 3
./run.sh sync --previous 7 --skip-mcpr
./run.sh sync --all --skip-mcpr
./run.sh sync --all
```

`--all` downloads every completed Animal Husbandry round. With `.mcpr` files that is still a large download, so use `--skip-mcpr` unless you have the disk and the time. Re-running sync skips replay files that are already on disk. A `--round` id from another mission is rejected.

## Bench environment

`./run.sh install` creates `neverplayalone_bench/.venv`, installs the package with its dev extras, and runs `npm install` for:

- `tools/recorder` (writes `.mcpr` packet logs)
- `docker/agent` (sandboxed mineflayer runtime)
- `agents/log_gatherer`, `agents/llm_agent`, `agents/random_breaker`

The archive package needs Python 3.11 or newer. The machine used for this setup has Python 3.14, Node 22, and Docker. A real `npabench run` still needs Docker, because the harness starts a Paper 1.21.11 server and a sandbox container per agent. Activate the environment with:

```bash
source neverplayalone_bench/.venv/bin/activate
npabench run log_gatherer=neverplayalone_bench/agents/log_gatherer \
  --mission resource_gathering \
  --seed 42
```

The example agent path in the upstream README (`examples/agents/log_gatherer`) is `neverplayalone_bench/agents/log_gatherer` in this checkout.

## Dashboard

The server is `neverplayalone_bench/dashboard/server.py`. It reads `data/rounds` and `agents` and does not call the network. `./run.sh dashboard` keeps it running with pm2 (`ecosystem.config.cjs`, app name `npa-replay`). The default bind is `0.0.0.0:8780`.

## Tests

```bash
PYTHONPATH=. neverplayalone_bench/.venv/bin/pytest tests -q
```

## Privacy

Do not point this tool at a private API token or a non-public bucket. It only uses the public read API. If `/api/v1/agents/{uid}/code` says `code_private`, nothing from that response is written. The reigning crown is skipped before that request, even when a round standing names them.
