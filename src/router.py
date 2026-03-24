"""Roteador de mensagens — despacha para handlers por tipo."""

from typing import Callable

from src.dto.message import Message, MessageType


class MessageRouter:
    """Despacha mensagens recebidas para funções callback registradas.

    Funciona como um dicionário {MessageType: handler}.
    Exemplo de uso::

        router = MessageRouter()
        router.register(MessageType.CHAT, meu_handler_de_chat)
        router.dispatch(mensagem)  # chama meu_handler_de_chat(mensagem)
    """

    def __init__(self):
        self._handlers: dict[MessageType, Callable[[Message], None]] = {}

    def register(self, msg_type: MessageType, handler: Callable[[Message], None]) -> None:
        """Registra um handler (callback) para um tipo de mensagem."""
        self._handlers[msg_type] = handler

    def dispatch(self, message: Message) -> None:
        """Despacha a mensagem para o handler correspondente ao seu tipo.

        Se não houver handler registrado para o tipo, a mensagem é ignorada
        com um aviso no terminal.
        """
        handler = self._handlers.get(message.type)
        if handler is not None:
            handler(message)
        else:
            print(f"[Router] Nenhum handler para tipo {message.type.name}, ignorando.")
