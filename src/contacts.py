"""Gerenciamento de contatos conhecidos."""


class Contact:
    """Modelo de um contato na rede.

    Attributes:
        name: Apelido do contato.
        addr: Tupla (host, port) do contato.
        online: Indica se o contato está online (usado futuramente pelo Heartbeat).
    """

    def __init__(self, name: str, addr: tuple[str, int], online: bool = False):
        self.name = name
        self.addr = tuple(addr)
        self.online = online

    def __repr__(self) -> str:
        status = "ON" if self.online else "OFF"
        return f"Contact({self.name!r}, {self.addr}, {status})"


class ContactBook:
    """Lista de contatos conhecidos — CRUD simples sobre um dicionário."""

    def __init__(self):
        self._contacts: dict[str, Contact] = {}

    def add(self, name: str, addr: tuple[str, int]) -> Contact:
        """Adiciona ou atualiza um contato pelo nome."""
        contact = Contact(name, addr)
        self._contacts[name] = contact
        return contact

    def remove(self, name: str) -> None:
        """Remove um contato pelo nome. Ignora se não existir."""
        self._contacts.pop(name, None)

    def get(self, name: str) -> Contact | None:
        """Retorna o contato pelo nome, ou None se não existir."""
        return self._contacts.get(name)

    def get_by_addr(self, addr: tuple[str, int]) -> Contact | None:
        """Busca reversa: retorna o contato pelo endereço, ou None."""
        for contact in self._contacts.values():
            if contact.addr == addr:
                return contact
        return None

    def list_all(self) -> list[Contact]:
        """Retorna todos os contatos cadastrados."""
        return list(self._contacts.values())
