"""Node — fachada principal e entidade na rede P2P."""

import threading
import time
from typing import Callable

from src.transport import UDPTransport
from src.dto.message import Message, MessageType
from src.router import MessageRouter
from src.contacts import Contact, ContactBook
from src.buffer import MessageBuffer

# ── Constantes de configuração ──────────────────────────────────────────────

HEARTBEAT_INTERVAL: float = 5.0   # segundos entre envios de PING
OFFLINE_THRESHOLD: float = 15.0   # segundos sem PONG para marcar offline
ACK_TIMEOUT: float = 2.0          # segundos para esperar ACK antes de retransmitir
MAX_RETRIES: int = 3               # número máximo de retransmissões antes de desistir


class Node:
    """Representa um nó (peer) na rede de chat P2P.

    O Node é a **entidade na rede** — possui endereço (host:port) e
    orquestra transporte, roteamento, contatos e buffer de mensagens.

    Uso básico::

        node = Node("0.0.0.0", 5001)
        node.start()
        node.send(("127.0.0.1", 5002), "Olá!")
        node.stop()

    Callbacks opcionais (substituídos pela TUI)::

        node.on_contact_online   = lambda contact: ...
        node.on_contact_offline  = lambda contact: ...
        node.on_ack_received     = lambda msg_id: ...
        node.on_ack_timeout      = lambda message: ...
        node.on_message_buffered = lambda contact_name, message: ...
        node.on_buffer_flushed   = lambda contact_name, count: ...
    """

    def __init__(self, host: str, port: int, nickname: str = ""):
        self.host = host
        self.port = port
        self.nickname = nickname

        self.transport = UDPTransport()
        self.router = MessageRouter()
        self.contacts = ContactBook()
        self.buffer = MessageBuffer()

        # ── Estado interno de threads ──────────────────────────────────────
        self._running = False
        self._heartbeat_thread: threading.Thread | None = None
        self._ack_thread: threading.Thread | None = None

        # Rastreamento de ACKs pendentes: {msg_id: (message, sent_at, retry_count)}
        self._pending_acks: dict[str, tuple[Message, float, int]] = {}
        self._ack_lock = threading.Lock()

        # ── Callbacks opcionais (injetados pela TUI) ───────────────────────
        self.on_contact_online:   Callable[[Contact], None] | None = None
        self.on_contact_offline:  Callable[[Contact], None] | None = None
        self.on_ack_received:     Callable[[str], None] | None = None
        self.on_ack_timeout:      Callable[[Message], None] | None = None
        self.on_message_buffered: Callable[[str, Message], None] | None = None
        self.on_buffer_flushed:   Callable[[str, int], None] | None = None

        # ── Registro de handlers no roteador ──────────────────────────────
        self.router.register(MessageType.CHAT, self._handle_chat)
        self.router.register(MessageType.ACK,  self._handle_ack)
        self.router.register(MessageType.PING, self._handle_ping)
        self.router.register(MessageType.PONG, self._handle_pong)

    @property
    def addr(self) -> tuple[str, int]:
        """Endereço completo (host, port) deste nó."""
        return (self.host, self.port)

    # ── Ciclo de vida ──────────────────────────────────────────────────────

    def start(self) -> None:
        """Inicia o nó: faz bind no socket, escuta e inicia threads de background."""
        self.transport.bind(self.host, self.port)
        if self.port == 0 and self.transport._sock is not None:
            _, self.port = self.transport._sock.getsockname()
        self.transport.listen(self._on_receive)

        self._running = True

        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop, daemon=True, name="telex-heartbeat"
        )
        self._heartbeat_thread.start()

        self._ack_thread = threading.Thread(
            target=self._ack_check_loop, daemon=True, name="telex-ack-checker"
        )
        self._ack_thread.start()

        print(f"\x1b[33m[Node] Escutando em {self.host}:{self.port}\x1b[0m")

    def stop(self) -> None:
        """Para a escuta, encerra threads de background e fecha o socket."""
        self._running = False
        if self._heartbeat_thread is not None:
            self._heartbeat_thread.join(timeout=2.0)
        if self._ack_thread is not None:
            self._ack_thread.join(timeout=2.0)
        self.transport.close()
        print("\x1b[33m[Node] Encerrado.\x1b[0m")

    # ── Envio ──────────────────────────────────────────────────────────────

    def send(self, dest: tuple[str, int], text: str) -> Message:
        """Envia uma mensagem de chat para o endereço de destino.

        Se o contato estiver offline, a mensagem é armazenada no buffer e
        enviada automaticamente quando o contato reconectar via Heartbeat.

        Returns:
            A instância de Message criada (enviada ou bufferizada).
        """
        msg = Message(
            sender=self.addr,
            sender_name=self.nickname,
            receiver=dest,
            body=text,
        )

        contact = self.contacts.get_by_addr(dest)
        contact_name = contact.name if contact else f"{dest[0]}:{dest[1]}"

        if contact is not None and not contact.online:
            self.buffer.enqueue(contact_name, msg)
            print(f"\x1b[33m[Buffer → {contact_name}] mensagem armazenada (offline)\x1b[0m")
            if self.on_message_buffered is not None:
                self.on_message_buffered(contact_name, msg)
            return msg

        self.transport.send_to(msg.to_bytes(), dest)
        with self._ack_lock:
            self._pending_acks[msg.id] = (msg, time.time(), 0)
        print(f"\x1b[34m[Enviado → {contact_name}] {text}\x1b[0m")
        return msg

    # ── Handlers de mensagens ──────────────────────────────────────────────

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
        """Handler para ACK — confirma entrega de uma mensagem enviada."""
        original_id = message.body
        with self._ack_lock:
            if original_id in self._pending_acks:
                self._pending_acks.pop(original_id)
                if self.on_ack_received is not None:
                    self.on_ack_received(original_id)

    def _handle_ping(self, message: Message) -> None:
        """Handler para PING — responde com PONG imediatamente."""
        sender_addr = tuple(message.sender)

        # Só responde a contatos conhecidos
        contact = self.contacts.get_by_addr(sender_addr)
        if contact is None:
            return

        pong = Message(
            sender=self.addr,
            sender_name=self.nickname,
            receiver=sender_addr,
            body="",
            type=MessageType.PONG,
        )
        try:
            self.transport.send_to(pong.to_bytes(), sender_addr)
        except Exception:
            pass

    def _handle_pong(self, message: Message) -> None:
        """Handler para PONG — atualiza presença e descarrega buffer se necessário."""
        contact = self.contacts.get_by_addr(tuple(message.sender))
        if contact is None:
            return

        was_offline = not contact.online
        contact.last_seen = time.time()
        contact.online = True

        if was_offline:
            self._flush_buffer(contact.name)
            if self.on_contact_online is not None:
                self.on_contact_online(contact)

    # ── Callback interno de recepção ───────────────────────────────────────

    def _on_receive(self, data: bytes, addr: tuple[str, int]) -> None:
        """Callback chamado pelo UDPTransport quando chega um datagrama."""
        try:
            message = Message.from_bytes(data)

            # Auto-ACK: responde a mensagens CHAT imediatamente
            if message.type == MessageType.CHAT:
                ack = Message(
                    sender=self.addr,
                    sender_name=self.nickname,
                    receiver=tuple(message.sender),
                    body=message.id,
                    type=MessageType.ACK,
                )
                try:
                    self.transport.send_to(ack.to_bytes(), tuple(message.sender))
                except Exception:
                    pass

            self.router.dispatch(message)
        except Exception as e:
            print(f"\x1b[31m[Node] Erro ao processar datagrama de {addr}: {e}\x1b[0m")

    # ── Loops de background ────────────────────────────────────────────────

    def _heartbeat_loop(self) -> None:
        """Thread daemon: envia PING a todos os contatos e verifica timeouts."""
        while self._running:
            for contact in self.contacts.list_all():
                # Envia PING
                ping = Message(
                    sender=self.addr,
                    sender_name=self.nickname,
                    receiver=contact.addr,
                    body="",
                    type=MessageType.PING,
                )
                try:
                    self.transport.send_to(ping.to_bytes(), contact.addr)
                except Exception:
                    pass

                # Verifica timeout de presença
                if contact.online and contact.last_seen > 0:
                    if time.time() - contact.last_seen > OFFLINE_THRESHOLD:
                        contact.online = False
                        if self.on_contact_offline is not None:
                            self.on_contact_offline(contact)

            time.sleep(HEARTBEAT_INTERVAL)

    def _ack_check_loop(self) -> None:
        """Thread daemon: verifica ACKs pendentes, retransmite ou desiste."""
        while self._running:
            now = time.time()
            timed_out: list[str] = []

            with self._ack_lock:
                for msg_id, (msg, sent_at, retries) in list(self._pending_acks.items()):
                    if now - sent_at >= ACK_TIMEOUT:
                        if retries < MAX_RETRIES:
                            try:
                                self.transport.send_to(msg.to_bytes(), tuple(msg.receiver))
                            except Exception:
                                pass
                            self._pending_acks[msg_id] = (msg, now, retries + 1)
                        else:
                            timed_out.append(msg_id)

                for msg_id in timed_out:
                    msg, _, _ = self._pending_acks.pop(msg_id)
                    if self.on_ack_timeout is not None:
                        self.on_ack_timeout(msg)

            time.sleep(1.0)

    # ── Buffer ─────────────────────────────────────────────────────────────

    def _flush_buffer(self, contact_name: str) -> None:
        """Descarrega mensagens pendentes do buffer para um contato que reconectou."""
        contact = self.contacts.get(contact_name)
        if contact is None:
            return

        messages = self.buffer.flush(contact_name)
        count = len(messages)
        for msg in messages:
            try:
                self.transport.send_to(msg.to_bytes(), contact.addr)
                with self._ack_lock:
                    self._pending_acks[msg.id] = (msg, time.time(), 0)
            except Exception:
                pass

        if count > 0 and self.on_buffer_flushed is not None:
            self.on_buffer_flushed(contact_name, count)
