# Telex — Elaboração Arquitetural Detalhada

Documento de referência que detalha cada ponto arquitetural do Telex antes da implementação.
Cada seção corresponde a uma decisão ou subsistema que precisa de definição clara.

---

## 1. Papel do Rendezvous no Hole Punching

### O Problema

Dois peers atrás de NAT não conseguem se comunicar diretamente. O NAT traduz endereços internos para externos, mas bloqueia pacotes de entrada não-solicitados. Para que A e B se comuniquem, ambos precisam enviar pacotes um para o outro "ao mesmo tempo", criando mapeamentos no NAT que permitem a passagem de pacotes vindos do outro peer.

O Rendezvous Server é a peça central que viabiliza esse processo — mas seu grau de envolvimento define a arquitetura.

### Abordagem A: Coordenador Puro (Clássico)

O rendezvous **não toca nos pacotes de dados**. Ele apenas:

1. Recebe registros de peers (nickname + endpoint público descoberto via STUN)
2. Quando A quer falar com B, informa a ambos o endpoint público do outro
3. A e B enviam pacotes UDP diretamente um para o outro

```
Peer A                    Rendezvous                   Peer B
  |                           |                           |
  |--- REGISTER(A, pubA) ---->|                           |
  |                           |<--- REGISTER(B, pubB) ---|
  |                           |                           |
  |--- CONNECT(target=B) ---->|                           |
  |<-- PUNCH_INTRO(pubB) ----|                           |
  |                           |--- PUNCH_INTRO(pubA) --->|
  |                           |                           |
  |========= UDP Punch (A envia para pubB) =============>|
  |<======== UDP Punch (B envia para pubA) ===============|
  |                           |                           |
  |<========= P2P direto estabelecido ===================>|
```

**Prós:**
- Simples de implementar
- Rendezvous é stateless após a introdução
- O padrão da indústria (WebRTC, libjingle, etc.)

**Contras:**
- Requer timing preciso: ambos os peers devem enviar pacotes quase simultaneamente
- Se um peer recebe o PUNCH_INTRO com atraso, o punch pode falhar
- Não funciona com Symmetric NAT

### Abordagem B: Proxy de Punch (IP Spoofing)

O rendezvous envia pacotes **se passando** pelo peer remoto para "pré-aquecer" o NAT.

**Problema fatal:** IP spoofing (forjar o endereço de origem de um pacote UDP) é bloqueado pela maioria dos ISPs e provedores de cloud via BCP38/uRPF. Em ambientes modernos, isso simplesmente **não funciona**. Um VPS na AWS, GCP, ou DigitalOcean não consegue enviar pacotes com IP de origem diferente do seu.

**Veredito: descartada.** Não é viável em infraestrutura real.

### Abordagem C: Modelo Híbrido (Coordenador + Facilitador)

O rendezvous coordena a troca de endpoints **e** participa ativamente do timing:

1. A envia CONNECT para o rendezvous
2. O rendezvous envia PUNCH_INTRO para **ambos simultaneamente**
3. Os peers, ao receber o PUNCH_INTRO, imediatamente iniciam o envio de pacotes de punch
4. O rendezvous pode enviar um pacote "dummy" para o NAT de cada peer (a partir do IP do rendezvous) para criar uma entrada no NAT — isso **não** substitui o punch real, mas pode ajudar em NATs do tipo Restricted Cone

A diferença para o Coordenador Puro é sutil: o rendezvous toma cuidado extra com o **timing** da notificação (envia para A e B no mesmo instante) e pode opcionalmente enviar pacotes "keep-warm" para os NATs.

**Prós:**
- Melhor taxa de sucesso que o coordenador puro em NATs restritivos
- Não depende de IP spoofing

**Contras:**
- Ligeiramente mais complexo
- Os pacotes do rendezvous para os NATs não garantem sucesso (o NAT registra o IP do rendezvous, não do peer remoto)

### Recomendação

**Coordenador Puro (Abordagem A)** com refinamentos de timing. O rendezvous envia o PUNCH_INTRO para ambos os peers com mínimo delay entre si. Os peers executam o punch com retry agressivo (pacotes a cada 100-200ms por até 10 segundos). Isso é o que funciona na prática e é o que protocolos reais usam.

A "assunção de identidade" mencionada originalmente se traduz melhor como: **o rendezvous age em nome dos peers para orquestrar a conexão**, mas não falsifica pacotes. Ele coordena, introduz, e os peers fazem o trabalho.

---

## 2. Modelo de Conexão On-Demand

### Decisão Base

Sem keepalive/heartbeat. Cada "sessão de comunicação" é efêmera. Se o NAT fechar, refaz o hole punch.

### Ciclo de Vida de uma Conexão

```
                        ┌─────────────────────────────┐
                        │       DISCONNECTED           │
                        │  (estado default de todo     │
                        │   contato)                   │
                        └──────────┬──────────────────┘
                                   │
                          Usuário quer enviar msg
                          ou recebe PUNCH_INTRO
                                   │
                                   ▼
                        ┌─────────────────────────────┐
                        │       PUNCHING               │
                        │  (enviando pacotes de punch  │
                        │   a cada 100-200ms)          │
                        └──────────┬──────────────────┘
                                   │
                          Recebeu PUNCH_ACK do peer
                                   │
                                   ▼
                        ┌─────────────────────────────┐
                        │       CONNECTED              │
                        │  (NAT hole aberto, pode      │
                        │   enviar/receber msgs)       │
                        │  TTL = now + NAT_TIMEOUT     │
                        └──────────┬──────────────────┘
                                   │
                        ┌──────────┴──────────────────┐
                        │                              │
                  Cada msg enviada              TTL expirou sem
                  ou recebida reseta            atividade
                  o TTL                                │
                        │                              ▼
                        │               ┌─────────────────────────┐
                        │               │       STALE              │
                        │               │  (NAT provavelmente      │
                        │               │   fechou, precisa        │
                        │               │   re-punch)              │
                        │               └──────────┬──────────────┘
                        │                          │
                        │                 Próximo envio dispara
                        │                 novo hole punch
                        │                          │
                        └──────────────────────────┘
```

### Cache de Conexões (ConnectionState)

Cada peer mantém um cache local com o estado de cada conexão:

```python
@dataclass
class PeerConnection:
    nickname: str
    public_addr: tuple[str, int]
    state: Literal["disconnected", "punching", "connected", "stale"]
    last_activity: float  # timestamp da última msg enviada/recebida
    nat_ttl: float        # segundos estimados até o NAT fechar (default: 30)

    @property
    def is_alive(self) -> bool:
        return (
            self.state == "connected"
            and (time.time() - self.last_activity) < self.nat_ttl
        )
```

### Detecção de NAT Hole Closure

Não existe uma forma direta de saber se o NAT fechou o mapeamento. A estratégia é **estimativa local**:

- **TTL conservador**: assumir que o NAT fecha após **30 segundos** de inatividade. NATs residenciais típicos mantêm mapeamentos UDP por 30-120s (RFC 4787 recomenda mínimo de 2 minutos, mas muitos NATs não seguem)
- **Evidência de fechamento**: se enviar uma mensagem e não receber ACK dentro do timeout de retransmissão (ex: 3 tentativas, 2s cada), considerar conexão como `stale`
- **Reset de TTL**: cada mensagem enviada **ou recebida** com sucesso reseta o timer do TTL

### Cenário Crítico: Resposta Tardia

A envia mensagem para B. B lê e quer responder 5 minutos depois. O NAT já fechou.

**Fluxo:**
1. B tenta enviar resposta para A
2. O `PeerConnection` de A no cache de B está `stale` (TTL expirou)
3. B automaticamente inicia um novo hole punch via rendezvous
4. Após o punch, B envia a resposta
5. B também descarrega qualquer mensagem pendente no buffer

### Latência do Primeiro Envio

O overhead do primeiro envio em uma sessão inclui:

| Etapa | Latência estimada |
|---|---|
| Consulta ao rendezvous (CONNECT) | ~50-200ms (1 RTT ao servidor) |
| Rendezvous notifica ambos peers (PUNCH_INTRO) | ~50-200ms |
| Hole punch (pacotes + espera ACK) | ~200-2000ms (retry a cada 200ms) |
| **Total** | **~300ms a ~2.5s** |

Após o punch, mensagens subsequentes têm latência normal de um UDP direto (~1-50ms em LAN/Internet).

---

## 3. ACK e Garantia de Entrega

### Formato do ACK

O ACK é uma `Message` com `type=ACK` cujo `body` contém o `id` da mensagem original sendo confirmada:

```python
# Mensagem original enviada por A:
Message(
    id="abc-123",
    sender=("1.2.3.4", 5000),
    receiver=("5.6.7.8", 6000),
    body="Olá!",
    type=MessageType.CHAT,
)

# ACK enviado por B em resposta:
Message(
    id="def-456",          # ID próprio do ACK
    sender=("5.6.7.8", 6000),
    receiver=("1.2.3.4", 5000),
    body="abc-123",        # referencia o ID da msg original
    type=MessageType.ACK,
)
```

### Máquina de Estados de uma Mensagem

```
     send()
       │
       ▼
  ┌─────────┐    ACK recebido     ┌────────────┐
  │  SENT    │ ─────────────────> │  DELIVERED  │
  └────┬─────┘                    └────────────┘
       │
       │ timeout (2s)
       ▼
  ┌──────────┐   ACK recebido     ┌────────────┐
  │ RETRY_1  │ ─────────────────> │  DELIVERED  │
  └────┬─────┘                    └────────────┘
       │
       │ timeout (2s)
       ▼
  ┌──────────┐   ACK recebido     ┌────────────┐
  │ RETRY_2  │ ─────────────────> │  DELIVERED  │
  └────┬─────┘                    └────────────┘
       │
       │ timeout (2s)
       ▼
  ┌──────────┐
  │ FAILED   │ ──> move para buffer (store-and-forward)
  └──────────┘     marca conexão como STALE
```

### Parâmetros

| Parâmetro | Valor | Configurável? |
|---|---|---|
| ACK_TIMEOUT | 2 segundos | Sim, via construtor do Node |
| MAX_RETRIES | 3 tentativas | Sim |
| Total wait antes de FAILED | 6 segundos | Derivado |

### Deduplicação no Receptor

Se o remetente retransmite a mesma mensagem (mesmo `id`), o receptor precisa:

1. Detectar que já processou esse `id`
2. **Não** exibir a mensagem novamente
3. **Enviar o ACK novamente** (porque o ACK anterior pode ter se perdido)

Implementação: manter um `set` de IDs já processados com expiração temporal (sliding window):

```python
@dataclass
class DeduplicationCache:
    _seen: dict[str, float]  # {message_id: timestamp}
    ttl: float = 60.0        # IDs expiram após 60s

    def is_duplicate(self, msg_id: str) -> bool:
        self._evict_expired()
        if msg_id in self._seen:
            return True
        self._seen[msg_id] = time.time()
        return False
```

### Interação ACK + Hole Punch

Quando um ACK não chega, há ambiguidade: foi o peer que está offline ou o NAT que fechou?

**Estratégia**: após MAX_RETRIES sem ACK:
1. Marcar a mensagem como FAILED e mover para o buffer
2. Marcar a conexão como STALE
3. **Não** tentar re-punch imediatamente (evita loop)
4. O re-punch acontece na próxima vez que o usuário explicitamente tentar enviar uma nova mensagem

---

## 4. Buffer Store-and-Forward (Mensagens Offline)

### Localização do Buffer

O buffer reside **exclusivamente no sender**. Razões:

- Mantém o rendezvous simples (apenas coordenação, não armazena dados de usuário)
- Evita que o rendezvous se torne um ponto de falha para dados
- O sender é o interessado na entrega — ele sabe quando re-enviar
- Sem questões de privacidade (mensagens criptografadas não ficam em servidor terceiro)

### Quando uma Mensagem Entra no Buffer

Uma mensagem é enfileirada no buffer quando:

1. **ACK não recebido** após MAX_RETRIES (conexão era ativa mas falhou)
2. **Conexão em estado STALE/DISCONNECTED** e o usuário tenta enviar (pode-se tentar o punch primeiro, e se falhar, bufferar)

### Estrutura do Buffer

O `MessageBuffer` existente em `src/buffer.py` já usa `deque[Message]` por contato. Expansões necessárias:

```python
class MessageBuffer:
    MAX_PER_CONTACT: int = 100     # máximo de msgs por contato
    MAX_TOTAL: int = 1000          # máximo global

    def __init__(self):
        self._pending: dict[str, deque[Message]] = {}
        self._total_count: int = 0

    def enqueue(self, contact_name: str, message: Message) -> bool:
        """Retorna False se o buffer estiver cheio."""
        if self._total_count >= self.MAX_TOTAL:
            return False
        queue = self._pending.setdefault(contact_name, deque())
        if len(queue) >= self.MAX_PER_CONTACT:
            return False
        queue.append(message)
        self._total_count += 1
        return True
```

### Trigger de Re-envio

Sem keepalive, o trigger de re-envio é **oportunista**:

1. **Usuário envia nova mensagem para o contato**: o hole punch é re-feito, e se bem-sucedido, o buffer inteiro é descarregado antes da nova mensagem
2. **Contato inicia comunicação**: se B faz hole punch com A (B quer enviar algo), A detecta a conexão e descarrega o buffer
3. **Consulta periódica ao rendezvous** (opcional): A pode periodicamente checar se B está registrado no rendezvous e tentar reconectar

Fluxo de flush:

```
Hole punch com B bem-sucedido
         │
         ▼
  buffer.has_pending("bob")?
         │
    Sim ──┤
         │
         ▼
  Para cada msg em buffer.flush("bob"):
    1. Envia a msg
    2. Aguarda ACK (com retry)
    3. Se falhar, re-enfileira
         │
         ▼
  Envia a nova mensagem (se houver)
```

### Persistência

Para o escopo do projeto: **buffer apenas em memória**. Se o processo do peer morrer, as mensagens pendentes são perdidas. Isso é aceitável porque:

- Simplifica a implementação significativamente
- Um chat P2P efêmero não tem expectativa de durabilidade enterprise
- Persistência em disco pode ser adicionada futuramente (serializar o deque em JSON/SQLite)

### Ordenação

Mensagens no buffer são armazenadas em **ordem de enfileiramento** (FIFO) e descarregadas nessa ordem. O campo `timestamp` já presente na `Message` permite que o receptor ordene mensagens recebidas fora de ordem, se necessário.

---

## 5. Modelo de Confiança e Gerenciamento de Chaves

### Objetivo

Garantir que:
- **Autenticidade**: a mensagem realmente veio de quem diz que veio
- **Confidencialidade**: ninguém além do destinatário pode ler o conteúdo
- **Integridade**: a mensagem não foi alterada em trânsito

### Opção A: TOFU (Trust On First Use)

Modelo usado pelo SSH. Na primeira conexão direta entre A e B, trocam chaves públicas. Cada peer armazena localmente a chave pública dos contatos conhecidos.

```
Primeira conexão A <-> B:
  A envia: KEY_EXCHANGE { public_key: pubA }
  B envia: KEY_EXCHANGE { public_key: pubB }
  Ambos armazenam a chave do outro localmente

Conexões seguintes:
  A já tem pubB armazenada, B já tem pubA
  Mensagens são criptografadas/assinadas usando as chaves armazenadas
  Se a chave mudar: ALERTA (possível MITM)
```

**Prós:**
- Descentralizado — não depende do rendezvous para segurança
- Modelo bem compreendido (SSH, Signal na primeira troca)
- Simples de implementar

**Contras:**
- Vulnerável na **primeira** conexão (MITM pode interceptar a troca de chaves)
- Se o peer reinstalar/perder as chaves, todos os contatos veem um alerta de "chave mudou"
- Requer armazenamento local persistente de chaves

### Opção B: Rendezvous como Autoridade de Chaves

O rendezvous armazena as chaves públicas de todos os peers. Quando A quer se comunicar com B, consulta a chave pública de B no rendezvous.

```
Registro:
  A envia: REGISTER { nickname, public_addr, public_key: pubA }
  Rendezvous armazena pubA associada ao nickname "alice"

Comunicação:
  A quer falar com B
  A consulta: GET_KEY { target: "bob" }
  Rendezvous responde: KEY_RESPONSE { public_key: pubB }
  A usa pubB para criptografar/verificar
```

**Prós:**
- Chaves disponíveis antes mesmo da primeira conexão direta
- Peer pode trocar chaves (reinstalação) atualizando no rendezvous
- Não precisa de troca de chaves no canal P2P

**Contras:**
- **O rendezvous se torna um ponto de confiança central** — se comprometido, MITM é trivial
- Centraliza um aspecto de segurança em um sistema que deveria ser P2P
- Requer que o rendezvous persista dados (chaves)
- As chaves viajam pela rede sem proteção no canal rendezvous-peer (a menos que esse canal também seja seguro)

### Opção C: Modelo Híbrido

Chaves são registradas no rendezvous **e** verificadas na conexão direta:

```
Registro:
  A registra pubA no rendezvous
  B registra pubB no rendezvous

Primeira conexão:
  A obtém pubB do rendezvous
  B obtém pubA do rendezvous
  Na conexão direta, ambos enviam KEY_EXCHANGE com suas chaves
  Cada lado COMPARA a chave recebida diretamente com a obtida do rendezvous
  Se iguais: ✓ confiança reforçada (o rendezvous não está mentindo)
  Se diferentes: ALERTA (possível MITM no rendezvous OU na conexão direta)

Conexões seguintes:
  Usa chaves armazenadas localmente (como TOFU)
  Alerta se a chave mudar tanto localmente quanto no rendezvous
```

**Prós:**
- Defesa em profundidade: mesmo que o rendezvous seja comprometido, a verificação direta detecta inconsistência
- A chave fica disponível antes da conexão (permite criptografar a primeira mensagem)
- Se a chave local mudar mas a do rendezvous não (ou vice-versa), detecta anomalia

**Contras:**
- Mais complexo de implementar
- Duas fontes de verdade podem gerar confusão em edge cases

### Recomendação

**Opção C (Híbrido)** oferece o melhor equilíbrio para um projeto que já tem rendezvous:
- O rendezvous já existe, então armazenar chaves é incremental
- A verificação direta mitiga o risco de confiar só no rendezvous
- Demonstra compreensão de modelos de confiança na apresentação

Para simplificação no MVP: começar com **Opção A (TOFU)** pura e evoluir para C.

---

## 6. Protocolo do Rendezvous Server

### Tipos de Mensagem do Protocolo

O protocolo rendezvous é **separado** do protocolo de chat entre peers. Usa seu próprio enum e modelo:

```python
class RendezvousAction(str, Enum):
    # Peer -> Rendezvous
    REGISTER = "REGISTER"           # registrar-se no servidor
    UNREGISTER = "UNREGISTER"       # sair do servidor
    DISCOVER = "DISCOVER"           # buscar info de um peer específico
    PEER_LIST = "PEER_LIST"         # listar todos os peers online
    CONNECT = "CONNECT"             # solicitar hole punch com um peer

    # Rendezvous -> Peer
    REGISTER_OK = "REGISTER_OK"     # confirmação de registro
    PEER_INFO = "PEER_INFO"         # resposta ao DISCOVER
    PEER_LIST_RESPONSE = "PEER_LIST_RESPONSE"
    PUNCH_INTRO = "PUNCH_INTRO"     # instrução para iniciar punch
    ERROR = "ERROR"                 # erro genérico

class RendezvousMessage(BaseModel):
    action: RendezvousAction
    sender_nickname: str = ""
    payload: dict = {}              # dados específicos de cada action
    timestamp: float = Field(default_factory=time.time)
```

### Detalhamento de Cada Mensagem

**REGISTER** — Peer se apresenta ao rendezvous
```json
{
  "action": "REGISTER",
  "sender_nickname": "alice",
  "payload": {
    "public_addr": ["1.2.3.4", 5000],
    "local_addr": ["192.168.1.10", 5000],
    "public_key": "hex-encoded-ed25519-pubkey"
  }
}
```

**REGISTER_OK** — Rendezvous confirma o registro
```json
{
  "action": "REGISTER_OK",
  "payload": {
    "your_public_addr": ["1.2.3.4", 5000],
    "ttl": 300
  }
}
```

**DISCOVER** — Peer busca informações de outro peer
```json
{
  "action": "DISCOVER",
  "sender_nickname": "alice",
  "payload": {
    "target": "bob"
  }
}
```

**PEER_INFO** — Resposta ao DISCOVER
```json
{
  "action": "PEER_INFO",
  "payload": {
    "nickname": "bob",
    "public_addr": ["5.6.7.8", 6000],
    "public_key": "hex-encoded-ed25519-pubkey",
    "online": true
  }
}
```

**CONNECT** — Peer solicita hole punch com outro peer
```json
{
  "action": "CONNECT",
  "sender_nickname": "alice",
  "payload": {
    "target": "bob"
  }
}
```

**PUNCH_INTRO** — Rendezvous instrui ambos os peers a iniciar punch
```json
{
  "action": "PUNCH_INTRO",
  "payload": {
    "peer_nickname": "bob",
    "peer_public_addr": ["5.6.7.8", 6000],
    "peer_local_addr": ["192.168.1.20", 6000],
    "peer_public_key": "hex-encoded-ed25519-pubkey"
  }
}
```

### Estado Mantido no Servidor

```python
@dataclass
class RegisteredPeer:
    nickname: str
    public_addr: tuple[str, int]
    local_addr: tuple[str, int]
    public_key: str
    last_seen: float
    real_addr: tuple[str, int]  # addr real do recvfrom (validação)

peers: dict[str, RegisteredPeer] = {}  # nickname -> info
```

### Ciclo de Vida de um Peer no Rendezvous

```
  Peer inicia
      │
      ▼
  STUN discovery (obtém public_addr)
      │
      ▼
  REGISTER no rendezvous ─────> Rendezvous armazena peer
      │                         e responde REGISTER_OK
      │
      ▼
  Peer está "online" no rendezvous
      │
      ├── Pode enviar PEER_LIST para ver quem está online
      ├── Pode enviar CONNECT para iniciar hole punch
      ├── Pode receber PUNCH_INTRO (outro peer quer conectar)
      │
      │   (se não houver atividade por TTL segundos)
      │
      ▼
  Rendezvous remove peer por inatividade
  (peer precisa re-registrar na próxima vez que quiser se comunicar)
```

### Autenticação do Peer com o Rendezvous

Para o escopo do projeto, autenticação mínima:

1. **Validação de endereço**: o rendezvous compara o `public_addr` declarado no REGISTER com o endereço real de onde o pacote veio (`recvfrom`). Se divergirem, recusa.
2. **Unicidade de nickname**: se "alice" já está registrada, uma nova tentativa de REGISTER com o mesmo nickname é recusada (ou sobrescreve, dependendo da política).
3. **Rate limiting básico**: limitar registros por IP para evitar flood.

Autenticação forte (tokens, challenge-response) está fora do escopo.

### Formato do Protocolo

JSON sobre UDP, usando Pydantic models (consistente com a `Message` já existente). O rendezvous escuta em um socket UDP único. Cada mensagem é um JSON com o campo `action` determinando o tipo.

---

## 7. Fluxo Completo End-to-End de Envio de Mensagem

### Cenário Principal: Primeira Mensagem entre A e B

Premissas: ambos registrados no rendezvous, sem conexão prévia.

```
Peer A (sender)                Rendezvous               Peer B (receiver)
     │                              │                         │
     │  1. Usuário digita:          │                         │
     │     /send bob Olá            │                         │
     │                              │                         │
     │  2. Verifica PeerConnection  │                         │
     │     state = DISCONNECTED     │                         │
     │                              │                         │
     │  3. CONNECT(target=bob) ────>│                         │
     │                              │  4. Lookup "bob" no     │
     │                              │     registro            │
     │                              │                         │
     │  5. <── PUNCH_INTRO(pubB) ──│── PUNCH_INTRO(pubA) ──>│
     │                              │                         │
     │  6. state = PUNCHING         │         state = PUNCHING│
     │                              │                         │
     │  7. PUNCH ──────────────────────────────────────────>│ │
     │  │<────────────────────────────────────────── PUNCH  │ │
     │                              │                         │
     │  8. PUNCH_ACK ──────────────────────────────────────>│
     │  │<──────────────────────────────────────── PUNCH_ACK│
     │                              │                         │
     │  9. state = CONNECTED        │       state = CONNECTED │
     │     TTL = now + 30s          │       TTL = now + 30s   │
     │                              │                         │
     │  10. Verifica buffer:        │                         │
     │      has_pending("bob")?     │                         │
     │      → Não                   │                         │
     │                              │                         │
     │  11. Criptografa msg com     │                         │
     │      pubB + assina com       │                         │
     │      privA                   │                         │
     │                              │                         │
     │  12. CHAT(encrypted) ───────────────────────────────>│
     │                              │                 13. Verifica│
     │                              │                 assinatura  │
     │                              │                 Descriptografa│
     │                              │                 Exibe msg    │
     │                              │                 Verifica dedup│
     │                              │                         │
     │  14. <─────────────────────────────────────── ACK    │
     │                              │                         │
     │  15. Marca msg como          │                         │
     │      DELIVERED               │                         │
     │      Reseta TTL              │                         │
```

### Cenário de Falha: ACK Não Recebido

```
Peer A                                              Peer B
  │                                                    │
  │── CHAT(msg_id=X) ──────────────────────────────>│  │
  │                                                    │
  │   (2s timeout, sem ACK)                            │
  │                                                    │
  │── CHAT(msg_id=X) [retry 1] ───────────────────>│  │ (pacote perdido
  │                                                    │  ou NAT fechou)
  │   (2s timeout, sem ACK)                            │
  │                                                    │
  │── CHAT(msg_id=X) [retry 2] ───────────────────>│  │
  │                                                    │
  │   (2s timeout, sem ACK)                            │
  │                                                    │
  │  MAX_RETRIES atingido                              │
  │  → msg_id=X movida para buffer                    │
  │  → PeerConnection.state = STALE                    │
  │  → Exibe: "[!] Mensagem para bob será             │
  │            enviada quando reconectar"              │
```

### Cenário: B Responde Depois do NAT Fechar

```
Peer A                       Rendezvous                Peer B
  │                               │                        │
  │ (30s+ se passaram,            │                        │
  │  NAT de ambos fechou)         │                        │
  │                               │                        │
  │                               │                        │ Usuário digita:
  │                               │                        │ /send alice Oi!
  │                               │                        │
  │                               │                        │ PeerConnection
  │                               │                        │ state = STALE
  │                               │                        │
  │                               │<── CONNECT(alice) ────│
  │                               │                        │
  │ <── PUNCH_INTRO ─────────────│──── PUNCH_INTRO ──────>│
  │                               │                        │
  │ (novo hole punch)             │          (novo punch)  │
  │<═══════════════════ PUNCH ═══════════════════════════>│ │
  │                               │                        │
  │ state = CONNECTED             │       state = CONNECTED│
  │                               │                        │
  │ Detecta reconexão com bob     │                        │
  │ → buffer.has_pending("bob")?  │                        │
  │ → Sim! Flush buffer           │                        │
  │                               │                        │
  │── CHAT(buffered msg 1) ────────────────────────────>│  │
  │<─────────────────────────────────────────── ACK     │  │
  │── CHAT(buffered msg 2) ────────────────────────────>│  │
  │<─────────────────────────────────────────── ACK     │  │
  │                               │                        │
  │                               │  B envia sua mensagem: │
  │<────────────────────────────────── CHAT("Oi!") ────│  │
  │── ACK ──────────────────────────────────────────────>│ │
```

### Cenário: Peer Offline (Não Registrado no Rendezvous)

```
Peer A                        Rendezvous
  │                                │
  │── CONNECT(target=bob) ────────>│
  │                                │  "bob" não encontrado
  │<── ERROR("peer_not_found") ───│
  │                                │
  │  Mensagem vai direto para o buffer
  │  Exibe: "[!] bob não está online.
  │          Mensagem será entregue quando conectar."
```

---

## 8. Criptografia e Segurança das Mensagens

### Objetivos de Segurança

| Propriedade | Descrição | Necessária? |
|---|---|---|
| Confidencialidade | Só o destinatário lê o conteúdo | Sim |
| Autenticidade | Confirma quem enviou | Sim |
| Integridade | Conteúdo não foi alterado | Sim (inclusa na autenticidade) |
| Forward Secrecy | Comprometimento de chave não expõe msgs passadas | Desejável, não obrigatória |
| Proteção contra replay | Reenvio de msg capturada não engana o receptor | Sim |

### Arquitetura Criptográfica

Dois níveis de chaves:

1. **Chaves de identidade (longo prazo)**: Ed25519 — usadas para **assinatura** e para derivar chaves de sessão
2. **Chaves de sessão (efêmeras)**: X25519 + AES-256-GCM — usadas para **criptografia** do payload

```
Par de chaves de identidade (Ed25519):
  - Gerado uma vez por peer
  - Chave privada: nunca sai do peer
  - Chave pública: compartilhada via rendezvous e/ou TOFU
  - Usada para: assinar mensagens, verificar identidade

Chave de sessão (X25519 -> AES-256-GCM):
  - Derivada via Diffie-Hellman efêmero a cada nova sessão de punch
  - Usada para: criptografar/descriptografar o body das mensagens
  - Descartada quando a sessão termina
  - Fornece Forward Secrecy
```

### Fluxo de Troca de Chaves de Sessão

Integrado ao hole punch — após o PUNCH_ACK, antes de qualquer CHAT:

```
Peer A                                              Peer B
  │                                                    │
  │  (hole punch completo, ambos CONNECTED)            │
  │                                                    │
  │  Gera par efêmero X25519:                          │
  │  ephA_priv, ephA_pub                               │
  │                                                    │
  │── KEY_EXCHANGE {                    ────────────>│  │
  │     ephemeral_pub: ephA_pub,                       │
  │     identity_pub: idA_pub,                         │
  │     signature: sign(ephA_pub, idA_priv)            │  Gera par efêmero:
  │   }                                                │  ephB_priv, ephB_pub
  │                                                    │
  │                                                    │  Verifica assinatura
  │                                                    │  de A com idA_pub
  │                                                    │
  │  <──────────────── KEY_EXCHANGE {               │  │
  │                      ephemeral_pub: ephB_pub,      │
  │                      identity_pub: idB_pub,        │
  │                      signature: sign(ephB_pub,     │
  │                                     idB_priv)      │
  │                    }                               │
  │                                                    │
  │  Verifica assinatura de B                          │
  │                                                    │
  │  Ambos calculam:                                   │
  │  shared_secret = X25519(ephA_priv, ephB_pub)       │
  │                = X25519(ephB_priv, ephA_pub)       │
  │                                                    │
  │  Derivam chave AES via HKDF:                       │
  │  aes_key = HKDF(shared_secret, salt, info)         │
  │                                                    │
  │<══════ Canal criptografado estabelecido ══════════>│
```

### O Que é Criptografado

```python
class Message(BaseModel):
    id: str                    # NÃO criptografado (necessário para ACK/dedup)
    sender: tuple[str, int]    # NÃO criptografado (necessário para roteamento)
    sender_name: str           # NÃO criptografado (necessário para exibição)
    receiver: tuple[str, int]  # NÃO criptografado (necessário para roteamento)
    type: MessageType          # NÃO criptografado (necessário para roteamento)
    timestamp: float           # NÃO criptografado (necessário para ordenação)
    body: str                  # CRIPTOGRAFADO (conteúdo da mensagem)
    nonce: str                 # Nonce usado na criptografia AES-GCM
    signature: str             # Assinatura Ed25519 sobre todo o payload
```

O `body` é criptografado com AES-256-GCM (que também fornece integridade do body).
A `signature` é calculada sobre **todos os campos** (incluindo o body já criptografado), garantindo que os metadados também não foram alterados.

### Proteção Contra Replay Attacks

AES-GCM usa um **nonce** (number used once) que impede replay no nível criptográfico. Adicionalmente:

1. **Nonce único por mensagem**: gerado aleatoriamente (12 bytes), incluído na mensagem
2. **Timestamp**: o receptor rejeita mensagens com timestamp fora de uma janela aceitável (ex: +-30 segundos do relógio local)
3. **Cache de deduplicação**: o `message.id` já é verificado contra duplicatas (mesma estrutura usada para ACK retry)

A combinação de nonce + timestamp + dedup ID torna replay attacks impraticáveis.

### Algoritmos e Bibliotecas

| Componente | Algoritmo | Biblioteca Python |
|---|---|---|
| Assinatura de identidade | Ed25519 | `cryptography` (Ed25519PrivateKey) |
| Troca de chaves de sessão | X25519 (ECDH) | `cryptography` (X25519PrivateKey) |
| Derivação de chave | HKDF-SHA256 | `cryptography` (HKDF) |
| Criptografia de payload | AES-256-GCM | `cryptography` (AESGCM) |
| Nonce | 12 bytes aleatórios | `os.urandom(12)` |

Todas as operações usam uma única dependência externa: `cryptography` (padrão da indústria Python, mantida pela Python Cryptographic Authority).

### Simplificação para MVP

Se a complexidade de chaves efêmeras + DH for demais para a primeira versão:

**MVP**: apenas assinatura Ed25519 (autenticidade + integridade) sem criptografia do body. Isso garante que ninguém forja mensagens, mas o conteúdo viaja em texto claro.

**V2**: adicionar X25519 + AES-GCM para confidencialidade completa.

---

## Resumo de Dependências entre Subsistemas

```
┌─────────────────────────────────────────────────────────────┐
│                        CLI (main.py)                        │
│  /send, /connect, /peers, /contacts                        │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                     Node (src/node.py)                       │
│  Orquestra todos os subsistemas                             │
│                                                              │
│  ┌──────────┐  ┌──────────────┐  ┌──────────────────────┐  │
│  │ Contacts │  │    Buffer    │  │  ConnectionCache     │  │
│  │          │  │  (store &    │  │  (PeerConnection     │  │
│  │          │  │   forward)   │  │   states + TTL)      │  │
│  └──────────┘  └──────────────┘  └──────────────────────┘  │
│                                                              │
│  ┌──────────────────────┐  ┌────────────────────────────┐   │
│  │   CryptoManager      │  │   AckTracker               │   │
│  │   (keys, sign,       │  │   (pending ACKs, retry,    │   │
│  │    encrypt/decrypt)  │  │    dedup cache)            │   │
│  └──────────────────────┘  └────────────────────────────┘   │
│                                                              │
│  ┌──────────────────────┐  ┌────────────────────────────┐   │
│  │   RendezvousClient   │  │   HolePuncher              │   │
│  │   (register, connect,│  │   (punch, detect success)  │   │
│  │    discover)         │  │                            │   │
│  └──────────────────────┘  └────────────────────────────┘   │
│                                                              │
│  ┌──────────────────────┐  ┌────────────────────────────┐   │
│  │   MessageRouter      │  │   UDPTransport             │   │
│  │   (dispatch by type) │  │   (socket, send, listen)   │   │
│  └──────────────────────┘  └────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```
