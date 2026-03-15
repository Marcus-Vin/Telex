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

    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port

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
        self.transport.listen(self._on_receive)
        print(f"[Node] Escutando em {self.host}:{self.port}")

    def stop(self) -> None:
        """Para a escuta e fecha o socket."""
        self.transport.close()
        print("[Node] Encerrado.")

    def send(self, dest: tuple[str, int], text: str) -> None:
        """Envia uma mensagem de chat para o endereço de destino."""
        msg = Message(
            sender=self.addr,
            receiver=dest,
            body=text,
        )
        self.transport.send_to(msg.to_bytes(), dest)
        print(f"[Enviado → {dest[0]}:{dest[1]}] {text}")

    # ── Handlers ──────────────────────────────────────────────

    def _handle_chat(self, message: Message) -> None:
        """Handler para mensagens do tipo CHAT — imprime no terminal."""
        sender = f"{message.sender[0]}:{message.sender[1]}"
        print(f"[Recebido ← {sender}] {message.body}")

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
            print(f"[Node] Erro ao processar datagrama de {addr}: {e}")
