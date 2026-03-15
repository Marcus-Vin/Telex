"""Camada de transporte UDP — ferramenta de envio e recebimento de dados."""

import socket
import threading
from typing import Callable


class UDPTransport:
    """Encapsula um socket UDP para envio e escuta de datagramas.

    Esta classe é uma **ferramenta sem estado próprio de endereço**.
    O endereço (host, port) é fornecido pelo Node via ``bind()``.
    """

    BUFFER_SIZE = 4096  # tamanho máximo do datagrama recebido

    def __init__(self):
        self._sock: socket.socket | None = None
        self._listening = False
        self._listen_thread: threading.Thread | None = None

    def bind(self, host: str, port: int) -> None:
        """Cria o socket UDP e faz bind no endereço fornecido pelo Node."""
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.bind((host, port))
        self._sock.settimeout(1.0)  # permite verificar _listening periodicamente

    def send_to(self, data: bytes, addr: tuple[str, int]) -> None:
        """Envia dados brutos para o endereço de destino."""
        if self._sock is None:
            raise RuntimeError("Transport não está ligado. Chame bind() primeiro.")
        self._sock.sendto(data, addr)

    def listen(self, callback: Callable[[bytes, tuple[str, int]], None]) -> None:
        """Inicia uma thread que escuta datagramas e repassa ao callback.

        Args:
            callback: função chamada com (dados, endereço_remetente) a cada
                      datagrama recebido.
        """
        if self._sock is None:
            raise RuntimeError("Transport não está ligado. Chame bind() primeiro.")
        self._listening = True
        self._listen_thread = threading.Thread(
            target=self._listen_loop,
            args=(callback,),
            daemon=True,
        )
        self._listen_thread.start()

    def _listen_loop(self, callback: Callable[[bytes, tuple[str, int]], None]) -> None:
        """Loop interno de escuta — roda em thread separada."""
        while self._listening:
            try:
                data, addr = self._sock.recvfrom(self.BUFFER_SIZE)
                callback(data, addr)
            except socket.timeout:
                continue  # volta a checar _listening
            except OSError:
                break  # socket foi fechado

    def close(self) -> None:
        """Para a escuta e fecha o socket."""
        self._listening = False
        if self._listen_thread is not None:
            self._listen_thread.join(timeout=2.0)
        if self._sock is not None:
            self._sock.close()
            self._sock = None
