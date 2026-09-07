"""Run the API and four private MCP processes for a local synthetic demo."""

import os
import signal
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    os.chdir(ROOT)
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], check=True)
    subprocess.run([sys.executable, "-m", "mock_bank.seed_data.seed"], check=True)
    subprocess.run([sys.executable, "-m", "knowledge_base.ingestion.store"], check=True)
    processes: list[subprocess.Popen] = []

    def stop(*_):
        for p in processes:
            p.terminate()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        for group, port in [
            ("accounts", 8101),
            ("transactions", 8102),
            ("services", 8103),
            ("knowledge", 8104),
        ]:
            processes.append(
                subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "uvicorn",
                        f"mcp_servers.{group}_server.app:app",
                        "--host",
                        "127.0.0.1",
                        "--port",
                        str(port),
                        "--no-access-log",
                    ]
                )
            )
        processes.append(
            subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "apps.banking_api.main:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    os.environ.get("PORT", "8000"),
                    "--no-access-log",
                ]
            )
        )
        print(
            f"SecureBank: http://127.0.0.1:{os.environ.get('PORT', '8000')}/assistant — synthetic demo only",
            flush=True,
        )
        processes[-1].wait()
    finally:
        stop()
        for p in processes:
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()


if __name__ == "__main__":
    main()
