"""Telex — Ponto de entrada CLI mínimo para testes."""

import argparse
import threading

from src.node import Node


def input_loop(node: Node) -> None:
    """Loop de input que lê comandos do terminal.

    Comandos disponíveis:
        /add <nome> <host> <porta>   — adiciona um contato
        /send <nome> <mensagem>      — envia mensagem para um contato
        /contacts                    — lista todos os contatos
        /quit                        — encerra o nó
    """
    print("Comandos: /add <nome> <host> <porta> | /send <nome> <msg> | /contacts | /quit")
    print("-" * 60)

    while True:
        try:
            line = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            break

        if not line:
            continue

        parts = line.split()
        cmd = parts[0].lower()

        if cmd == "/quit":
            break

        elif cmd == "/add" and len(parts) >= 4:
            name = parts[1]
            host = parts[2]
            port = int(parts[3])
            node.contacts.add(name, (host, port))
            print(f"[+] Contato '{name}' adicionado ({host}:{port})")

        elif cmd == "/send" and len(parts) >= 3:
            name = parts[1]
            text = " ".join(parts[2:])
            contact = node.contacts.get(name)
            if contact is None:
                print(f"[!] Contato '{name}' não encontrado. Use /add primeiro.")
            else:
                node.send(contact.addr, text)

        elif cmd == "/contacts":
            contacts = node.contacts.list_all()
            if not contacts:
                print("[i] Nenhum contato cadastrado.")
            else:
                for c in contacts:
                    print(f"    {c}")

        else:
            print("[?] Comando desconhecido. Use /add, /send, /contacts ou /quit.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Telex — Chat P2P sobre UDP")
    parser.add_argument("--host", default="0.0.0.0", help="IP para escuta (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=5000, help="Porta para escuta (default: 5000)")
    args = parser.parse_args()

    nickname = input("Digite seu nome/apelido: ").strip()
    while not nickname:
        nickname = input("Nome não pode ser vazio. Digite seu nome/apelido: ").strip()

    node = Node(args.host, args.port, nickname=nickname)
    node.start()

    try:
        input_loop(node)
    finally:
        node.stop()


if __name__ == "__main__":
    main()
