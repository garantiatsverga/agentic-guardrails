"""Точка входа Document Agent (спринт 1).

Запуск:
    python main.py            # веб-сервер http://localhost:8000
    python main.py --cli      # прогон 4 кейсов §12 (для видео)
    python main.py --host 0.0.0.0 --port 8080
"""
from __future__ import annotations

import argparse
import sys


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="document-agent",
        description="Document Agent — спринт 1 (обработка документов)",
    )
    parser.add_argument(
        "--cli", action="store_true",
        help="прогнать 4 кейса §12 и выйти (для видео-демо)",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    if args.cli:
        from document_agent.cli import main as cli_main
        return cli_main()

    from document_agent.web.app import run
    run(host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
