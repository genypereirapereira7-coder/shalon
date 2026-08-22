# Shalon — Arquitetura do Sistema

Sistema de pedidos e gestão para a sorveteria Shalon.

**Stack:** Python (FastAPI) no backend e 2 PWAs, os dois no celular. A impressão
da comanda sai do próprio celular do balcão, pelo RawBT.

---

## 1. Visão geral

Três peças, duas delas rodando em aparelhos diferentes — e **nenhum PC**:

```
┌───────────────────────────────────┐   ┌─────────────────────────┐
│  CELULAR DO BALCÃO (Android)      │   │  PWA DONO               │
│  ┌─────────────────────────────┐  │   │  celular do dono        │
│  │ PWA VENDAS                  │  │   │                         │
│  │ - cardápio                  │  │   │  - vendas em tempo real │
│  │ - monta pedido              │  │   │  - editar preços        │
│  │ - envia                     │  │   │  - fechamento do dia    │
│  │ - formata a comanda         │  │   │                         │
│  └──────────────┬──────────────┘  │   │                         │
│      Intent do  │  Android        │   │                         │
│                 v                 │   │                         │
│  ┌─────────────────────────────┐  │   │                         │
│  │ RawBT (app de impressão)    │  │   │                         │
│  └──────────────┬──────────────┘  │   │                         │
│                 v                 │   │                         │
│           IMPRESSORA TÉRMICA      │   │                         │
└───────────┬───────────────────────┘   └───────────┬─────────────┘
            │                                       │
            │  HTTPS + WebSocket                    │
            └───────────────┬───────────────────────┘
                            │
                            v
            ┌───────────────────────────────────┐
            │  BACKEND (nuvem)                  │
            │  FastAPI + PostgreSQL             │
            │  - regras de negócio              │
            │  - fonte única da verdade         │
            │  - hub WebSocket (tempo real)     │
            └───────────────────────────────────┘
```

**Por que o servidor fica na nuvem:** o dono precisa ver os números de qualquer lugar,
não só dentro da loja. O preço disso é que a sorveteria depende de internet — por isso
o PWA de vendas guarda os pedidos localmente e reenvia quando a conexão volta (seção 7).

**Por que não há PC na loja.** O desenho original tinha um: uma máquina na cozinha
rodando um agente Python de impressão e uma tela de comandas em quiosque. Sumiram os
dois. A tela porque ninguém a olhava — numa sorveteria de balcão quem monta o sorvete
é quem vendeu, com a comanda de papel na mão; uma segunda cópia da mesma informação num
monitor era uma tela a manter, uma sessão a renovar e um alerta a conferir, em troca de
nada. E o agente porque o celular que já está na mão do funcionário fala com a térmica
pelo RawBT, sem PC, sem cabo até a cozinha e sem um processo a mais pra alguém lembrar
de religar depois da queda de luz.

O agente continua no repositório (`agente/`) como caminho alternativo para quem
preferir a térmica presa a um PC. **Os dois não rodam juntos**: cada um imprimiria a sua
via da mesma comanda.

---

## 2. Os três componentes

### 2.1 Backend — `backend/`

| Item | Escolha |
|---|---|
| Framework | FastAPI (async, WebSocket nativo, docs automáticas) |
| Banco | PostgreSQL 16 |
| ORM | SQLAlchemy 2.0 (async) + Alembic para migrations |
| Validação | Pydantic v2 |
| Auth | JWT (PyJWT) + senha com bcrypt |
| Servidor | Uvicorn atrás de Caddy (HTTPS automático) |

Responsabilidades: guardar cardápio e preços, receber e validar pedidos, numerar os
pedidos do dia, distribuir tudo em tempo real via WebSocket, calcular os relatórios.

### 2.2 PWA Vendas — `frontend/vendas/`

Roda no celular Android que fica na sorveteria. Tela única de venda:

```
┌──────────────────────────┐
│  Shalon      Vanusa ▾    │   ← funcionário identificado
├──────────────────────────┤
│ [Sorvetes][Açaí][Bebidas]│   ← categorias
├──────────────────────────┤
│  ┌────────┐  ┌────────┐  │
│  │Casqui- │  │Casqui- │  │   ← botões grandes, foto/cor
│  │nha 1   │  │nha 2   │  │      pensados pra dedo, não mouse
│  │R$ 8,00 │  │R$12,00 │  │
│  └────────┘  └────────┘  │
├──────────────────────────┤
│ 2x Casquinha 1   16,00 ✕ │   ← carrinho
│ 1x Açaí 500ml    18,00 ✕ │
├──────────────────────────┤
│ TOTAL         R$ 34,00   │
│ [   ENVIAR E IMPRIMIR   ]│
└──────────────────────────┘
```

É também quem imprime — ver §3.

### 2.3 PWA Dono — `frontend/dono/`

Roda no celular do dono. Três telas:

- **Hoje** — total vendido, nº de pedidos, ticket médio, ranking dos itens mais vendidos,
  atualizando sozinho conforme as vendas entram (sem apertar refresh).
- **Cardápio** — lista de produtos com o preço editável ali mesmo. Ao salvar, o preço
  novo chega no celular da sorveteria em menos de 1 segundo.
- **Fechamento** — resumo do dia: cada item, quantidade vendida, valor total por item,
  total geral. Histórico por data.

### 2.4 Agente de impressão em PC — `agente/` (alternativa)

Programa Python empacotado como `.exe` único (PyInstaller) que inicia junto com o
Windows, para quem preferir a térmica presa a um PC em vez de ao celular. Ele:

1. Abre uma conexão WebSocket com o backend e fica escutando.
2. Ao chegar um pedido, formata o cupom e manda pra impressora.
3. Confirma pro servidor que imprimiu (`ACK`).
4. Ao religar ou reconectar, pergunta ao servidor quais pedidos do dia ficaram sem imprimir.

Não é o caminho padrão e **não roda junto com a impressão do celular**: os dois se
guiam pelo mesmo `impresso_em`, e na janela entre o papel e o ACK cada um tiraria a sua
via da mesma comanda.

---

## 3. Camada de impressão

Dois caminhos para o mesmo papel, com o mesmo layout de 48 colunas dos dois lados
(`frontend/vendas/comanda.js` e `agente/cupom.py` produzem texto idêntico — comanda que
sai diferente dependendo de quem imprimiu é comanda que ninguém confere).

### 3.1 O caminho principal: RawBT no celular do balcão

O PWA de vendas não alcança a impressora direto. Web Bluetooth não enxerga térmica
clássica (perfil SPP) e Web USB não cobre esse caso no Chrome do Android. O que existe é
o **RawBT**, um aplicativo que fala com a térmica por Bluetooth, USB-OTG ou rede, e
aceita trabalho de fora por um *Intent*:

```
intent:<texto-url-encoded>#Intent;scheme=rawbt;package=ru.a402d.rawbtprinter;end;
```

Três arquivos, uma responsabilidade cada — e o de cima recebe os de baixo por injeção,
de modo que trocar de aplicativo de impressão ou de layout do cupom não se espalha:

| Arquivo | Responsabilidade |
|---|---|
| `frontend/vendas/comanda.js` | o layout do papel. Texto puro: sem DOM, sem rede |
| `frontend/vendas/rawbt.js` | o Intent do Android. Não sabe o que está imprimindo |
| `frontend/vendas/impressao.js` | *quando* imprimir, a fila de retentativa e o ACK |

**O Intent viaja num iframe escondido, não numa troca de página.** Trocar o `location`
tiraria o PWA da frente: o Android levaria o RawBT pro topo e o funcionário voltaria pro
app pela seta do sistema, com o balcão parado no meio. Navegando um iframe, o mesmo
Intent dispara e a tela de venda continua onde estava. O `package=` fixo é o que evita o
seletor "abrir com".

**O que este caminho não consegue saber é se saiu papel.** O Intent é de mão única: não
há retorno, callback nem erro. Então `impresso_em`, quando vem do celular, quer dizer
"a comanda foi entregue à impressora deste aparelho" — não "o papel está na bandeja". A
diferença aparece com a térmica desligada: a venda sai da fila de não-impressos sem ter
saído no papel. Quem percebe é o balcão, que tem a comanda na mão, e reimprime pela
lista de últimos pedidos. É uma troca consciente: o erro oposto — nunca marcar nada —
deixaria toda venda na fila para sempre e faria o agente de PC, se alguém o mantivesse
ligado, imprimir uma segunda via de tudo.

**A fila de retentativa existe pelo caminho offline.** A venda pode subir horas depois
(§7), com o app em segundo plano — e Intent nenhum sai de uma aba que não está na
frente. O que não foi despachado fica no `localStorage` e é tentado de novo quando o app
volta pra tela, com uma faixa contando quantas comandas esperam.

### 3.2 O caminho alternativo: térmica num PC

Aqui o agente conversa com uma interface, não com um modelo específico:

```python
class Impressora(Protocol):
    def imprimir(self, cupom: Cupom) -> None: ...
    def esta_ok(self) -> bool: ...
```

Implementações:

| Classe | Quando usar |
|---|---|
| `EscPosUSB` | Térmica de bobina ligada por USB |
| `EscPosRede` | Térmica com porta de rede (IP fixo) |
| `SpoolerWindows` | Impressora comum: gera PDF e joga na fila do Windows |
| `ImpressoraFake` | Desenvolvimento: escreve o cupom num `.txt` |

Biblioteca: `python-escpos`.

### Recomendação de compra

Compre uma **térmica 80mm com padrão ESC/POS**. Modelos fáceis de achar no Brasil:

- **Elgin i9** — melhor custo-benefício
- **Epson TM-T20X** — a mais confiável, suporte excelente
- **Bematech MP-4200 TH** — comum em assistência técnica

Três detalhes que importam:

- **80mm, não 58mm** — o cupom de 58mm é estreito demais, o pedido fica ilegível.
- **Bluetooth ou USB-OTG**, já que quem manda o trabalho é o celular. (Era USB e só USB
  quando quem imprimia era o PC — Bluetooth com PC cai e dá dor de cabeça. Com celular a
  conta se inverte: o Bluetooth é o que o RawBT faz melhor, e é o que dispensa cabo no
  balcão.)
- **Compre 2 bobinas extras** desde o começo.

> O cupom impresso é uma **comanda de produção**, não documento fiscal. Emissão de
> NFC-e/SAT é outro projeto, fora do escopo (seção 10).

---

## 4. Modelo de dados

Todo valor em dinheiro é **inteiro em centavos** (`preco_centavos = 800` → R$ 8,00).
Ponto flutuante em dinheiro gera erro de centavo no fechamento do dia.

```
usuario
  id, nome, pin_hash, papel(DONO|FUNCIONARIO), ativo, criado_em

categoria
  id, nome, ordem, ativo

produto
  id, categoria_id →categoria, nome, preco_centavos, cor_botao,
  ordem, ativo, criado_em, atualizado_em

preco_historico                       ← auditoria de quem mudou o preço
  id, produto_id →produto, preco_antigo, preco_novo,
  usuario_id →usuario, criado_em

pedido
  id (uuid), id_cliente (uuid)        ← gerado no celular, evita pedido duplicado
  numero_dia (int)                    ← "Pedido #37", reinicia todo dia
  data_operacional (date)
  usuario_id →usuario                 ← quem vendeu
  status (RECEBIDO|EM_PREPARO|PRONTO|ENTREGUE|CANCELADO)
  total_centavos, observacao
  criado_em, impresso_em, cancelado_em, cancelado_por

pedido_item
  id, pedido_id →pedido, produto_id →produto
  nome_snapshot                       ← nome do produto NA HORA da venda
  preco_unit_centavos_snapshot        ← preço NA HORA da venda
  quantidade, subtotal_centavos

fechamento_dia                        ← gerado ao fechar o caixa, imutável
  id, data_operacional, total_centavos, qtd_pedidos,
  fechado_em, fechado_por →usuario
```

### Duas decisões que evitam problema depois

**Snapshot em `pedido_item`.** Quando o dono aumenta o preço da casquinha de R$ 8 pra
R$ 9 às 15h, os pedidos da manhã continuam valendo R$ 8. Sem o snapshot, o relatório de
ontem muda sozinho toda vez que um preço é editado.

**`id_cliente` gerado no celular.** O funcionário aperta "enviar", a internet oscila, ele
aperta de novo. Como o pedido já vem com um UUID do celular, o servidor detecta que é o
mesmo e não cria dois — nem imprime duas comandas.

**`data_operacional` separada de `criado_em`.** Uma venda às 00h20 pertence ao movimento
do dia anterior. O dia operacional vira às 04h (configurável).

---

## 5. API

### REST

```
POST   /auth/login                  nome de usuário + senha → JWT
GET    /auth/sessoes                aparelhos logados agora           [DONO]
DELETE /auth/sessoes/{id}           tira o acesso de um aparelho      [DONO]
POST   /auth/parear                 primeiro acesso do celular (código de pareamento)

GET    /cardapio                    categorias + produtos ativos (o PWA cacheia)
PATCH  /produtos/{id}               editar preço/nome/ativo          [DONO]
POST   /produtos                    criar produto                    [DONO]

POST   /pedidos                     criar pedido (idempotente por id_cliente)
GET    /pedidos/hoje                pedidos do dia operacional
PATCH  /pedidos/{id}/status         mudar status                     [COZINHA]
POST   /pedidos/{id}/impresso       a comanda foi pra impressora     [BALCÃO]
POST   /pedidos/{id}/cancelar       cancelar (exige motivo)   [BALCÃO/DONO]
POST   /pedidos/{id}/reimprimir     manda de novo pra impressora
GET    /pedidos/nao-impressos       o agente chama isso ao reconectar

GET    /relatorios/hoje             totais ao vivo                   [DONO]
GET    /relatorios/dia/{data}       fechamento de um dia             [DONO]
POST   /fechamento                  fecha o caixa do dia             [DONO]
```

### WebSocket

Um endpoint só, `/ws`, com o papel vindo do JWT. O servidor mantém as conexões em
memória agrupadas por papel (`app/servicos/eventos.py`) e faz o roteamento:

| Evento | Servidor → quem | Conteúdo |
|---|---|---|
| `pedido.novo` | agente, dono | pedido completo |
| `pedido.status` | vendas, dono | pedido completo |
| `pedido.impresso` | dono | pedido completo |
| `preco.alterado` | vendas, dono | produto completo |
| `metricas.tick` | dono | o resumo do dia inteiro |
| `impressora.status` | dono | ok, detalhe |

O papel `COZINHA` continua no `Papel` e no `DESTINOS` do hub porque é um valor de
enum no Postgres e apagá-lo custa uma migration; nenhum cliente conecta com ele
desde que a tela do PC saiu.

E do agente/telas para o servidor: `auth` (obrigatória, a primeira), `ping` e
`impressora.status`.

**A autenticação é a primeira mensagem, não a URL.** O navegador não deixa pôr
cabeçalho `Authorization` num WebSocket, e o caminho comum — `/ws?token=…` —
gravaria o token de acesso no log de requisições do Caddy e no histórico do
navegador. O servidor aceita a conexão, espera `{"tipo":"auth","token":"…"}` por
5s e fecha se não vier.

Três diferenças em relação ao que esta seção previa, cada uma por um motivo:

- **`pedido.*` leva o pedido inteiro**, não `{id, status}`. É mais bytes numa
  rede que é uma loja só, em troca de um formato só: quem recebe redesenha do
  mesmo jeito tenha o pedido nascido, mudado de status ou saído na impressora.
- **`metricas.tick` leva o resumo completo**, não os três números do topo. O PWA
  do dono foi escrito pra nunca mostrar o total novo com o ranking velho; um
  evento magro o obrigaria justamente a isso. O resumo só é calculado se houver
  dono conectado.
- **`ack.impresso` não existe: o ACK continua sendo REST** (`POST
  /pedidos/{id}/impresso`). A rota já é idempotente e o agente precisa dela de
  qualquer jeito pro caso do socket estar fora do ar — dois caminhos de escrita
  pro mesmo campo seria uma cópia a mais pra manter sem nada em troca. E
  `pedido.falha_impressao` virou `impressora.status`: o que interessa saber não
  é que *uma* comanda falhou, é que *nenhuma* vai sair até alguém olhar a
  impressora.

**O socket acelera; quem garante é o polling.** Nem as duas telas nem o agente
dependem dele pra estarem corretos: todos continuam com a sua varredura
periódica, e o WebSocket só encurta a espera pra menos de um segundo. Se ele
nunca conectar, o sistema inteiro funciona mais devagar — e ninguém fica sem
saber de um pedido.

E a venda **não passa por ele**: quem garante que o pedido sobe é a fila do
IndexedDB, e quem faz sair papel é o próprio celular que vendeu. Um caminho que
só funciona com o socket de pé seria um caminho a menos de confiança.

---

## 6. Fluxo principal: da venda ao papel

```
1. Funcionário monta o pedido no celular e aperta ENVIAR
      │
2. PWA salva em IndexedDB com status PENDENTE_ENVIO e um uuid próprio
      │
3. POST /pedidos ────────────────────────────> Backend
      │                                            │
      │                              4. valida, recalcula o total pelo
      │                                 preço atual do banco (não confia
      │                                 no total que veio do celular),
      │                                 tira o próximo numero_dia,
      │                                 grava pedido + itens com snapshot
      │                                            │
5. <──── 201 { id, numero_dia } ────────────────────┤
   PWA marca como ENVIADO, limpa o carrinho         │
                                                    │
6. PWA monta o texto da comanda (comanda.js)        │
      │                                             │
7. Intent ──> RawBT ──> IMPRESSORA TÉRMICA          │
      │                                             │
      │                       8. broadcast `pedido.novo`
      │                            └─────> Celular do dono (números sobem)
      │                                             │
9. POST /pedidos/{id}/impresso ─────────────────────┤
                                                    │
                            10. grava impresso_em ✓
```

O passo 6 acontece porque o passo 5 aconteceu: antes da resposta do servidor não existe
`numero_dia`, e comanda sem número é papel que ninguém casa com o pedido do balcão.

O passo 9 fecha o ciclo mesmo sem confirmação da impressora — ver a ressalva da §3.1
sobre o que `impresso_em` passou a significar. Se o passo 7 nem sair (app em segundo
plano, aparelho sem RawBT), a comanda fica na fila local e uma faixa no topo da tela de
vendas diz quantas estão esperando, com o toque que tenta de novo.

### Cupom impresso

```
      SORVETERIA SHALON
 ═══════════════════════════
      PEDIDO  #37
  11/08/2026        19:42
  Atendente: João
 ───────────────────────────
  2x  Casquinha 1 bola
  1x  Açaí 500ml
  1x  Coca-Cola lata
 ───────────────────────────
  Obs: sem granulado
 ═══════════════════════════
        TOTAL  R$ 34,00
```

---

## 7. Quando a internet cai

O ponto fraco de ter o servidor na nuvem. Como cada peça reage:

| Componente | Comportamento |
|---|---|
| PWA Vendas | Cardápio já está em cache — continua vendendo. Pedidos vão pra fila no IndexedDB e sobem sozinhos quando a conexão volta. Um aviso amarelo mostra "3 pedidos aguardando envio". |
| Impressão | Nada muda: o Intent é local. A comanda só espera o `numero_dia`, então o papel sai quando a venda subir — e sai marcado `REIMPRESSAO` se demorar mais de 10 minutos, porque a essa altura alguém já pode ter montado o pedido pela anotação à mão. |
| Agente (se em uso) | Fica tentando reconectar (backoff). Ao voltar, chama `/pedidos/nao-impressos` e imprime o que ficou pra trás. |
| PWA Dono | Mostra os últimos números conhecidos com a hora da última atualização. |

O risco real que sobra: se a internet ficar fora por muito tempo, os pedidos não são
numerados, a comanda não sai e o atendimento vira papel e caneta. Se isso acontecer com frequência, a saída
é migrar pro modelo "local + sync" — por isso o agente e o backend falam por uma
interface bem definida, que permite virar essa chave depois sem reescrever os PWAs.

---

## 8. Estrutura de pastas

```
shalon/
├── backend/
│   ├── app/
│   │   ├── main.py                 # FastAPI, rotas, CORS, static
│   │   ├── config.py               # settings via env
│   │   ├── db.py                   # engine + session
│   │   ├── models/                 # SQLAlchemy
│   │   ├── schemas/                # Pydantic
│   │   ├── rotas/
│   │   │   ├── auth.py
│   │   │   ├── cardapio.py
│   │   │   ├── pedidos.py
│   │   │   ├── relatorios.py
│   │   │   └── ws.py
│   │   └── servicos/
│   │       ├── pedidos.py          # numeração, idempotência, total
│   │       ├── relatorios.py
│   │       ├── dia_operacional.py
│   │       └── eventos.py          # hub do WS: conexões por papel
│   ├── alembic/
│   ├── tests/
│   └── pyproject.toml
│
├── frontend/
│   ├── vendas/                     # PWA dos funcionários
│   │   ├── index.html
│   │   ├── app.js
│   │   ├── fila.js                 # IndexedDB + reenvio
│   │   ├── comanda.js              # o layout do papel
│   │   ├── rawbt.js                # o Intent do Android
│   │   ├── impressao.js            # quando imprimir + fila + ACK
│   │   ├── sw.js                   # service worker
│   │   └── manifest.json
│   ├── dono/                       # PWA do dono
│   │   └── (mesma estrutura)
│   └── comum/                      # api.js, ws.js, css
│
├── agente/
│   ├── main.py                     # laço: WebSocket + varredura + fila
│   ├── api.py                      # cliente REST (login, ACK, não-impressos)
│   ├── config.py                   # leitura do config.ini
│   ├── impressoras/
│   │   ├── base.py                 # Protocol Impressora
│   │   ├── escpos_usb.py
│   │   ├── escpos_rede.py
│   │   ├── spooler_windows.py
│   │   └── fake.py
│   ├── cupom.py                    # formatação do papel
│   ├── tests/
│   ├── config.ini.exemplo          # o config.ini real fica fora do git (senha)
│   └── build.spec                  # PyInstaller → shalon-agente.exe
│
├── docker-compose.yml
├── Caddyfile
└── ARCHITECTURE.md
```

**Front-end sem build step:** HTML + JavaScript puro, servido como arquivos estáticos
pelo próprio FastAPI. Sem npm, sem webpack, sem node_modules. Para 2 telas simples isso
é mais rápido de fazer e muito mais fácil de manter do que React — e um PWA não precisa
de framework pra funcionar.

**E sem layout de tablet nem de desktop.** As duas telas são de celular e só: a grade de
produtos, o carrinho que sobe de baixo e a folha de acompanhamentos são desenhos de tela
estreita, e mantê-los funcionando também em duas colunas era um segundo layout pra
conferir a cada mexida, num aparelho que ninguém usa. Numa tela larga o app trava numa
coluna da largura de um celular em vez de esticar — mesmo layout, sem uma segunda
versão. A página nunca rola: quem rola é uma área interna, para que a barra do navegador
não apareça e desapareça movendo o botão ENVIAR bem na hora em que o dedo já ia nele.

---

## 9. Deploy

```
Internet
   │
   v
Caddy (HTTPS automático, Let's Encrypt)     ← HTTPS é obrigatório:
   │                                           sem ele o PWA não instala
   ├──> Uvicorn/FastAPI  (container)           nem roda service worker
   └──> arquivos estáticos dos PWAs
             │
             v
        PostgreSQL (container + volume)
             │
             └──> backup diário (pg_dump → storage externo)
```

- **Hospedagem:** VPS pequena (Hetzner CX22, ~R$30/mês) com Docker Compose, ou Railway/
  Render se preferir não administrar servidor. Uma sorveteria cabe folgada no menor plano.
- **Domínio:** algo como `shalon.app.br` — necessário pro certificado e pra instalar o PWA.
- **Instalação nos celulares:** abrir o link no Chrome → "Adicionar à tela inicial". Vira
  ícone, abre em tela cheia, sem barra de navegador.
- **No celular do balcão, mais um passo:** instalar o RawBT, parear a térmica e imprimir
  a página de teste por ele uma vez. Se a página de teste não sai, o PWA também não vai
  fazer sair — o problema está entre o RawBT e a impressora.
- **Um worker só do Uvicorn** na v1: o gerenciador de WebSocket guarda as conexões em
  memória. Se um dia precisar escalar pra vários workers, entra Redis pub/sub — não é
  problema pro tamanho de uma sorveteria.
- **Backup:** `pg_dump` diário. É o dado financeiro do negócio.

---

## 10. Fora do escopo da v1 (mas o modelo já comporta)

Deixados de fora de propósito, com o ponto de extensão já mapeado:

| Recurso | Como entra depois |
|---|---|
| Sabor do milk-shake | Mesmo mecanismo dos acompanhamentos (`opcao_grupo`), que já está de pé — falta só o dono decidir a lista. |
| Forma de pagamento | Coluna `forma_pagamento` em `pedido` + quebra por forma no fechamento. |
| Controle de estoque | Tabela `insumo` + `ficha_tecnica` ligando produto→insumo, com baixa no `pedido.criado`. |
| Nota fiscal (NFC-e/SAT) | Integração à parte; o cupom atual é comanda de produção. |
| Delivery / iFood | Novo canal de entrada de pedido, mesmo fluxo daí pra frente. |
| Metas e comparativo de meses | Só relatório, já dá com os dados que existem. |

---

## 11. Ordem de construção

| Fase | Entrega | Como sei que funcionou | |
|---|---|---|---|
| 0 | Docker Compose, Postgres, FastAPI de pé, migrations | `/health` responde | ✅ |
| 1 | Modelos, cardápio (com acompanhamentos), login | Cardápio impresso inteiro no banco | ✅ |
| 2 | PWA Vendas: cardápio, carrinho, envio | Pedido cai no banco pelo celular | ✅ |
| 3 | Agente + impressão + reimpressão | **Sai papel na impressora** | ✅* |
| 4 | WebSocket + tela da cozinha com status | Pedido aparece na cozinha na hora | ⊘ |
| 5 | PWA Dono: números ao vivo + editar preço | Preço muda no celular da loja na hora | ✅ |
| 6 | Fechamento do dia por item e total | O relatório bate com o caixa | ✅ |
| 7 | Deploy, HTTPS, instalar os PWAs, backup | Rodando na sorveteria de verdade | ⬜ |
| 8 | Impressão pelo RawBT, no celular do balcão | Comanda sai sem PC nenhum | ✅* |

**\*** As fases 3 e 8 levam asterisco porque são as únicas que dependem de
hardware físico, e a impressora ainda não foi comprada.

A fase 8 é a que vale hoje: `comanda.js`, `rawbt.js` e `impressao.js` estão
escritos e exercitados — o texto do cupom sai byte a byte igual ao do
`agente/cupom.py`, a fila guarda e retenta o que não pôde ser despachado, o
mesmo pedido não vira duas vias e o ACK só vai depois do despacho. O único
trecho que nunca foi exercitado é o que ninguém consegue exercitar sem a
máquina: o Intent chegando ao RawBT e o RawBT chegando à bobina.

A fase 3, o agente de PC, continua escrita e testada contra a `ImpressoraFake` —
venda no celular, evento no socket, cupom formatado, ACK de volta, reimpressão
marcada, e a varredura recuperando o que ficou pra trás enquanto ele esteve
desligado. `EscPosUSB`, `EscPosRede` e `SpoolerWindows` estão implementados e
não têm como ser testados sem a máquina na frente.

**⊘** A fase 4 foi entregue e depois **removida**. O WebSocket ficou; a tela da
cozinha saiu (§1). Ela funcionou como especificada, e o motivo de sumir não foi
defeito: numa sorveteria de balcão quem monta o sorvete é quem vendeu, com a
comanda de papel na mão, e o monitor era uma segunda cópia da mesma informação
que ninguém parava pra olhar.

Só a fase 7 continua aberta.

### O que já está de pé

Backend com 142 testes passando, mais 20 do agente: login por senha com trava de força bruta e refresh
rotativo, cardápio com auditoria de preço, e a API de pedidos inteira — criação
idempotente por `id_cliente`, numeração atômica do dia, snapshot de preço, fila de
impressão (`/pedidos/nao-impressos`, `/pedidos/{id}/impresso`, `/reimprimir`), ciclo
de status e cancelamento com motivo.

O cardápio impresso da loja está no `app.seed`: 26 produtos em 8 categorias e os
grupos de acompanhamentos, adicionais, bordas e coberturas com a cota de cada um
("3 acompanhamentos" no sundae, 4 no açaí montado).

**Duas escolhas são obrigatórias, e é de propósito.** A cobertura do sorvete e a
borda do trufado abrem com o botão ADICIONAR travado até alguém marcar uma —
inclusive a opção "Sem cobertura", que existe justamente para isso. Cota mínima
zero deixava o botão liberado sem ninguém ter perguntado nada, e a comanda saía
muda sobre a cobertura: quem monta não distinguia "o cliente não quis" de "o
atendente passou reto". O botão travado diz qual grupo está faltando, porque a
folha do trufado tem dois grupos e rola.

O seed é idempotente, e divide o cardápio em duas metades com donos diferentes.
**Preço e ativo são do dono:** ele os edita pela tela e o seed nunca os escreve
de volta — seria o cardápio voltando sozinho ao preço de agosto a cada
reinício. **A estrutura é do arquivo:** a ordem das opções e a cota de cada
grupo não têm tela que as edite, então o seed as sincroniza. Sem isso, mexer
numa cota aqui não teria efeito em banco nenhum que já existisse, incluindo o de
produção, e a mudança sumiria em silêncio. `tests/test_seed.py` fixa as duas
metades.

PWA de Vendas rodando em `frontend/vendas/`, com a fila offline do §7 funcionando:
a venda é gravada no IndexedDB antes de qualquer ida ao servidor, o carrinho limpa na
hora e a fila sobe sozinha quando a conexão volta. Só sobem os pedidos do funcionário
logado — o pedido carrega quem vendeu, e mandar com o token de outro faria o relatório
mentir.

Tocar num açaí, sundae ou cestinha abre a folha de acompanhamentos: cota respeitada na
tela, adicional pago com o preço no próprio botão e o total da unidade ao vivo. Dois
açaís com acompanhamentos diferentes são duas linhas do carrinho — somar num "2x" faria
a cozinha montar os dois iguais. A tela só evita o erro; quem valida cota e preço é o
servidor.

PWA do Dono rodando em `frontend/dono/`, com as três abas da §2.3. Os números
não são calculados no celular: total, ticket médio e ranking vêm prontos de
`/relatorios/hoje`, em centavos, numa resposta só — três chamadas separadas
dariam três chances de mostrar o total novo com o ranking velho.

**O balcão desfaz a própria venda.** No celular de vendas, a lista de últimos
pedidos tem o ✕ que cancela: o valor sai do faturamento na hora, o pedido fica
marcado como CANCELADO na lista e aparece destacado no painel do dono, que vê o
número junto — "2 pedidos cancelados hoje (R$ 14,00), fora do total".

A permissão foi aberta de propósito, mas com limite: o funcionário cancela o
que **ele** vendeu **hoje**; venda de outro atendente ou de outro dia só o dono
desfaz, porque qualquer um dos dois casos é mexer em movimento que já foi
conferido. O motivo é obrigatório e sai de botões prontos ("cliente desistiu",
"pedido errado"), não de um campo em branco — no balcão, com cliente na frente,
campo em branco vira "erro" digitado às pressas ou nada. Fica gravado com o
autor.

O que protege o caixa aqui não é a dificuldade de cancelar, é o registro. A
alternativa — exigir o dono pra cada engano de digitação — deixaria a fila
parada e, na prática, faria o funcionário simplesmente não corrigir, o que é
pior: um pedido errado que fica no total.

Cancelado sai do faturamento e é contado à parte; adicional pago entra no valor
do item que o levou. O fechamento é imutável e vai com o total que estava na
tela: se uma venda entrou entre a conferência e o toque no botão, o servidor
recusa em vez de congelar um número que o dono não aprovou. Venda que sobe da
fila offline depois disso entra marcada como `pos_fechamento` e aparece
destacada — sem isso o dono compararia relatório e gaveta e acharia que faltou.

**Login por nome de usuário e senha, e mais nada na tela.** A versão anterior
listava os usuários pra tocar num — o que entregava a qualquer pessoa com o
link os nomes de quem trabalha na loja e qual deles era o dono, e punha o botão
do dono ali no celular do balcão, convidando. A rota que devolvia essa lista
deixou de existir junto com a tela.

**A senha é pedida uma vez por aparelho.** O refresh dura um ano e desliza a
cada renovação: funcionário deslogado no meio de um sábado é fila parada. O que
substitui o prazo curto é o controle explícito — `GET /auth/sessoes` mostra ao
dono cada aparelho conectado, e `DELETE /auth/sessoes/{id}` o derruba. Revogar
mata o refresh na hora, mas o token de acesso que o aparelho já tem vale até
vencer, então o corte leva até 30 minutos; validar o token contra o banco a
cada chamada custaria uma consulta por requisição de toda tela, o dia inteiro,
pra cobrir um caso que acontece uma vez por ano. A tela diz isso em vez de
prometer o que a rota não entrega.

O painel do dono não abre pra quem não é dono: o papel vem do token, não da
tela. Sem essa checagem a funcionária entraria com a própria senha e veria o
faturamento — as rotas de relatório respondem 403, mas a tela abriria vazia sem
explicar por quê.

Impressão pelo RawBT rodando em `frontend/vendas/`. A comanda sai do celular
que vendeu, sozinha, assim que o servidor confirma o pedido — o detalhe do
mecanismo e das trocas está na §3.1. Três arquivos com uma responsabilidade
cada: `comanda.js` desenha o papel, `rawbt.js` fala com o Android, e
`impressao.js` decide quando imprimir. O texto que o `comanda.js` produz é
idêntico, caractere a caractere, ao do `agente/cupom.py` — os dois caminhos de
impressão precisam entregar a mesma comanda, senão ninguém confere o papel
contra o pedido.

O `POST /pedidos/{id}/impresso` passou a aceitar o papel `FUNCIONARIO`: é o
celular do balcão que confirma agora. É a permissão mais fraca do sistema —
carimba uma data num pedido que o próprio aparelho acabou de criar, sem tocar em
dinheiro nem em status.

Agente de impressão em `agente/`, como alternativa (§2.4). O laço dele tem duas
fontes pro mesmo trabalho de propósito: o `pedido.novo` do WebSocket dá o papel
em menos de um segundo, e uma varredura de `/pedidos/nao-impressos` a cada 30s é
o que garante que nenhuma comanda se perca quando o Wi-Fi cai, o socket morre
sem avisar ou a bobina acaba. Só confirma depois que imprimiu — confirmar antes
transformaria impressora travada em comanda que ninguém vai buscar. E erra pro
lado de imprimir duas vezes: se cair entre o papel e o ACK, a comanda sai de
novo marcada como REIMPRESSÃO, porque o erro oposto é o cliente esperando no
balcão.

O WebSocket está de pé e as duas telas assinam. O total sobe no celular do dono
na hora e o preço editado chega no balcão em menos de 1 segundo, como a §2.3
promete. **Mas o polling continua ligado nas duas** — só mais espaçado (60s no
dono) enquanto o socket está de pé, voltando ao ritmo curto quando ele cai.
Nenhuma tela depende do socket pra estar correta; ele só encurta a espera.

Uma coisa no backend sobreviveu à tela da cozinha e vale registrar: as datas de
saída usam o tipo `Utc` (`app/schemas/tipos.py`) em vez de `datetime` cru. Sem o
fuso no JSON o navegador lê a hora UTC como local e a comanda sai impressa com
três horas a mais. Como o Postgres devolve datetime com fuso e o SQLite sem,
esse descuido só aparece em dev, onde é fácil culpar "coisa do SQLite" e seguir.

Saiu junto com a tela o `frontend/comum/relogio.js`, que corrigia o desvio do
relógio do PC da cozinha. Ele existia por uma conta só — "esta comanda passou de
15 segundos sem imprimir" — e sem a tela que fazia a conta, ninguém mais lia o
número que ele produzia.
