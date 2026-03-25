from __future__ import annotations

import io
import sys
from datetime import datetime

from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, ScrollableContainer, Vertical
from textual.screen import ModalScreen, Screen
from textual.theme import Theme
from textual.widgets import Button, Header, Input, Label, RichLog, Select, Static

from src.contacts import Contact
from src.dto.message import Message, MessageType
from src.node import Node


class _Sink(io.StringIO):
    def write(self, s: str) -> int:  # type: ignore[override]
        return len(s)

    def flush(self) -> None:
        pass


class WelcomeScreen(Screen[str]):
    DEFAULT_CSS = """
    WelcomeScreen {
        align: center middle;
        background: $background;
    }

    #welcome-box {
        width: 48;
        height: auto;
        background: $surface;
        border: double $primary;
        padding: 2 4 3 4;
        align: center middle;
    }

    #logo {
        text-align: center;
        text-style: bold;
        color: $accent;
        padding-bottom: 0;
    }

    #tagline {
        text-align: center;
        color: $text-muted;
        padding-bottom: 2;
    }

    #sep {
        color: $primary-darken-1;
        text-align: center;
        padding-bottom: 1;
    }

    #nick-label {
        color: $text-muted;
        padding-bottom: 0;
    }

    #nick-input {
        margin-bottom: 2;
    }

    #enter-btn {
        width: 100%;
    }

    #welcome-error {
        text-align: center;
        color: $error;
        height: 1;
        margin-top: 1;
    }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="welcome-box"):
            yield Static("T  E  L  E  X", id="logo")
            yield Static("chat p2p · udp", id="tagline")
            yield Static("─" * 34, id="sep")
            yield Label("seu apelido", id="nick-label")
            yield Input(placeholder="ex: Alice", id="nick-input")
            yield Button("Entrar", variant="primary", id="enter-btn")
            yield Static("", id="welcome-error")

    def on_mount(self) -> None:
        self.query_one("#nick-input", Input).focus()

    @on(Button.Pressed, "#enter-btn")
    def on_btn(self) -> None:
        self._submit()

    @on(Input.Submitted, "#nick-input")
    def on_input(self) -> None:
        self._submit()

    def _submit(self) -> None:
        nick = self.query_one("#nick-input", Input).value.strip()
        if not nick:
            self.query_one("#welcome-error", Static).update("escolha um apelido para continuar")
            return
        self.dismiss(nick)


class AddContactModal(ModalScreen[tuple[str, str, int] | None]):
    BINDINGS = [Binding("escape", "cancel", "Cancelar")]

    DEFAULT_CSS = """
    AddContactModal { align: center middle; }

    AddContactModal > Vertical {
        width: 52;
        height: auto;
        background: $surface;
        border: tall $primary;
        padding: 1 2 2 2;
    }
    AddContactModal #modal-title {
        text-align: center;
        text-style: bold;
        color: $accent;
        padding-bottom: 1;
    }
    AddContactModal Label { margin-top: 1; color: $text-muted; }
    AddContactModal #modal-error { color: $error; height: 1; margin-top: 1; }
    AddContactModal #modal-buttons { margin-top: 2; align: right middle; height: 3; }
    AddContactModal #modal-buttons Button { margin-left: 1; }
    """

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label("Adicionar Contato", id="modal-title")
            yield Label("Nome")
            yield Input(placeholder="ex: Bob", id="f-name")
            yield Label("IP")
            yield Input(placeholder="127.0.0.1", id="f-host")
            yield Label("Porta")
            yield Input(placeholder="5000", id="f-port")
            yield Static("", id="modal-error")
            with Horizontal(id="modal-buttons"):
                yield Button("Confirmar", variant="primary", id="confirm")
                yield Button("Cancelar", id="cancel")

    def on_mount(self) -> None:
        self.query_one("#f-name", Input).focus()

    @on(Button.Pressed, "#confirm")
    @on(Input.Submitted)
    def do_confirm(self, _event: object = None) -> None:
        name = self.query_one("#f-name", Input).value.strip()
        host = self.query_one("#f-host", Input).value.strip()
        port_str = self.query_one("#f-port", Input).value.strip()
        err = self.query_one("#modal-error", Static)
        if not name:
            err.update("Nome é obrigatório.")
            return
        if not host:
            err.update("IP é obrigatório.")
            return
        try:
            port = int(port_str)
        except ValueError:
            err.update("Porta deve ser um número inteiro.")
            return
        self.dismiss((name, host, port))

    @on(Button.Pressed, "#cancel")
    def action_cancel(self) -> None:
        self.dismiss(None)


class SendMessageModal(ModalScreen[tuple[str, str] | None]):
    BINDINGS = [Binding("escape", "cancel", "Cancelar")]

    DEFAULT_CSS = """
    SendMessageModal { align: center middle; }

    SendMessageModal > Vertical {
        width: 60;
        height: auto;
        background: $surface;
        border: tall $success;
        padding: 1 2 2 2;
    }
    SendMessageModal #modal-title {
        text-align: center;
        text-style: bold;
        color: $success;
        padding-bottom: 1;
    }
    SendMessageModal Label { margin-top: 1; color: $text-muted; }
    SendMessageModal #modal-error { color: $error; height: 1; margin-top: 1; }
    SendMessageModal #modal-buttons { margin-top: 2; align: right middle; height: 3; }
    SendMessageModal #modal-buttons Button { margin-left: 1; }
    """

    def __init__(self, contacts: list[Contact]) -> None:
        super().__init__()
        self._contacts = contacts

    def compose(self) -> ComposeResult:
        options = [(c.name, c.name) for c in self._contacts]
        with Vertical():
            yield Label("Enviar Mensagem", id="modal-title")
            yield Label("Para")
            yield Select(options, id="f-contact", prompt="Selecione um contato...")
            yield Label("Mensagem")
            yield Input(placeholder="Digite sua mensagem...", id="f-message")
            yield Static("", id="modal-error")
            with Horizontal(id="modal-buttons"):
                yield Button("Enviar", variant="success", id="confirm")
                yield Button("Cancelar", id="cancel")

    def on_mount(self) -> None:
        self.query_one("#f-message", Input).focus()

    @on(Button.Pressed, "#confirm")
    def do_confirm(self) -> None:
        sel = self.query_one("#f-contact", Select)
        msg = self.query_one("#f-message", Input).value.strip()
        err = self.query_one("#modal-error", Static)
        if sel.value is Select.BLANK:
            err.update("Selecione um contato.")
            return
        if not msg:
            err.update("Mensagem não pode estar vazia.")
            return
        self.dismiss((str(sel.value), msg))

    @on(Button.Pressed, "#cancel")
    def action_cancel(self) -> None:
        self.dismiss(None)

    @on(Input.Submitted, "#f-message")
    def on_enter(self) -> None:
        self.do_confirm()


class ContactsModal(ModalScreen[None]):
    BINDINGS = [Binding("escape", "action_close_modal", "Fechar")]

    DEFAULT_CSS = """
    ContactsModal { align: center middle; }

    ContactsModal > Vertical {
        width: 56;
        height: auto;
        max-height: 30;
        background: $surface;
        border: tall $primary;
        padding: 1 2 2 2;
    }
    ContactsModal #modal-title {
        text-align: center;
        text-style: bold;
        color: $accent;
        padding-bottom: 1;
    }
    ContactsModal #contact-list { height: auto; max-height: 20; }
    ContactsModal .contact-row { padding: 0 1; height: 2; }
    ContactsModal #modal-buttons { margin-top: 2; align: center middle; height: 3; }
    """

    def __init__(self, contacts: list[Contact]) -> None:
        super().__init__()
        self._contacts = contacts

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label("Contatos", id="modal-title")
            with ScrollableContainer(id="contact-list"):
                if not self._contacts:
                    yield Static("[dim]Nenhum contato cadastrado.[/dim]", markup=True)
                else:
                    for c in self._contacts:
                        dot = "[bold green]●[/bold green]" if c.online else "[dim]○[/dim]"
                        yield Static(
                            f"{dot}  [bold]{c.name}[/bold]  [dim]{c.addr[0]}:{c.addr[1]}[/dim]",
                            markup=True,
                            classes="contact-row",
                        )
            with Horizontal(id="modal-buttons"):
                yield Button("Fechar", variant="primary", id="close")

    def on_mount(self) -> None:
        self.query_one("#close", Button).focus()

    @on(Button.Pressed, "#close")
    def action_close_modal(self) -> None:
        self.dismiss(None)


class TelexApp(App[None]):
    TITLE = "Telex"

    THEME = Theme(
        name="telex",
        dark=False,
        primary="#8978c0",
        secondary="#d4789a",
        accent="#b0a8d8",
        success="#5aaa78",
        warning="#ca8a04",
        error="#c06080",
        background="#fdfcff",
        surface="#f3eeff",
        panel="#ebe4ff",
    )

    CSS = """
    Screen { background: $background; }
    Header { background: $primary-darken-2; }

    #chat {
        height: 1fr;
        margin: 1 1 0 1;
        padding: 0 1;
        border: tall $primary;
        scrollbar-size: 1 1;
    }

    #toolbar {
        height: 3;
        margin: 0 1;
        align: left middle;
    }

    #status {
        width: 1fr;
        height: 3;
        padding: 0 1;
        content-align: left middle;
        color: $text-muted;
    }

    #btn-add      { margin: 0 0 0 1; }
    #btn-send     { margin: 0 0 0 1; }
    #btn-contacts { margin: 0 0 0 1; }
    #btn-quit     { margin: 0 1 0 1; }

    #cmd {
        margin: 0 1 1 1;
        height: 3;
    }

    Input { border: tall $accent; }
    Input:focus { border: tall $success; }
    """

    BINDINGS = [
        Binding("ctrl+n", "open_add",      "Adicionar", show=True),
        Binding("ctrl+s", "open_send",     "Enviar",    show=True),
        Binding("ctrl+l", "open_contacts", "Contatos",  show=True),
        Binding("ctrl+c", "quit_app",      "Sair",      show=True),
    ]

    def __init__(self, host: str, port: int) -> None:
        super().__init__()
        self.host = host
        self.port = port
        self.node: Node | None = None
        self._original_stdout = sys.stdout

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield RichLog(id="chat", highlight=True, markup=True, wrap=True)
        with Horizontal(id="toolbar"):
            yield Static("", id="status")
            yield Button("＋ Adicionar", id="btn-add",      variant="success")
            yield Button("▶ Enviar",     id="btn-send",     variant="primary")
            yield Button("☰ Contatos",  id="btn-contacts",  variant="default")
            yield Button("✕ Sair",      id="btn-quit",      variant="error")
        yield Input(placeholder="ou digite: /add  /send  /contacts  /quit", id="cmd")

    def on_mount(self) -> None:
        self.register_theme(self.THEME)
        self.theme = "telex"
        self.push_screen(WelcomeScreen(), self._on_nickname)

    def _on_nickname(self, nickname: str) -> None:
        self._original_stdout = sys.stdout
        sys.stdout = _Sink()

        self.node = Node(self.host, self.port, nickname=nickname)
        self.node.start()

        # Substitui handler padrão de CHAT pelo da TUI
        self.node.router.register(MessageType.CHAT, self._on_chat_received)

        # Registra callbacks de eventos de rede para atualização thread-safe da UI
        self.node.on_contact_online   = lambda c: self.call_from_thread(self._on_contact_online, c)
        self.node.on_contact_offline  = lambda c: self.call_from_thread(self._on_contact_offline, c)
        self.node.on_ack_timeout      = lambda m: self.call_from_thread(self._on_ack_timeout, m)
        self.node.on_message_buffered = lambda n, m: self.call_from_thread(self._on_message_buffered, n, m)
        self.node.on_buffer_flushed   = lambda n, c: self.call_from_thread(self._on_buffer_flushed, n, c)

        self.sub_title = f"{nickname}  •  {self.node.host}:{self.node.port}"

        self._show_welcome()
        self._refresh_status()
        self.set_interval(5.0, self._refresh_status)
        self.query_one("#cmd", Input).focus()

    def _show_welcome(self) -> None:
        assert self.node is not None
        log = self.query_one("#chat", RichLog)
        log.write(f"[bold]Bem-vindo ao Telex, {self.node.nickname}![/bold]")
        log.write(f"[dim]ouvindo em {self.node.host}:{self.node.port}[/dim]")
        if not self.node.contacts.list_all():
            log.write("")
            log.write(
                "[dim]Nenhum contato ainda — clique em [/dim]"
                "[bold]＋ Adicionar[/bold][dim] para começar.[/dim]"
            )
        log.write("[dim]─────────────────────────────────────────[/dim]")

    def on_unmount(self) -> None:
        sys.stdout = self._original_stdout

    def _on_chat_received(self, message: Message) -> None:
        self.call_from_thread(self._show_incoming, message)

    def _show_incoming(self, message: Message) -> None:
        assert self.node is not None
        contact = self.node.contacts.get_by_addr(tuple(message.sender))  # type: ignore[arg-type]
        name = (
            contact.name if contact
            else (message.sender_name or f"{message.sender[0]}:{message.sender[1]}")
        )
        t = datetime.fromtimestamp(message.timestamp).strftime("%H:%M")
        self.query_one("#chat", RichLog).write(
            f"[dim]{t}[/dim]  [bold green]{name}[/bold green]  {message.body}"
        )
        self.bell()

    def _show_sent(self, name: str, text: str) -> None:
        t = datetime.now().strftime("%H:%M")
        self.query_one("#chat", RichLog).write(
            f"[dim]{t}[/dim]  [bold cyan]você → {name}[/bold cyan]  {text}"
        )

    def _show_info(self, text: str) -> None:
        self.query_one("#chat", RichLog).write(f"[yellow]{text}[/yellow]")

    def _show_error(self, text: str) -> None:
        self.query_one("#chat", RichLog).write(f"[bold red]{text}[/bold red]")

    # ── Callbacks de eventos de rede ───────────────────────────────────────

    def _on_contact_online(self, contact: "Contact") -> None:
        self._show_info(f"● [bold]{contact.name}[/bold] está online")
        self._refresh_status()

    def _on_contact_offline(self, contact: "Contact") -> None:
        self._show_info(f"○ [bold]{contact.name}[/bold] ficou offline")
        self._refresh_status()

    def _on_ack_timeout(self, message: "Message") -> None:
        assert self.node is not None
        contact = self.node.contacts.get_by_addr(tuple(message.receiver))  # type: ignore[arg-type]
        name = contact.name if contact else f"{message.receiver[0]}:{message.receiver[1]}"
        self._show_error(f"⚠ mensagem para [bold]{name}[/bold] não foi entregue após {3} tentativas")

    def _on_message_buffered(self, contact_name: str, _message: "Message") -> None:
        self._show_info(f"⏸ [bold]{contact_name}[/bold] está offline — mensagem guardada no buffer")

    def _on_buffer_flushed(self, contact_name: str, count: int) -> None:
        noun = "mensagem" if count == 1 else "mensagens"
        self._show_info(f"⟳ {count} {noun} pendente(s) enviada(s) para [bold]{contact_name}[/bold]")

    def _refresh_status(self) -> None:
        if self.node is None:
            return
        contacts = self.node.contacts.list_all()
        if contacts:
            parts = [
                f"[bold green]{c.name}[/bold green]" if c.online else f"[dim]{c.name}[/dim]"
                for c in contacts
            ]
            text = "  ".join(parts)
        else:
            text = "[dim]nenhum contato[/dim]"
        self.query_one("#status", Static).update(f" {text}")

    @on(Button.Pressed, "#btn-add")
    def action_open_add(self) -> None:
        self.push_screen(AddContactModal(), self._handle_add_result)

    @on(Button.Pressed, "#btn-send")
    def action_open_send(self) -> None:
        assert self.node is not None
        contacts = self.node.contacts.list_all()
        if not contacts:
            self._show_error("nenhum contato — adicione um primeiro com ＋ Adicionar")
            return
        self.push_screen(SendMessageModal(contacts), self._handle_send_result)

    @on(Button.Pressed, "#btn-contacts")
    def action_open_contacts(self) -> None:
        assert self.node is not None
        self.push_screen(ContactsModal(self.node.contacts.list_all()))

    @on(Button.Pressed, "#btn-quit")
    def action_quit_app(self) -> None:
        self._graceful_exit()

    def _handle_add_result(self, result: tuple[str, str, int] | None) -> None:
        if result is None:
            return
        assert self.node is not None
        name, host, port = result
        self.node.contacts.add(name, (host, port))
        self._show_info(f"[+] contato '[bold]{name}[/bold]' adicionado  ({host}:{port})")
        self._refresh_status()
        self.query_one("#cmd", Input).focus()

    def _handle_send_result(self, result: tuple[str, str] | None) -> None:
        self.query_one("#cmd", Input).focus()
        if result is None:
            return
        assert self.node is not None
        name, text = result
        contact = self.node.contacts.get(name)
        if contact is None:
            self._show_error(f"contato '{name}' não encontrado")
            return
        self.node.send(contact.addr, text)
        if contact.online:
            self._show_sent(name, text)

    @on(Input.Submitted)
    def handle_command(self, event: Input.Submitted) -> None:
        if self.node is None:
            return
        line = event.value.strip()
        self.query_one("#cmd", Input).clear()
        if not line:
            return

        parts = line.split()
        cmd = parts[0].lower()

        if cmd == "/quit":
            self._graceful_exit()

        elif cmd == "/add":
            if len(parts) < 4:
                self._show_error("uso: /add <nome> <ip> <porta>")
                return
            name, host = parts[1], parts[2]
            try:
                port = int(parts[3])
            except ValueError:
                self._show_error("porta deve ser um número inteiro")
                return
            self.node.contacts.add(name, (host, port))
            self._show_info(f"[+] contato '[bold]{name}[/bold]' adicionado  ({host}:{port})")
            self._refresh_status()

        elif cmd == "/send":
            if len(parts) < 3:
                self._show_error("uso: /send <nome> <mensagem>")
                return
            name = parts[1]
            text = " ".join(parts[2:])
            contact = self.node.contacts.get(name)
            if contact is None:
                self._show_error(f"contato '{name}' não encontrado")
                return
            self.node.send(contact.addr, text)
            if contact.online:
                self._show_sent(name, text)

        elif cmd == "/contacts":
            self.push_screen(ContactsModal(self.node.contacts.list_all()))

        else:
            self._show_error(
                f"comando desconhecido: [bold]{cmd}[/bold] — use /add, /send, /contacts, /quit"
            )

    def _graceful_exit(self) -> None:
        if self.node:
            self.node.stop()
        self.exit()
