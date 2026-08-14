# Shalon

Sistema de pedidos da sorveteria Shalon: um backend FastAPI e PWAs que rodam no
celular do caixa. A arquitetura, as decisões e o que ainda falta estão em
[ARCHITECTURE.md](ARCHITECTURE.md).

## O que já existe

| Parte | Estado |
| --- | --- |
| Backend (auth, cardápio, pedidos, relatórios, fechamento) | funcionando |
| PWA de vendas (`frontend/vendas/`) | funcionando |
| PWA do dono (`frontend/dono/`) | funcionando |
| PWA da cozinha (`frontend/cozinha/`) | funcionando |
| WebSocket (`/ws`) | funcionando |
| Agente de impressão (`agente/`) | funcionando, sem impressora de verdade |

O `backend/app/main.py` já monta as três pastas de PWA; as que não existem são
simplesmente ignoradas na subida.

A comanda aparece na cozinha e o total sobe no celular do dono em menos de um
segundo, pelo WebSocket. **O polling continua ligado por baixo em todas as
telas**, só mais espaçado — nenhuma delas depende do socket pra estar correta,
e se ele nunca conectar tudo funciona mais devagar em vez de quebrar.

O agente de impressão está escrito e testado, mas a impressora ainda não foi
comprada. Ele vem configurado com `tipo = fake`, que grava o cupom num
`cupons.txt` em vez de mandar pra bobina — dá pra ver o fluxo inteiro
funcionando sem hardware nenhum. Os drivers de térmica USB, de rede e do
spooler do Windows estão implementados e são uma linha no `config.ini`, mas
nunca foram testados contra máquina de verdade.

## Rodar localmente

Dois caminhos. O **A** é o mais rápido e não precisa de Docker; o **B** roda no
Postgres, igual à produção.

### A) Sem Docker — SQLite (mais rápido)

Precisa de Python 3.12+.

```bash
cd backend

# 1. ambiente virtual + dependências
python -m venv .venv
.venv/Scripts/python.exe -m pip install -e ".[dev]"     # Windows
# source .venv/bin/activate && pip install -e ".[dev]"  # Linux/macOS

# 2. criar o schema no SQLite
SHALON_DATABASE_URL="sqlite+aiosqlite:///_dev.db" \
.venv/Scripts/python.exe -c "
import asyncio
from app.db import engine
from app.models import Base
async def m():
    async with engine.begin() as c:
        await c.run_sync(Base.metadata.create_all)
asyncio.run(m())
"

# 3. popular o cardápio e os usuários (idempotente)
SHALON_DATABASE_URL="sqlite+aiosqlite:///_dev.db" \
.venv/Scripts/python.exe -m app.seed

# 4. subir
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

### Ver papel sair (agente de impressão)

Com o servidor no ar, noutro terminal:

```bash
cd agente
cp config.ini.exemplo config.ini     # já vem com tipo = fake

# Roda com o venv do backend — as dependências (httpx, websockets) são as mesmas.
../backend/.venv/Scripts/python.exe main.py     # Windows
# ../backend/.venv/bin/python main.py           # Linux/macOS
```

O `usuario_id` no `config.ini` é o do usuário AGENTE, e o `python -m app.seed`
imprime a tabela de ids no fim. Faça uma venda no PWA e a comanda aparece em
`agente/cupons.txt` — que é o "papel" da `ImpressoraFake`.

Vale desligar o agente, vender duas vezes e religar: ele pergunta ao servidor o
que ficou sem imprimir e recupera as duas. E pedir REIMPRIMIR na tela da cozinha
faz sair um segundo papel marcado `*** REIMPRESSAO ***`.

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

## Links

Com o servidor no ar:

| O quê | Link | Entrar como |
| --- | --- | --- |
| PWA de vendas | http://127.0.0.1:8000/vendas/ | João, PIN `1234` |
| PWA do dono | http://127.0.0.1:8000/dono/ | Dono, senha `shalon123` |
| PWA da cozinha | http://127.0.0.1:8000/cozinha/ | Cozinha, PIN `0000` |
| Docs da API (Swagger) | http://127.0.0.1:8000/docs | — |
| Health check | http://127.0.0.1:8000/health | — |

Cada PWA guarda a sessão separada — a chave leva o nome da pasta
(`shalon.sessao.vendas`, `.dono`, `.cozinha`) —, então dá pra ficar logado nos
três no mesmo navegador.

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
| Dono | `shalon123` | DONO |
| João | `1234` | FUNCIONARIO |
| Cozinha | `0000` | COZINHA |
| Agente de impressão | `0000` | AGENTE |

A senha do dono vem de `SHALON_SENHA_DONO` (padrão `shalon123`). **Troque antes
de subir para produção**, junto com o `SHALON_JWT_SEGREDO`:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

## Testes

Rodam em SQLite na memória, sem Docker e sem Postgres:

```bash
cd backend
.venv/Scripts/python.exe -m pytest          # 128 testes

cd ../agente
../backend/.venv/Scripts/python.exe -m pytest   # 15 testes
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
| `SHALON_SENHA_DONO` | `shalon123` | usada só pelo seed inicial |

O fuso é explícito de propósito: a VPS roda em UTC e a virada das 4h sairia
errada se dependesse do relógio do sistema.
