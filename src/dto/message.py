"""Modelo de dados de mensagem e enum de tipos."""

import uuid
import time
from enum import Enum

from pydantic import BaseModel, Field


class MessageType(str, Enum):
    """Tipos de mensagem suportados pelo protocolo."""

    CHAT = "CHAT"
    ACK = "ACK"


class Message(BaseModel):
    """Representa uma mensagem trafegada na rede.

    Cada mensagem possui um ID único (UUID), remetente, destinatário,
    corpo de texto, timestamp e tipo (CHAT ou ACK).
    """

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    sender: tuple[str, int]
    sender_name: str = ""
    receiver: tuple[str, int]
    body: str
    type: MessageType = MessageType.CHAT
    timestamp: float = Field(default_factory=time.time)

    def to_bytes(self) -> bytes:
        """Serializa a mensagem para bytes (JSON codificado em UTF-8)."""
        return self.model_dump_json().encode("utf-8")

    @classmethod
    def from_bytes(cls, data: bytes) -> "Message":
        """Desserializa bytes (JSON UTF-8) em uma instância de Message."""
        return cls.model_validate_json(data.decode("utf-8"))
