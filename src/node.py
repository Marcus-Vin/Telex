"""Node — fachada principal e entidade na rede P2P."""

from src.transport import UDPTransport
from src.dto.message import Message, MessageType
from src.router import MessageRouter
from src.contacts import ContactBook
from src.buffer import MessageBuffer


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

        # Registra handlers padrão
        self.router.register(MessageType.CHAT, self._handle_chat)
        self.router.register(MessageType.ACK, self._handle_ack)

    @property
    def addr(self) -> tuple[str, int]:
        """Endereço completo (host, port) deste nó."""
        return (self.host, self.port)

    def start(self) -> None:
        """Inicia o nó: faz bind no socket e começa a escutar."""
        self.transport.bind(self.host, self.port)
        # Atualiza a porta caso tenha sido passada como 0 (porta efêmera)
        if self.port == 0 and self.transport._sock is not None:
            _, self.port = self.transport._sock.getsockname()
        self.transport.listen(self._on_receive)
        print(f"\x1b[33m[Node] Escutando em {self.host}:{self.port}\x1b[0m")

    def stop(self) -> None:
        """Para a escuta e fecha o socket."""
        self.transport.close()
        print("\x1b[33m[Node] Encerrado.\x1b[0m")

    def send(self, dest: tuple[str, int], text: str) -> None:
        """Envia uma mensagem de chat para o endereço de destino."""
        msg = Message(
            sender=self.addr,
            sender_name=self.nickname,
            receiver=dest,
            body=text,
        )
        receiver_name = self.contacts.get_by_addr(dest).name
        if receiver_name is None:
            receiver_name = f"{dest[0]}:{dest[1]}"
        self.transport.send_to(msg.to_bytes(), dest)
        print(f"\x1b[34m[Enviado → {receiver_name}] {text}\x1b[0m")

    # ── Handlers ──────────────────────────────────────────────

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
        """Handler para mensagens do tipo ACK — placeholder para futuro."""
        pass  # TODO: implementar lógica de confirmação de entrega

    # ── Callback interno ──────────────────────────────────────

    def _on_receive(self, data: bytes, addr: tuple[str, int]) -> None:
        """Callback chamado pelo UDPTransport quando chega um datagrama."""
        try:
            message = Message.from_bytes(data)
            self.router.dispatch(message)
        except Exception as e:
            print(f"\x1b[31m[Node] Erro ao processar datagrama de {addr}: {e}\x1b[0m")
