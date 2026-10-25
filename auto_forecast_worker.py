from __future__ import annotations

import socket
import time
from pathlib import Path

import yaml

from quant_mm.auto_forecast import (
    load_auto_settings,
    run_auto_forecast_cycle,
    save_auto_status,
)

CONFIG_PATH = Path("config_forecast.yaml")
SINGLETON_HOST = "127.0.0.1"
SINGLETON_PORT = 8766


def load_project_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def acquire_singleton() -> socket.socket | None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind((SINGLETON_HOST, SINGLETON_PORT))
        sock.listen(1)
        return sock
    except OSError:
        sock.close()
        return None


def main() -> int:
    singleton = acquire_singleton()
    if singleton is None:
        return 0

    next_run = 0.0
    was_enabled = False
    try:
        while True:
            config = load_project_config()
            results_dir = str(config.get("results_dir", "results/forecast"))
            settings = load_auto_settings(results_dir)
            enabled = bool(settings.get("enabled", False))

            if not enabled:
                if was_enabled:
                    save_auto_status(
                        results_dir,
                        {
                            "enabled": False,
                            "worker_running": True,
                            "message": "自动预测已关闭，后台服务保持待命。",
                        },
                    )
                was_enabled = False
                next_run = 0.0
                time.sleep(5)
                continue

            now = time.monotonic()
            if not was_enabled:
                next_run = 0.0
            was_enabled = True

            if now >= next_run:
                result = run_auto_forecast_cycle(
                    settings,
                    config,
                    results_dir=results_dir,
                )
                result["worker_running"] = True
                result["settings"] = settings
                save_auto_status(results_dir, result)
                next_run = time.monotonic() + int(settings.get("poll_seconds", 60))

            time.sleep(5)
    finally:
        singleton.close()


if __name__ == "__main__":
    raise SystemExit(main())
