### Documento de Arquitetura: Chat P2P sobre UDP

**1. Visão Geral do Projeto**
Desenvolvimento de uma aplicação de chat descentralizada (P2P) focada em redes de computadores. Cada instância atua simultaneamente como cliente e servidor (Nó). O projeto foca na manipulação direta de sockets, contornando protocolos de alto nível para implementar lógicas customizadas de confiabilidade na camada de aplicação.

* **Linguagem Base:** Python (utilizando bibliotecas nativas como `socket`, `threading`, `time` e `json` para a lógica de rede).
* **Protocolo de Transporte:** UDP (escolhido pela simplicidade e ausência de estado/conexão fixa).
* **Ambiente de Deploy/Testes:** Evolutivo. Inicia localmente, passa para um ambiente contêinerizado orquestrado via `docker-compose.yml` e finaliza com testes em rede local entre dispositivos e sistemas operacionais distintos.

**2. Modelos de Comunicação (Roteamento)**

* **Mensagens Privadas (Unicast):** Envio direto de dados de um Nó para o IP e porta de destino específicos.
* **Mensagens de Grupo (Multicast - Bônus):** Uso de endereços IP da classe D (ex: `224.0.0.1`) para otimização de tráfego. O envio é feito uma única vez, e a infraestrutura de rede replica o pacote apenas para os nós inscritos no grupo.

**3. Confiabilidade e Estado (Camada de Aplicação)**
O UDP não garante entrega e não monitora conexão. Para contornar isso, as seguintes lógicas serão implementadas:

* **Controle de Presença (Heartbeat / Ping-Pong):** Uma *thread* em segundo plano emite periodicamente pacotes minúsculos ("PING") para os contatos conhecidos. O sistema mantém um registro local com o *timestamp* do último "PING" recebido. Se o tempo exceder o limite, o contato é marcado como *offline*.
* **Tolerância a Desconexões (Store and Forward / Buffer):** Mensagens enviadas para contatos *offline* não são descartadas, mas retidas em uma fila (buffer) local. Quando um novo "PING" desse contato é recebido (reconexão), o sistema descarrega automaticamente as mensagens pendentes para ele.
* **Garantia de Entrega (ACK - Bônus):** Implementação de Confirmação de Recebimento. Toda mensagem recebe um ID. Se o remetente não receber um pacote "ACK" de volta em X segundos, a mensagem é retransmitida.

**4. Interface de Usuário (Apresentação)**
A interface refletirá o estado da rede, dividida em três blocos: Painel Lateral (contatos e status online/offline indicados pelo Heartbeat), Painel Central (histórico de chat com indicativo visual de mensagens no buffer) e Barra de Input. A tecnologia será definida entre duas abordagens:

* **Opção A: Interface Web Assíncrona:** Construção de um servidor local utilizando o framework **FastAPI** para servir a interface visual. A comunicação em tempo real entre o navegador (frontend) e o motor UDP (backend Python) será feita via **WebSockets**. É uma arquitetura excelente para expor portas em contêineres Docker.
* **Opção B: TUI (Terminal User Interface):** Criação de uma interface rica direto no terminal usando bibliotecas como Textual ou Rich (desenhando painéis e cores na linha de comando). É uma opção extremamente leve, ideal para rodar nativamente e realizar testes diretos em ambientes nativos como o **Debian**, sem necessidade de interface gráfica instalada.

**5. Fases de Desenvolvimento (Backlog Atualizado)**

* **Fase 1: Motor Base (UDP Local):** Criar socket UDP escutando em `0.0.0.0`, implementar envio `sendto()` e unir ambos com *threads* para testes no terminal.
* **Fase 2: Gestão de Estado:** Implementar o Heartbeat (envio/processamento de PINGs), o buffer de mensagens pendentes e a lógica de reconexão.
* **Fase 3: Acoplamento da Interface:** Escolher entre Web (FastAPI) ou Terminal (TUI) e conectar o motor de rede construído na Fase 1 e 2 à parte visual.
* **Fase 4: Simulação em Rede Virtual:** Escrever o `Dockerfile` e configurar o `docker-compose.yml` para levantar múltiplos nós em uma rede *bridge*, testando a resolução de nomes.
* **Fase 5: Infraestrutura Real:** Executar o sistema em computadores físicos diferentes na mesma rede, lidando com permissões de Firewall e IPs de rede local.
