"""Aplicação FastAPI."""

from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app.config import get_config
from app.db import engine
from app.rotas import auth, cardapio, pedidos, relatorios, ws
from app.servicos.dia_operacional import dia_atual

cfg = get_config()

app = FastAPI(
    title="Shalon",
    description="Sistema de pedidos da sorveteria Shalon",
    version="0.1.0",
    docs_url="/docs" if not cfg.producao else None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=cfg.cors_origens,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.rotas)
app.include_router(cardapio.rotas)
app.include_router(pedidos.rotas)
app.include_router(relatorios.rotas)
app.include_router(ws.rotas)


@app.get("/health", tags=["infra"])
async def health():
    """Checagem de vida — inclui o banco, senão só diz que o Python subiu."""
    banco_ok = True
    try:
        async with engine.connect() as conexao:
            await conexao.execute(text("SELECT 1"))
    except Exception:
        banco_ok = False

    return {
        "ok": banco_ok,
        "ambiente": cfg.ambiente,
        "agora_utc": datetime.now(UTC).isoformat(),
        "fuso": cfg.fuso,
        "dia_operacional": dia_atual().isoformat(),
        "banco": "ok" if banco_ok else "indisponivel",
    }


# Em dev o próprio FastAPI serve os PWAs; em produção quem serve é o Caddy.
_frontend = (Path(__file__).resolve().parent.parent / cfg.dir_frontend).resolve()
for _nome in ("vendas", "dono", "cozinha"):
    _pasta = _frontend / _nome
    if _pasta.is_dir():
        app.mount(f"/{_nome}", StaticFiles(directory=_pasta, html=True), name=_nome)
if (_frontend / "comum").is_dir():
    app.mount("/comum", StaticFiles(directory=_frontend / "comum"), name="comum")
