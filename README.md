# Shalon

Sistema de pedidos da sorveteria Shalon: um backend FastAPI e PWAs que rodam no
celular do caixa. A arquitetura, as decisões e o que ainda falta estão em
[ARCHITECTURE.md](ARCHITECTURE.md).

## O que já existe

| Parte | Estado |
| --- | --- |
| Backend (auth, cardápio, pedidos, dia operacional) | funcionando |
| PWA de vendas (`frontend/vendas/`) | funcionando |
| PWA do dono | não implementado |
| PWA da cozinha | não implementado |

O `backend/app/main.py` já monta as três pastas de PWA; as que não existem são
simplesmente ignoradas na subida.

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

| O quê | Link |
| --- | --- |
| PWA de vendas | http://127.0.0.1:8000/vendas/ |
| Docs da API (Swagger) | http://127.0.0.1:8000/docs |
| Health check | http://127.0.0.1:8000/health |

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
.venv/Scripts/python.exe -m pytest
```

O que é específico do Postgres — o `SELECT ... FOR UPDATE` da numeração de
pedidos — não é coberto por esses testes e precisa do caminho B.

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
