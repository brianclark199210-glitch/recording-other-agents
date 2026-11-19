#!/usr/bin/env bash
# Archive public Never Play Alone agents, open the round dashboard, or replay one agent folder.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
export PYTHONPATH="${ROOT}${PYTHONPATH:+:${PYTHONPATH}}"

if [[ -x "${ROOT}/neverplayalone_bench/.venv/bin/python" ]]; then
  PY="${ROOT}/neverplayalone_bench/.venv/bin/python"
else
  PY="$(command -v python3)"
fi

usage() {
  cat <<'EOF'
Usage:
  ./run.sh <agent-dir>          Replay every saved fact for that agent folder
  ./run.sh current              Print the live round
  ./run.sh sync [options]       Download Animal Husbandry standings and non-crown artifacts
  ./run.sh dashboard [options]  Serve the replay dashboard under pm2 (0.0.0.0:8780)
  ./run.sh install              Install the neverplayalone_bench environment
  ./run.sh replay <agent-dir>   Same as passing the folder directly

Sync options are passed through:
  --round 2026-11-15   one round
  --previous N         latest N completed rounds (default 1)
  --all                every completed round
  --skip-mcpr          code and detail only
  --workers N          parallel agent downloads

Only Animal Husbandry rounds are saved. Other missions are removed.
The reigning crown is listed in the dashboard and is not downloaded.

Dashboard options:
  --host 0.0.0.0       bind address (default 0.0.0.0)
  --port 8780          port (default 8780)
EOF
}

DASH_NAME="${NPA_REPLAY_PM2_NAME:-npa-replay}"

require_pm2() {
  command -v pm2 >/dev/null 2>&1 || {
    echo "pm2 not found. Install with: npm install -g pm2" >&2
    exit 1
  }
}

start_dashboard() {
  require_pm2
  local host="0.0.0.0"
  local port="8780"
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --host)
        host="${2:?--host needs a value}"
        shift 2
        ;;
      --port)
        port="${2:?--port needs a value}"
        shift 2
        ;;
      *)
        echo "Unknown dashboard option: $1" >&2
        exit 2
        ;;
    esac
  done
  export REPLAY_DASHBOARD_HOST="$host"
  export REPLAY_DASHBOARD_PORT="$port"
  local pid=""
  pid="$(pm2 pid "$DASH_NAME" 2>/dev/null || true)"
  if [[ "$pid" =~ ^[1-9][0-9]*$ ]]; then
    echo "pm2 ${DASH_NAME} already online (pid ${pid})"
  else
    if pm2 describe "$DASH_NAME" >/dev/null 2>&1; then
      pm2 delete "$DASH_NAME" >/dev/null
    fi
    pm2 start "$ROOT/ecosystem.config.cjs" --only "$DASH_NAME" --update-env
  fi
  local i
  for i in $(seq 1 20); do
    if curl -fsS --max-time 2 "http://127.0.0.1:${port}/" >/dev/null 2>&1; then
      echo "Replay dashboard at http://127.0.0.1:${port}"
      if [[ "$host" == "0.0.0.0" || "$host" == "::" ]]; then
        local public_ip=""
        public_ip="$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for (i = 1; i <= NF; i++) if ($i == "src") { print $(i + 1); exit }}')"
        if [[ -n "$public_ip" ]]; then
          echo "Replay dashboard at http://${public_ip}:${port}"
        fi
      else
        echo "Replay dashboard at http://${host}:${port}"
      fi
      pm2 save >/dev/null
      return 0
    fi
    sleep 0.5
  done
  echo "Replay dashboard did not answer on port ${port}" >&2
  pm2 logs "$DASH_NAME" --lines 40 --nostream || true
  exit 1
}

install_bench() {
  local bench="${ROOT}/neverplayalone_bench"
  python3 -m venv "${bench}/.venv"
  "${bench}/.venv/bin/pip" install -U pip
  "${bench}/.venv/bin/pip" install -e "${bench}[dev]"
  local dir
  for dir in tools/recorder docker/agent agents/log_gatherer agents/llm_agent agents/random_breaker; do
    if [[ -f "${bench}/${dir}/package.json" ]]; then
      echo "npm install ${dir}"
      (cd "${bench}/${dir}" && npm install --no-audit --no-fund)
    fi
  done
  mkdir -p "${ROOT}/data"
  "${bench}/.venv/bin/python" - <<PY
import json, platform, shutil
from pathlib import Path
out = {
    "python": platform.python_version(),
    "node": "$(node --version 2>/dev/null || echo missing)",
    "npm": "$(npm --version 2>/dev/null || echo missing)",
    "docker": "$(docker --version 2>/dev/null || echo missing)",
    "npabench": "editable install in neverplayalone_bench/.venv",
    "bench": "${bench}",
}
Path("${ROOT}/data/environment.json").write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
print(json.dumps(out, indent=2))
PY
  echo "Bench environment is ready. Activate it with: source neverplayalone_bench/.venv/bin/activate"
}

if [[ $# -eq 0 ]]; then
  usage
  exit 0
fi

if [[ -d "$1" ]]; then
  exec "$PY" -m npa_archive replay "$1"
fi

cmd="$1"
shift
case "$cmd" in
  -h|--help|help)
    usage
    ;;
  current)
    exec "$PY" -m npa_archive current "$@"
    ;;
  sync)
    exec "$PY" -m npa_archive sync "$@"
    ;;
  dashboard|serve)
    start_dashboard "$@"
    ;;
  replay)
    if [[ $# -lt 1 || ! -d "$1" ]]; then
      echo "replay needs an agent directory path" >&2
      exit 2
    fi
    exec "$PY" -m npa_archive replay "$1"
    ;;
  install)
    install_bench
    ;;
  *)
    echo "Unknown command: $cmd" >&2
    usage >&2
    exit 2
    ;;
esac
