"""Node — fachada principal e entidade na rede P2P."""

import time
import threading
from typing import Callable

from src.transport import UDPTransport
from src.dto.message import Message, MessageType
from src.router import MessageRouter
from src.contacts import ContactBook, Contact
from src.buffer import MessageBuffer

HEARTBEAT_INTERVAL = 5.0
OFFLINE_THRESHOLD = 15.0
ACK_TIMEOUT = 3.0
MAX_RETRIES = 3


class Node:
    """Representa um nó (peer) na rede de chat P2P.

    O Node é a **entidade na rede** — possui endereço (host:port) e
    orquestra transporte, roteamento, contatos e buffer de mensagens.

    Uso básico::

        node = Node("0.0.0.0", 5001)
        node.start()
        node.send(("127.0.0.1", 5002), "Olá!")
        node.stop()
    """

    def __init__(self, host: str, port: int, nickname: str = ""):
        self.host = host
        self.port = port
        self.nickname = nickname

        self.transport = UDPTransport()
        self.router = MessageRouter()
        self.contacts = ContactBook()
        self.buffer = MessageBuffer()

        self._running = False
        self._heartbeat_thread: threading.Thread | None = None
        self._ack_thread: threading.Thread | None = None

        self._pending_acks: dict[str, tuple[Message, float, int]] = {}
        self._ack_lock = threading.Lock()

        self.on_contact_online: Callable[[Contact], None] | None = None
        self.on_contact_offline: Callable[[Contact], None] | None = None
        self.on_ack_received: Callable[[str], None] | None = None
        self.on_ack_timeout: Callable[[Message], None] | None = None
        self.on_message_buffered: Callable[[str, Message], None] | None = None
        self.on_buffer_flushed: Callable[[str, int], None] | None = None

        self.router.register(MessageType.CHAT, self._handle_chat)
        self.router.register(MessageType.ACK, self._handle_ack)
        self.router.register(MessageType.PING, self._handle_ping)
        self.router.register(MessageType.PONG, self._handle_pong)

    @property
    def addr(self) -> tuple[str, int]:
        """Endereço completo (host, port) deste nó."""
        return (self.host, self.port)

    # ── Ciclo de vida ──────────────────────────────────────────

    def start(self) -> None:
        """Inicia o nó: faz bind no socket e começa a escutar."""
        self.transport.bind(self.host, self.port)
        if self.port == 0 and self.transport._sock is not None:
            _, self.port = self.transport._sock.getsockname()
        self.transport.listen(self._on_receive)

        self._running = True

        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop, daemon=True
        )
        self._heartbeat_thread.start()

        self._ack_thread = threading.Thread(
            target=self._ack_check_loop, daemon=True
        )
        self._ack_thread.start()

        print(f"\x1b[33m[Node] Escutando em {self.host}:{self.port}\x1b[0m")

    def stop(self) -> None:
        """Para a escuta e fecha o socket."""
        self._running = False
        if self._heartbeat_thread is not None:
            self._heartbeat_thread.join(timeout=2.0)
        if self._ack_thread is not None:
            self._ack_thread.join(timeout=2.0)
        self.transport.close()
        print("\x1b[33m[Node] Encerrado.\x1b[0m")

    # ── Envio ──────────────────────────────────────────────────

    def send(self, dest: tuple[str, int], text: str) -> Message:
        """Envia uma mensagem de chat para o endereço de destino.

        Se o contato está offline, a mensagem é armazenada no buffer
        e será enviada automaticamente quando ele reconectar.

        Returns:
            A Message criada.
        """
        msg = Message(
            sender=self.addr,
            sender_name=self.nickname,
            receiver=dest,
            body=text,
        )

        contact = self.contacts.get_by_addr(dest)
        contact_name = contact.name if contact else f"{dest[0]}:{dest[1]}"

        if contact and not contact.online:
            self.buffer.enqueue(contact_name, msg)
            if self.on_message_buffered:
                self.on_message_buffered(contact_name, msg)
            return msg

        self.transport.send_to(msg.to_bytes(), dest)
        with self._ack_lock:
            self._pending_acks[msg.id] = (msg, time.time(), 0)
        return msg

    # ── Handlers ───────────────────────────────────────────────

    def _handle_chat(self, message: Message) -> None:
        """Handler para mensagens do tipo CHAT — imprime no terminal."""
        contact = self.contacts.get_by_addr(tuple(message.sender))
        if contact is not None:
            display_name = contact.name
        elif message.sender_name:
            display_name = message.sender_name
        else:
            display_name = f"{message.sender[0]}:{message.sender[1]}"
        print(f"\x1b[32m[{display_name}] {message.body}\x1b[0m")

    def _handle_ack(self, message: Message) -> None:
        """Handler para mensagens do tipo ACK — confirma entrega."""
        original_id = message.body
        with self._ack_lock:
            removed = self._pending_acks.pop(original_id, None)
        if removed is not None and self.on_ack_received:
            self.on_ack_received(original_id)

    def _handle_ping(self, message: Message) -> None:
        """Responde a um PING com PONG e atualiza last_seen do remetente."""
        sender_addr = tuple(message.sender)
        pong = Message(
            sender=self.addr,
            sender_name=self.nickname,
            receiver=sender_addr,
            body="",
            type=MessageType.PONG,
        )
        self.transport.send_to(pong.to_bytes(), sender_addr)

        contact = self.contacts.get_by_addr(sender_addr)
        if contact is not None:
            contact.last_seen = time.time()
            if not contact.online:
                contact.online = True
                self._flush_buffer(contact.name)
                if self.on_contact_online:
                    self.on_contact_online(contact)

    def _handle_pong(self, message: Message) -> None:
        """Atualiza last_seen ao receber PONG; detecta reconexão."""
        contact = self.contacts.get_by_addr(tuple(message.sender))
        if contact is None:
            return
        was_offline = not contact.online
        contact.last_seen = time.time()
        contact.online = True
        if was_offline:
            self._flush_buffer(contact.name)
            if self.on_contact_online:
                self.on_contact_online(contact)

    # ── Callback interno ───────────────────────────────────────

    def _on_receive(self, data: bytes, addr: tuple[str, int]) -> None:
        """Callback chamado pelo UDPTransport quando chega um datagrama."""
        try:
            message = Message.from_bytes(data)
            # Substitui o sender pelo endereço real de origem do datagrama UDP.
            # O sender no payload pode conter "0.0.0.0" (endereço de bind), mas
            # o addr do recvfrom reflete sempre o IP real observado na rede.
            message.sender = addr
            if message.type == MessageType.CHAT:
                self._send_ack(message)
            self.router.dispatch(message)
        except Exception as e:
            print(f"\x1b[31m[Node] Erro ao processar datagrama de {addr}: {e}\x1b[0m")

    def _send_ack(self, original: Message) -> None:
        """Envia ACK de volta ao remetente confirmando recebimento."""
        ack = Message(
            sender=self.addr,
            sender_name=self.nickname,
            receiver=original.sender,
            body=original.id,
            type=MessageType.ACK,
        )
        self.transport.send_to(ack.to_bytes(), tuple(original.sender))

    def ping_contact(self, contact_name: str) -> None:
        """Envia um PING imediato a um contato específico (ex: ao adicioná-lo)."""
        contact = self.contacts.get(contact_name)
        if contact is None:
            return
        try:
            ping = Message(
                sender=self.addr,
                sender_name=self.nickname,
                receiver=contact.addr,
                body="",
                type=MessageType.PING,
            )
            self.transport.send_to(ping.to_bytes(), contact.addr)
        except OSError:
            pass

    # ── Buffer flush ───────────────────────────────────────────

    def _flush_buffer(self, contact_name: str) -> None:
        """Descarrega mensagens pendentes para um contato que reconectou."""
        contact = self.contacts.get(contact_name)
        if not contact:
            return
        messages = self.buffer.flush(contact_name)
        for msg in messages:
            self.transport.send_to(msg.to_bytes(), contact.addr)
            with self._ack_lock:
                self._pending_acks[msg.id] = (msg, time.time(), 0)
        if messages and self.on_buffer_flushed:
            self.on_buffer_flushed(contact_name, len(messages))

    # ── Background threads ─────────────────────────────────────

    def _heartbeat_loop(self) -> None:
        """Envia PING periodicamente a todos os contatos e verifica timeouts."""
        while self._running:
            for contact in self.contacts.list_all():
                try:
                    ping = Message(
                        sender=self.addr,
                        sender_name=self.nickname,
                        receiver=contact.addr,
                        body="",
                        type=MessageType.PING,
                    )
                    self.transport.send_to(ping.to_bytes(), contact.addr)
                except OSError:
                    pass

                if contact.online and contact.last_seen > 0:
                    if time.time() - contact.last_seen > OFFLINE_THRESHOLD:
                        contact.online = False
                        if self.on_contact_offline:
                            self.on_contact_offline(contact)

            for _ in range(int(HEARTBEAT_INTERVAL * 10)):
                if not self._running:
                    return
                time.sleep(0.1)

    def _ack_check_loop(self) -> None:
        """Verifica ACKs pendentes: retransmite ou desiste após max retries."""
        while self._running:
            now = time.time()
            timed_out: list[Message] = []

            with self._ack_lock:
                expired_ids: list[str] = []
                for msg_id, (msg, sent_at, retries) in self._pending_acks.items():
                    if now - sent_at >= ACK_TIMEOUT:
                        if retries < MAX_RETRIES:
                            try:
                                self.transport.send_to(msg.to_bytes(), tuple(msg.receiver))
                            except OSError:
                                pass
                            self._pending_acks[msg_id] = (msg, now, retries + 1)
                        else:
                            expired_ids.append(msg_id)
                for msg_id in expired_ids:
                    msg, _, _ = self._pending_acks.pop(msg_id)
                    timed_out.append(msg)

            for msg in timed_out:
                if self.on_ack_timeout:
                    self.on_ack_timeout(msg)

            for _ in range(10):
                if not self._running:
                    return
                time.sleep(0.1)
