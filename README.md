# Shalon

Sistema de pedidos da sorveteria Shalon: um backend FastAPI e dois PWAs que
rodam **no celular** — o do balcão e o do dono. A arquitetura, as decisões e o
que ainda falta estão em [ARCHITECTURE.md](ARCHITECTURE.md).

Não há tela de PC em lugar nenhum: as duas telas são desenhadas para aparelho na
mão, e a comanda sai da térmica ligada ao próprio celular do balcão, pelo
[RawBT](https://play.google.com/store/apps/details?id=ru.a402d.rawbtprinter).

## O que já existe

| Parte | Estado |
| --- | --- |
| Backend (auth, cardápio, pedidos, relatórios, fechamento) | funcionando |
| PWA de vendas (`frontend/vendas/`) | funcionando |
| PWA do dono (`frontend/dono/`) | funcionando |
| Impressão pelo RawBT (`frontend/vendas/impressao.js`) | funcionando |
| WebSocket (`/ws`) | funcionando |
| Agente de impressão em PC (`agente/`) | alternativa, sem impressora de verdade |

O `backend/app/main.py` monta as duas pastas de PWA; as que não existem são
simplesmente ignoradas na subida.

O total sobe no celular do dono em menos de um segundo, pelo WebSocket. **O
polling continua ligado por baixo nas duas telas**, só mais espaçado — nenhuma
delas depende do socket pra estar correta, e se ele nunca conectar tudo funciona
mais devagar em vez de quebrar.

### A comanda

Quem imprime é o celular que vendeu. Assim que o servidor confirma o pedido — e
não antes, porque é aí que existe o `numero_dia` —, o PWA formata o cupom de 48
colunas e o entrega ao RawBT por um *Intent* do Android. Sai sozinho, por trás,
sem ninguém apertar nada e sem o app sair da frente: o Intent viaja num iframe
escondido, e não numa troca de página, justamente pra que o funcionário continue
na venda seguinte.

Três arquivos, uma responsabilidade cada:

| Arquivo | Responsabilidade |
| --- | --- |
| `frontend/vendas/comanda.js` | o layout do papel — texto puro, sem DOM e sem rede |
| `frontend/vendas/rawbt.js` | o Intent do Android; não sabe o que está imprimindo |
| `frontend/vendas/impressao.js` | *quando* imprimir, a fila de retentativa e o ACK |

O `impressao.js` recebe os outros dois como dependência: trocar o RawBT por
outro aplicativo de impressão, ou o desenho do cupom, é trocar o que entra na
fábrica `criarImpressora()` — nenhum dos outros arquivos muda.

**O que o navegador não consegue saber:** se saiu papel. O Intent é de mão
única, sem retorno. Por isso o `impresso_em` que o celular grava no servidor quer
dizer "a comanda foi entregue à impressora deste aparelho", e não "o papel está
na bandeja". Com a térmica desligada, quem percebe é o balcão — e reimprime pela
lista de últimos pedidos (☰ → ⎙), que sai marcada `*** REIMPRESSAO ***`.

Comanda que não pôde ser despachada (app em segundo plano quando a venda subiu
da fila offline) fica guardada e é tentada de novo quando o app volta pra tela.
Enquanto isso, uma faixa no topo conta quantas estão esperando.

**Preparar o celular do balcão:** instalar o RawBT da Play Store, parear a
térmica nele (Bluetooth, USB-OTG ou rede) e imprimir a página de teste **pelo
próprio RawBT** uma vez. Se a página de teste não sai, o PWA também não vai
fazer sair — o problema está entre o RawBT e a impressora, não no sistema.

### O agente de PC (alternativa)

O `agente/` continua no repositório para quem preferir prender a térmica a um
PC. **Não rode os dois ao mesmo tempo**: cada um imprimiria a sua via da mesma
comanda. Ele vem com `tipo = fake`, que grava o cupom num `cupons.txt` em vez de
mandar pra bobina — dá pra ver o fluxo inteiro funcionando sem hardware nenhum.
Os drivers de térmica USB, de rede e do spooler do Windows estão implementados e
são uma linha no `config.ini`, mas nunca foram testados contra máquina de
verdade.

## Rodar localmente

Dois caminhos. O **A** é o mais rápido e não precisa de Docker; o **B** roda no
Postgres, igual à produção.

### A) Sem Docker — SQLite (mais rápido)

Precisa de Python 3.12+. São dois comandos:

```bash
cd backend

# 1. ambiente virtual + dependências (só na primeira vez)
python -m venv .venv
.venv/Scripts/python.exe -m pip install -e ".[dev]"     # Windows
# source .venv/bin/activate && pip install -e ".[dev]"  # Linux/macOS

# 2. subir
.venv/Scripts/python.exe dev.py                          # Windows
# .venv/bin/python dev.py                                # Linux/macOS
```

O `dev.py` aponta pro SQLite, cria o banco se não existir, popula o cardápio e
sobe o servidor com reload. No fim ele imprime os links das duas telas.

> **Não rode `uvicorn app.main:app` direto sem exportar as variáveis.** O padrão
> do `config.py` é o Postgres de produção, então o servidor sobe, serve as duas
> telas e responde **500 em toda chamada de API** — a tela de vendas abre e não
> carrega nada. Se acontecer, o arranque avisa em letras garrafais no terminal.

Para escolher a porta ou expor na rede: `PORTA=9000 HOST=0.0.0.0 python dev.py`.

<details>
<summary>Rodar na mão, sem o <code>dev.py</code></summary>

```bash
SHALON_DATABASE_URL="sqlite+aiosqlite:///_dev.db" \
SHALON_JWT_SEGREDO="dev-local-segredo-com-mais-de-32-bytes-ok" \
.venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

No PowerShell as variáveis vão antes, em linhas separadas:

```powershell
$env:SHALON_DATABASE_URL = "sqlite+aiosqlite:///_dev.db"
$env:SHALON_JWT_SEGREDO  = "dev-local-segredo-com-mais-de-32-bytes-ok"
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

O banco precisa existir antes: `python dev.py` cria, ou rode
`python -m app.seed` com a mesma `SHALON_DATABASE_URL`.

</details>

### Se uma tela abrir e não funcionar

Duas causas, nesta ordem:

1. **A API está dando 500.** Abra http://127.0.0.1:8000/health — se disser
   `"banco": "indisponivel"`, é o caso acima: suba pelo `dev.py`.
2. **O navegador está preso numa versão antiga.** Os dois PWAs instalam um
   service worker que guarda a casca do app em cache. Depois de uma alteração,
   force o descarte: F12 → Application → Service Workers → *Unregister*, e
   recarregue com Ctrl+Shift+R. (Em produção o Caddy já impede esse cache; o
   servidor de desenvolvimento passou a impedir também.)

### Ver papel sair

**No celular (o caminho de verdade).** Suba o servidor com `--host 0.0.0.0`,
instale o RawBT no Android, abra `http://<ip-da-maquina>:8000/vendas/` no Chrome
e faça uma venda: a comanda vai pra impressora sozinha. Sem o RawBT instalado, o
Chrome oferece a Play Store na primeira tentativa e o app segue funcionando — só
com a faixa de "comanda não saiu" no topo.

O `intent:` só existe no Android. No Chrome do PC essa faixa aparece em toda
venda, dizendo que a impressão não está disponível neste aparelho — é o
comportamento correto, não um defeito.

**No PC (agente, alternativa).**

Com o servidor no ar, noutro terminal:

```bash
cd agente
cp config.ini.exemplo config.ini     # já vem com tipo = fake

# Roda com o venv do backend — as dependências (httpx, websockets) são as mesmas.
../backend/.venv/Scripts/python.exe main.py     # Windows
# ../backend/.venv/bin/python main.py           # Linux/macOS
```

O `usuario_id` no `config.ini` é o do usuário AGENTE — o `dev.py` imprime a
tabela de ids no arranque. Faça uma venda no PWA e a comanda aparece em
`agente/cupons.txt` — que é o "papel" da `ImpressoraFake`.

Vale desligar o agente, vender duas vezes e religar: ele pergunta ao servidor o
que ficou sem imprimir e recupera as duas.

> Para este teste, venda pelo Chrome do PC, onde a impressão pelo RawBT não
> roda. Com um Android vendendo ao mesmo tempo, o ACK do celular esvazia a fila
> do agente antes de ele chegar nela.

**As migrations do Alembic não rodam em SQLite** — a `0001_inicial` usa
`postgresql.UUID`. Por isso o passo 2 cria o schema direto pelo
`Base.metadata`, que é o mesmo caminho que os testes usam
(`backend/tests/conftest.py`). Para mexer em migration, use o caminho B.

### B) Com Docker — Postgres (igual à produção)

```bash
cp .env.example .env        # ajuste as senhas
docker compose up -d db api
docker compose exec api alembic upgrade head
docker compose exec api python -m app.seed
```

## Publicar no Railway

O `railway.toml` já descreve o build, o start e o healthcheck — o Railway lê
sozinho. O que sobra é criar o banco e três variáveis.

**1. O projeto e o banco.** No painel: *New Project → Deploy from GitHub repo*,
escolha `shalon`. Depois, dentro do projeto, *New → Database → Add PostgreSQL*.

**2. As três variáveis**, no serviço do app (aba *Variables*):

| Variável | Valor |
| --- | --- |
| `SHALON_DATABASE_URL` | `${{Postgres.DATABASE_URL}}` — referência, não copie a string |
| `SHALON_JWT_SEGREDO` | gere com o comando abaixo |
| `SHALON_AMBIENTE` | `prod` |

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Use a **referência** `${{Postgres.DATABASE_URL}}`, não a string copiada: o
Railway troca a senha do banco quando recria o serviço, e a referência
acompanha. Uma string colada à mão vira um serviço que sobe verde e responde
500 em tudo, no dia em que ninguém mexeu em nada.

O formato não precisa de ajuste. O Railway entrega `postgresql://…`, e o
`config.py` traduz pro `postgresql+asyncpg://` que a engine async usa. Se você
esquecer a variável, o `config.py` ainda tenta o `DATABASE_URL` pelado.

**3. O domínio.** Aba *Settings → Networking → Generate Domain*. O endereço
abre direto na tela de vendas.

**Se o build escolher o Python errado**, adicione `NIXPACKS_PYTHON_VERSION` =
`3.12`. O `.python-version` costuma bastar; esta é a saída quando não basta.

**Uma réplica, um worker — e isto não é economia.** O gerenciador de WebSocket
guarda as conexões em memória do processo. Com duas réplicas, metade dos avisos
de pedido novo cairia na réplica errada e nunca chegaria na tela. Se um dia a
loja precisar de mais de uma, o caminho é um Redis no meio, não subir o número
aqui.

**O primeiro acesso de cada pessoa.** O seed cria só o dono (`adriano`). Quem
vende toca em "Criar minha conta" na tela de vendas, escolhe um nome e um PIN
de seis dígitos — e **espera**. A conta nasce inativa e não vale nada até o
dono abrir a tela dele, aba *Funcionários*, e tocar em **Liberar**. Se ele
estiver com a tela aberta, o pedido aparece na hora, pelo WebSocket.

Foi assim que essa porta se fechou: antes, criar a conta *era* entrar. Dentro
da loja isso bastava, porque alcançar a tela já exigia estar atrás do balcão.
Num endereço público, a mesma porta atende qualquer um que descubra o link — e
uma conta de funcionário enxerga o cardápio, lança pedido e imprime comanda.

## Publicar no Render

O repositório já vem pronto: `render.yaml` descreve o serviço e o banco,
`requirements.txt` traz as dependências e `.python-version` fixa o interpretador.

No painel do Render: **New → Blueprint**, aponte pro repositório e confirme. Ele
cria o Postgres, cria o serviço web e liga os dois — não há variável de ambiente
pra digitar. Alguns minutos depois o endereço `https://<nome>.onrender.com/` abre
direto na tela de vendas.

**Um serviço só.** O FastAPI serve a API, o WebSocket e os dois PWAs na mesma
origem, e é isso que faz o `api.js` funcionar com caminhos relativos. Não há
frontend separado pra publicar. O `Caddyfile` e o `docker-compose.yml` continuam
no repositório para quem preferir a VPS.

O que acontece a cada deploy (o `startCommand` do `render.yaml`):

1. `alembic upgrade head` — cria ou atualiza o schema.
2. `python -m app.seed` — planta o cardápio e a conta `adriano`. Funcionário
   não nasce pelo seed: cada um cria a própria conta pela tela de vendas
   ("Criar minha conta"). É idempotente: não duplica nada e não desfaz preço
   que o dono já editou.
3. `uvicorn … --workers 1`.

**Por que um worker só.** O gerenciador de WebSocket guarda as conexões em
memória do processo. Com dois workers, metade dos avisos de pedido novo cairia
no worker errado e nunca chegaria na tela.

**Trocar a senha do dono sem mexer em código.** Crie `SHALON_SENHA_DONO` em
*Environment*. Ela vale na primeira semeadura da conta; depois disso a senha
vive no banco.

**O plano gratuito tem dois preços escondidos.** O serviço hiberna depois de 15
minutos parado e leva perto de um minuto pra acordar — quem chegar primeiro num
sábado de manhã espera. E o Postgres gratuito expira; anote a data ou passe pro
plano pago antes que ela chegue, porque o banco vai junto. Para uma loja que
abre todo dia, o plano pago do serviço web é o que faz sentido.

**Se precisar apontar pra outro banco**, cole a string de conexão como ela vier,
em `SHALON_DATABASE_URL`. O `config.py` traduz `postgres://` e `postgresql://`
para `postgresql+asyncpg://` e converte `?sslmode=require` no `ssl=require` que
o asyncpg entende.

## Links

Com o servidor no ar:

| O quê | Link | Entrar como |
| --- | --- | --- |
| PWA de vendas | http://127.0.0.1:8000/vendas/ | usuário `vanusa` |
| PWA do dono | http://127.0.0.1:8000/dono/ | usuário `adriano` |
| Docs da API (Swagger) | http://127.0.0.1:8000/docs | — |
| Health check | http://127.0.0.1:8000/health | — |

Cada PWA guarda a sessão separada — a chave leva o nome da pasta
(`shalon.sessao.vendas`, `.dono`) —, então dá pra ficar logado nos dois no mesmo
navegador.

As duas telas são **de celular**. Abertas num monitor, elas travam numa coluna
da largura de um aparelho em vez de esticar: é o mesmo layout, no mesmo lugar,
sem uma segunda versão pra manter.

O `/health` diz se o banco respondeu e qual é o dia operacional corrente:

```json
{"ok": true, "ambiente": "dev", "banco": "ok", "dia_operacional": "2026-08-12"}
```

### Abrir no celular

O `--host 127.0.0.1` só aceita conexão da própria máquina. Para testar no
celular na mesma rede Wi-Fi, suba com `--host 0.0.0.0` e acesse
`http://<ip-da-maquina>:8000/vendas/` (o IP sai de `ipconfig` no Windows,
`ip addr` no Linux).

## Usuários de desenvolvimento

Criados pelo `python -m app.seed`:

| Usuário | Segredo | Papel |
| --- | --- | --- |
| `adriano` | `adriano212121` | DONO |
| Agente de impressão | `0000` | AGENTE |

Funcionário não nasce pelo seed: abra `/vendas/`, toque em **Criar minha
conta** e cadastre nome + senha de 6 números. A conta fica **esperando
liberação** — entre como `adriano` no `/dono/`, aba *Funcionários*, e toque em
**Liberar**. Só depois disso ela entra.

Em desenvolvimento isso são dois cliques a mais, e é de propósito: o caminho de
um funcionário novo é o mesmo em toda parte, então o que você testa na sua
máquina é o que a loja vai viver.

O login é por **nome de usuário e senha** — não há lista de usuários pra
escolher, e nem rota que a devolva: a tela mostra dois campos, e quem não sabe o
nome não tem o que tentar. O nome ignora maiúscula e espaço nas pontas.

**A senha é pedida uma vez por aparelho.** O refresh dura um ano e desliza a
cada renovação, então o celular do balcão não desloga no meio de um sábado.
Quem tira o acesso de alguém é o dono, em ☰ → **Quem está logado**: a lista
mostra cada aparelho conectado e o botão que o derruba. O aparelho removido
volta pra tela de login em até 30 minutos — é o tempo que o token de acesso que
ele já tem na mão leva pra vencer.

A senha do dono vem de `SHALON_SENHA_DONO`, e o valor acima é só o padrão do
`config.py`. **Troque-a antes de expor o sistema fora da loja**, junto com o
`SHALON_JWT_SEGREDO`:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

## Testes

Rodam em SQLite na memória, sem Docker e sem Postgres:

```bash
cd backend
.venv/Scripts/python.exe -m pytest          # 142 testes

cd ../agente
../backend/.venv/Scripts/python.exe -m pytest   # 20 testes
```

Os do agente não precisam de servidor, de rede nem de impressora: a `Api` e a
`Impressora` são fingidas, que é justamente pra isso que ele fala com um
protocolo em vez de um modelo de impressora.

Duas coisas **não** são cobertas e precisam de máquina de verdade: o
`SELECT ... FOR UPDATE` da numeração de pedidos (é específico do Postgres, use o
caminho B) e os drivers `EscPosUSB` / `EscPosRede` / `SpoolerWindows`.

## Configuração

Tudo por variável de ambiente com prefixo `SHALON_`; os padrões estão em
`backend/app/config.py`. As mais usadas:

| Variável | Padrão | Para quê |
| --- | --- | --- |
| `SHALON_DATABASE_URL` | Postgres em localhost | banco |
| `SHALON_JWT_SEGREDO` | valor de dev | assinatura dos tokens |
| `SHALON_AMBIENTE` | `dev` | em `prod` o `/docs` some |
| `SHALON_FUSO` | `America/Sao_Paulo` | fuso do dia operacional |
| `SHALON_HORA_VIRADA_DIA` | `4` | hora em que o dia operacional vira |
| `SHALON_SENHA_DONO` | `adriano212121` | usada só pelo seed inicial |

O fuso é explícito de propósito: a VPS roda em UTC e a virada das 4h sairia
errada se dependesse do relógio do sistema.
