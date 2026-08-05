import { spawn, spawnSync } from "node:child_process";

const corsOrigins = new Set(
  (process.env.OPENTERMINALUI_CORS_ORIGINS || "")
    .split(",")
    .map((origin) => origin.trim())
    .filter(Boolean),
);
for (const origin of [
  "http://localhost:4173",
  "http://127.0.0.1:4173",
  "http://localhost:5173",
  "http://127.0.0.1:5173",
]) {
  corsOrigins.add(origin);
}

const env = {
  ...process.env,
  E2E_BACKEND_MODE: "docker",
  E2E_BACKEND_PORT: process.env.E2E_BACKEND_PORT || "8000",
  E2E_BACKEND_URL: process.env.E2E_BACKEND_URL || "http://127.0.0.1:8000",
  E2E_DEV_AUTH: "1",
  OPENTERMINALUI_CORS_ORIGINS: Array.from(corsOrigins).join(","),
};

const compose = spawnSync("docker", ["compose", "up", "-d", "backend", "redis"], {
  cwd: new URL("..", import.meta.url),
  env,
  shell: process.platform === "win32",
  stdio: "inherit",
});

if (compose.status !== 0) {
  process.exit(compose.status ?? 1);
}

const child = spawn("npx", ["playwright", "test", "--config", "../playwright.config.ts"], {
  cwd: new URL("../frontend", import.meta.url),
  env,
  shell: process.platform === "win32",
  stdio: "inherit",
});

child.on("exit", (code, signal) => {
  if (signal) {
    process.kill(process.pid, signal);
  }
  process.exit(code ?? 1);
});
