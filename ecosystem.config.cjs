const path = require("path");

const root = __dirname;
const python = path.join(root, "neverplayalone_bench", ".venv", "bin", "python");
const name = process.env.NPA_REPLAY_PM2_NAME || "npa-replay";
const host = process.env.REPLAY_DASHBOARD_HOST || "0.0.0.0";
const port = String(process.env.REPLAY_DASHBOARD_PORT || "8780");

module.exports = {
  apps: [
    {
      name,
      cwd: root,
      script: python,
      args: `-u -m npa_archive dashboard --host ${host} --port ${port}`,
      interpreter: "none",
      autorestart: true,
      restart_delay: 3000,
      max_restarts: 30,
      time: true,
      env: {
        PYTHONPATH: root,
        REPLAY_DASHBOARD_HOST: host,
        REPLAY_DASHBOARD_PORT: port,
      },
    },
  ],
};
