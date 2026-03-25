"""Telex — Ponto de entrada."""

import argparse

from tui import TelexApp


def main() -> None:
    parser = argparse.ArgumentParser(description="Telex — Chat P2P sobre UDP")
    parser.add_argument("--host", default="0.0.0.0", help="IP para escuta (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=0, help="Porta para escuta (default: 5000)")
    args = parser.parse_args()

    TelexApp(args.host, args.port).run()


if __name__ == "__main__":
    main()
