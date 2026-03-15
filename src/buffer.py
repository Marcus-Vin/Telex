"""Buffer de mensagens pendentes para contatos offline."""

from src.dto.message import Message


class MessageBuffer:
    """Fila de mensagens pendentes por contato.

    Quando um contato está offline, as mensagens são enfileiradas aqui.
    Quando o contato reconecta (futuro Heartbeat), as mensagens são
    descarregadas via ``flush()``.
    """

    def __init__(self):
        self._pending: dict[str, list[Message]] = {}

    def enqueue(self, contact_name: str, message: Message) -> None:
        """Enfileira uma mensagem para um contato offline."""
        if contact_name not in self._pending:
            self._pending[contact_name] = []
        self._pending[contact_name].append(message)

    def flush(self, contact_name: str) -> list[Message]:
        """Retorna e limpa todas as mensagens pendentes de um contato."""
        return self._pending.pop(contact_name, [])

    def has_pending(self, contact_name: str) -> bool:
        """Verifica se há mensagens pendentes para um contato."""
        return bool(self._pending.get(contact_name))
