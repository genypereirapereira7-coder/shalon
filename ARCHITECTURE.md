# Shalon — Arquitetura do Sistema

Sistema de pedidos e gestão para a sorveteria Shalon.

**Stack:** Python (FastAPI) no backend, 2 PWAs, agente Python no PC da cozinha para impressão.

---

## 1. Visão geral

Quatro peças, três delas rodando em máquinas diferentes:

```
┌─────────────────────────┐         ┌─────────────────────────┐
│  PWA VENDAS             │         │  PWA DONO               │
│  celular da sorveteria  │         │  celular do dono        │
│  (funcionários)         │         │                         │
│  - cardápio             │         │  - vendas em tempo real │
│  - monta pedido         │         │  - editar preços        │
│  - envia                │         │  - fechamento do dia    │
└───────────┬─────────────┘         └───────────┬─────────────┘
            │                                   │
            │  HTTPS + WebSocket                │
            └───────────────┬───────────────────┘
                            │
                            v
            ┌───────────────────────────────────┐
            │  BACKEND (nuvem)                  │
            │  FastAPI + PostgreSQL             │
            │  - regras de negócio              │
            │  - fonte única da verdade         │
            │  - hub WebSocket (tempo real)     │
            └───────────────┬───────────────────┘
                            │  WebSocket
                            v
            ┌───────────────────────────────────┐
            │  PC DA COZINHA                    │
            │  ┌─────────────────────────────┐  │
            │  │ AGENTE DE IMPRESSÃO (Python)│  │
            │  │ recebe pedido → imprime     │  │
            │  └──────────────┬──────────────┘  │
            │                 v                 │
            │           IMPRESSORA TÉRMICA      │
            │                                   │
            │  ┌─────────────────────────────┐  │
            │  │ TELA DA COZINHA (navegador) │  │
            │  │ pedidos: preparo/pronto     │  │
            │  └─────────────────────────────┘  │
            └───────────────────────────────────┘
```

**Por que o servidor fica na nuvem:** o dono precisa ver os números de qualquer lugar,
não só dentro da loja. O preço disso é que a sorveteria depende de internet — por isso
o PWA de vendas guarda os pedidos localmente e reenvia quando a conexão volta (seção 7).

---

## 2. Os quatro componentes

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

Roda no celular que fica na sorveteria. Tela única de venda:

```
┌──────────────────────────┐
│  Shalon        João ▾    │   ← funcionário identificado
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
│ [ ENVIAR PARA A COZINHA ]│
└──────────────────────────┘
```

### 2.3 PWA Dono — `frontend/dono/`

Roda no celular do dono. Três telas:

- **Hoje** — total vendido, nº de pedidos, ticket médio, ranking dos itens mais vendidos,
  atualizando sozinho conforme as vendas entram (sem apertar refresh).
- **Cardápio** — lista de produtos com o preço editável ali mesmo. Ao salvar, o preço
  novo chega no celular da sorveteria em menos de 1 segundo.
- **Fechamento** — resumo do dia: cada item, quantidade vendida, valor total por item,
  total geral. Histórico por data.

### 2.4 Agente da Cozinha — `agente/`

Programa Python que roda no PC da cozinha, instalado como um `.exe` único (PyInstaller)
que inicia junto com o Windows. Ele:

1. Abre uma conexão WebSocket com o backend e fica escutando.
2. Ao chegar um pedido, formata o cupom e manda pra impressora.
3. Confirma pro servidor que imprimiu (`ACK`).
4. Ao religar ou reconectar, pergunta ao servidor quais pedidos do dia ficaram sem imprimir.
5. Abre o navegador na tela da cozinha em modo quiosque.

**A tela da cozinha é uma página do próprio backend** (`/cozinha`) aberta no navegador do
PC, não uma janela do agente. Assim a interface é uma só e o agente fica minúsculo — só
imprime.

---

## 3. Camada de impressão (trocável)

A impressora ainda não foi comprada, então o agente conversa com uma interface, não com
um modelo específico:

```python
class Impressora(Protocol):
    def imprimir(self, cupom: Cupom) -> None: ...
    def esta_ok(self) -> bool: ...
```

Implementações:

| Classe | Quando usar |
|---|---|
| `EscPosUSB` | Térmica de bobina ligada por USB — **o caminho principal** |
| `EscPosRede` | Térmica com porta de rede (IP fixo) |
| `SpoolerWindows` | Impressora comum: gera PDF e joga na fila do Windows |
| `ImpressoraFake` | Desenvolvimento: escreve o cupom num `.txt` |

Biblioteca: `python-escpos`.

### Recomendação de compra

Compre uma **térmica 80mm USB com padrão ESC/POS**. Modelos que funcionam bem com
`python-escpos` e são fáceis de achar no Brasil:

- **Elgin i9** (USB) — melhor custo-benefício
- **Epson TM-T20X** — a mais confiável, suporte excelente
- **Bematech MP-4200 TH** — comum em assistência técnica

Três detalhes que importam:

- **80mm, não 58mm** — o cupom de 58mm é estreito demais, o pedido fica ilegível.
- **USB, não Bluetooth** — Bluetooth com PC cai e dá dor de cabeça.
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
POST   /auth/login                  PIN do funcionário / senha do dono → JWT
POST   /auth/parear                 primeiro acesso do celular (código de pareamento)

GET    /cardapio                    categorias + produtos ativos (o PWA cacheia)
PATCH  /produtos/{id}               editar preço/nome/ativo          [DONO]
POST   /produtos                    criar produto                    [DONO]

POST   /pedidos                     criar pedido (idempotente por id_cliente)
GET    /pedidos/hoje                pedidos do dia operacional
PATCH  /pedidos/{id}/status         mudar status                     [COZINHA]
POST   /pedidos/{id}/cancelar       cancelar (exige motivo)          [DONO]
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
| `pedido.novo` | cozinha, agente, dono | pedido completo |
| `pedido.status` | vendas, cozinha, dono | pedido completo |
| `pedido.impresso` | cozinha, dono | pedido completo |
| `preco.alterado` | vendas, cozinha, dono | produto completo |
| `metricas.tick` | dono | o resumo do dia inteiro |
| `impressora.status` | cozinha, dono | ok, detalhe |

E do agente/telas para o servidor: `auth` (obrigatória, a primeira), `ping` e
`impressora.status`.

**A autenticação é a primeira mensagem, não a URL.** O navegador não deixa pôr
cabeçalho `Authorization` num WebSocket, e o caminho comum — `/ws?token=…` —
gravaria o token de acesso no log de requisições do Caddy e no histórico do
navegador. O servidor aceita a conexão, espera `{"tipo":"auth","token":"…"}` por
5s e fecha se não vier.

Três diferenças em relação ao que esta seção previa, cada uma por um motivo:

- **`pedido.*` leva o pedido inteiro**, não `{id, status}`. É mais bytes numa
  rede que é uma loja só, em troca de um formato só: a tela da cozinha redesenha
  a comanda do mesmo jeito tenha ela nascido, mudado de status ou saído na
  impressora.
- **`metricas.tick` leva o resumo completo**, não os três números do topo. O PWA
  do dono foi escrito pra nunca mostrar o total novo com o ranking velho; um
  evento magro o obrigaria justamente a isso. O resumo só é calculado se houver
  dono conectado.
- **`ack.impresso` não existe: o ACK continua sendo REST** (`POST
  /pedidos/{id}/impresso`). A rota já é idempotente e o agente precisa dela de
  qualquer jeito pro caso do socket estar fora do ar — dois caminhos de escrita
  pro mesmo campo seria uma cópia a mais pra manter sem nada em troca. E
  `pedido.falha_impressao` virou `impressora.status`: o que a cozinha precisa
  saber não é que *uma* comanda falhou (o prazo de 15s já diz isso), é que
  *nenhuma* vai sair até alguém olhar a impressora.

**O socket acelera; quem garante é o polling.** Nenhuma das três telas nem o
agente dependem dele pra estarem corretos: todos continuam com a sua varredura
periódica, e o WebSocket só encurta a espera pra menos de um segundo. Se ele
nunca conectar, o sistema inteiro funciona mais devagar — e ninguém fica sem
saber de um pedido.

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
                             6. broadcast `pedido.novo`
                                  ├──────────> Agente da cozinha
                                  ├──────────> Tela da cozinha
                                  └──────────> Celular do dono (números sobem)
                                                    │
7. Agente imprime a comanda ────> IMPRESSORA        │
      │                                             │
8. `ack.impresso` ──────────────────────────────────┤
                                                    │
                             9. grava impresso_em,
                                avisa a tela da cozinha ✓
```

Se o passo 8 não chegar em 15 segundos, o pedido aparece na tela da cozinha marcado em
vermelho com um botão **REIMPRIMIR**. Ninguém descobre que a impressora travou pelo
cliente reclamando.

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
| Agente | Fica tentando reconectar (backoff). Ao voltar, chama `/pedidos/nao-impressos` e imprime o que ficou pra trás. |
| Tela da cozinha | Mantém na tela os pedidos que já tinha e mostra "sem conexão". |
| PWA Dono | Mostra os últimos números conhecidos com a hora da última atualização. |

O risco real que sobra: se a internet ficar fora por muito tempo, os pedidos não chegam
na cozinha e o atendimento vira papel e caneta. Se isso acontecer com frequência, a saída
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
│   │   ├── sw.js                   # service worker
│   │   └── manifest.json
│   ├── dono/                       # PWA do dono
│   │   └── (mesma estrutura)
│   ├── cozinha/                    # tela do PC da cozinha
│   └── comum/                      # api.js, ws.js, relogio.js, css
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
│   ├── config.ini.exemplo          # o config.ini real fica fora do git (PIN)
│   └── build.spec                  # PyInstaller → shalon-agente.exe
│
├── docker-compose.yml
├── Caddyfile
└── ARCHITECTURE.md
```

**Front-end sem build step:** HTML + JavaScript puro, servido como arquivos estáticos
pelo próprio FastAPI. Sem npm, sem webpack, sem node_modules. Para 3 telas simples isso
é mais rápido de fazer e muito mais fácil de manter do que React — e um PWA não precisa
de framework pra funcionar.

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
| 1 | Modelos, cardápio (com acompanhamentos), login por PIN | Cardápio impresso inteiro no banco | ✅ |
| 2 | PWA Vendas: cardápio, carrinho, envio | Pedido cai no banco pelo celular | ✅ |
| 3 | Agente + impressão + reimpressão | **Sai papel na impressora** | ✅* |
| 4 | WebSocket + tela da cozinha com status | Pedido aparece na cozinha na hora | ✅ |
| 5 | PWA Dono: números ao vivo + editar preço | Preço muda no celular da loja na hora | ✅ |
| 6 | Fechamento do dia por item e total | O relatório bate com o caixa | ✅ |
| 7 | Deploy, HTTPS, instalar os PWAs, backup | Rodando na sorveteria de verdade | ⬜ |

**\*** A fase 3 leva asterisco porque é a única que depende de hardware físico, e a
impressora ainda não foi comprada. O agente está escrito, testado e roda o fluxo
inteiro da §6 de ponta a ponta contra a `ImpressoraFake` — venda no celular,
evento no socket, cupom formatado, ACK de volta, reimpressão marcada, e a
varredura recuperando o que ficou pra trás enquanto ele esteve desligado. O
único trecho que nunca foi exercitado é o cabo: `EscPosUSB`, `EscPosRede` e
`SpoolerWindows` estão implementados e não têm como ser testados sem a máquina
na frente. Trocar a `fake` por uma delas é uma linha no `config.ini`.

Só a fase 7 continua aberta.

### O que já está de pé

Backend com 128 testes passando, mais 15 do agente: login por PIN com trava de força bruta e refresh
rotativo, cardápio com auditoria de preço, e a API de pedidos inteira — criação
idempotente por `id_cliente`, numeração atômica do dia, snapshot de preço, fila de
impressão (`/pedidos/nao-impressos`, `/pedidos/{id}/impresso`, `/reimprimir`), ciclo
de status e cancelamento com motivo.

O cardápio impresso da loja está no `app.seed`: 25 produtos em 8 categorias e os
grupos de acompanhamentos, adicionais e coberturas com a cota de cada um ("3
acompanhamentos" no sundae, 4 no açaí montado). O seed é idempotente e nunca
sobrescreve preço já alterado pelo dono.

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

PWA do Dono rodando em `frontend/dono/`, com as três telas da §2.3. Os números
não são calculados no celular: total, ticket médio e ranking vêm prontos de
`/relatorios/hoje`, em centavos, numa resposta só — três chamadas separadas
dariam três chances de mostrar o total novo com o ranking velho.

Cancelado sai do faturamento e é contado à parte; adicional pago entra no valor
do item que o levou. O fechamento é imutável e vai com o total que estava na
tela: se uma venda entrou entre a conferência e o toque no botão, o servidor
recusa em vez de congelar um número que o dono não aprovou. Venda que sobe da
fila offline depois disso entra marcada como `pos_fechamento` e aparece
destacada — sem isso o dono compararia relatório e gaveta e acharia que faltou.

Tela da Cozinha rodando em `frontend/cozinha/`, no monitor do PC da produção.
Ela é a única tela que ninguém fica olhando: comanda nova entra com som e vai
pro topo da fila, porque o custo de passar despercebida é o cliente esperando
um sorvete que ninguém começou. Comanda sem ACK de impressão em 15s (§6) pulsa
em vermelho com o botão REIMPRIMIR — descobrir a impressora travada pelo
cliente reclamando é o jeito errado. Queda de conexão não limpa a lista: as
comandas ficam com um aviso de que parou de atualizar, porque sumir com o
pedido de alguém é pior do que mostrá-lo velho.

Duas coisas no backend nasceram dessa tela. `/pedidos/hoje` ganhou filtro de
status: recarregando de 5 em 5s, baixar o dia inteiro num sábado seria quase
tudo comanda já entregue. E as datas de saída passaram a usar o tipo `Utc`
(`app/schemas/tipos.py`) em vez de `datetime` cru — sem o fuso no JSON o
navegador lê a hora UTC como local, a comanda parece criada três horas no
futuro e o alerta de impressora travada nunca acende. Como o Postgres devolve
datetime com fuso e o SQLite sem, esse descuido só aparece em dev, onde é fácil
culpar "coisa do SQLite" e seguir.

Agente de impressão rodando em `agente/`. É o único pedaço que toca hardware, e
o laço dele tem duas fontes pro mesmo trabalho de propósito: o `pedido.novo` do
WebSocket dá o papel em menos de um segundo, e uma varredura de
`/pedidos/nao-impressos` a cada 30s é o que garante que nenhuma comanda se perca
quando o Wi-Fi cai, o socket morre sem avisar ou a bobina acaba. Só confirma
depois que imprimiu — confirmar antes transformaria impressora travada em
comanda que ninguém vai buscar. E erra pro lado de imprimir duas vezes: se cair
entre o papel e o ACK, a comanda sai de novo marcada como REIMPRESSÃO, porque o
erro oposto é o cliente esperando no balcão.

O WebSocket está de pé e as três telas assinam. A comanda aparece na cozinha e
o total sobe no celular do dono na hora, e o preço editado chega no balcão em
menos de 1 segundo como a §2.3 promete. **Mas o polling continua ligado em
todas elas** — só mais espaçado (20s na cozinha, 60s no dono) enquanto o socket
está de pé, voltando ao ritmo curto quando ele cai. Nenhuma tela depende do
socket pra estar correta; ele só encurta a espera.

Uma correção que veio junto: a tela da cozinha comparava `criado_em` do servidor
com o `Date.now()` do PC pra decidir se a impressora travou. Um PC de cozinha
pode ficar meses sem sincronizar o relógio, e uns poucos minutos de desvio
acendem o alerta vermelho em todas as comandas ou em nenhuma. Agora os dois
lados da conta saem do mesmo relógio (`frontend/comum/relogio.js`), acertado
pelo `/health` e pelo `pronto` do socket.
