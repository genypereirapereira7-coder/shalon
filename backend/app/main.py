"""Aplicação FastAPI."""

import asyncio
import logging
import sys
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app.config import get_config
from app.db import engine
from app.rotas import auth, cardapio, pedidos, relatorios, sabores, usuarios, ws
from app.servicos import fechamento_automatico
from app.servicos.dia_operacional import dia_atual

log = logging.getLogger("shalon")

# Sem isto o `log.info` deste app não sai em lugar nenhum.
#
# O Uvicorn configura os loggers dele e mais nenhum; um logger próprio sem
# handler cai no `lastResort` do Python, que só emite WARNING pra cima. O
# efeito era mudo e enganoso: os `log.error` apareciam (o do JWT, por exemplo),
# então tudo parecia configurado — enquanto "caixa fechado automaticamente" e
# "quem entrou no WebSocket" sumiam sem deixar rastro. Num serviço em que o log
# do painel é a única janela pra dentro, isso é a diferença entre saber e supor.
#
# Handler próprio em vez de `basicConfig`: mexer no logger raiz mudaria o
# comportamento de toda biblioteca de terceiro junto.
if not log.handlers:
    _saida = logging.StreamHandler(sys.stdout)
    _saida.setFormatter(logging.Formatter("%(levelname)s:     %(message)s"))
    log.addHandler(_saida)
    log.setLevel(logging.INFO)

cfg = get_config()


@asynccontextmanager
async def ciclo_de_vida(_: FastAPI):
    """Encosta no banco no arranque e grita se ele não responder.

    Sem isto o servidor sobe feliz, serve as duas telas e só falha quando
    alguém aperta um botão — com uma parede de traceback do driver no terminal
    e um "Erro 500" na cara de quem está no balcão. O caso comum em
    desenvolvimento é ter esquecido as variáveis de ambiente e estar apontando
    pro Postgres de produção, que não existe na máquina de ninguém.

    Avisa, mas não derruba: em produção o banco é um container que pode subir
    depois da API, e morrer no arranque transformaria uma espera de dois
    segundos numa noite sem sistema.
    """
    try:
        async with engine.connect() as conexao:
            await conexao.execute(text("SELECT 1"))
    except Exception as erro:
        alvo = cfg.database_url.split("@")[-1]  # sem a senha
        # Moldura em ASCII puro de propósito: o console do Windows costuma
        # estar em cp1252 e transformaria caracteres de caixa em lixo. Este é
        # justamente o aviso que precisa sobreviver ao pior terminal.
        log.error(
            "\n"
            "  ============================================================\n"
            "   O BANCO NAO RESPONDEU - as telas abrem, mas nada funciona\n"
            "  ============================================================\n"
            "   tentei: %s\n"
            "   erro:   %s\n"
            "\n"
            "   Rodando na sua maquina? Use o atalho que ja cuida disso:\n"
            "       cd backend && .venv/Scripts/python.exe dev.py\n"
            "   Ele aponta pro SQLite, cria o banco e semeia o cardapio.\n"
            "  ============================================================\n",
            alvo,
            erro,
        )

    # O relógio do fechamento sobe depois do banco de propósito: a primeira
    # coisa que ele faz é procurar um dia em aberto, e sem banco isso só geraria
    # um traceback no arranque. Ele já se defende sozinho (ver `_tentar`), mas
    # não há por que provocar.
    tarefa = fechamento_automatico.agendar()
    try:
        yield
    finally:
        # Desligar limpo: sem isto o `asyncio.sleep` de quinze minutos segura o
        # encerramento, e o Ctrl+C no terminal parece travado.
        if tarefa is not None:
            tarefa.cancel()
            with suppress(asyncio.CancelledError):
                await tarefa


app = FastAPI(
    title="Shalon",
    description="Sistema de pedidos da sorveteria Shalon",
    version="0.1.0",
    docs_url="/docs" if not cfg.producao else None,
    lifespan=ciclo_de_vida,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=cfg.cors_origens,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def cabecalhos_de_seguranca(requisicao, proxima):
    """Os cabeçalhos que o Caddy punha e a hospedagem gerenciada não põe.

    Eles moravam no `Caddyfile`, e funcionaram enquanto o sistema rodava atrás
    dele numa VPS. No Railway e no Render não há Caddy nenhum — o Uvicorn
    responde direto — e as proteções sumiram junto com o proxy, sem nada
    quebrar e sem ninguém notar. Aqui elas passam a viajar com a aplicação, o
    que também as leva pro `dev.py` e pros testes.

    Cada um resolve uma coisa concreta:

    - `X-Content-Type-Options` impede o navegador de adivinhar o tipo de um
      arquivo e executar como script algo que servimos como texto.
    - `X-Frame-Options` e `frame-ancestors` barram clickjacking: a tela de
      vendas dentro de um iframe de outro site, com um botão invisível por
      cima do "FINALIZAR".
    - `Referrer-Policy` evita vazar o caminho da página pra fora.
    - A CSP é a que faz trabalho de verdade: mesmo que um nome de produto
      digitado pelo dono escape do `escapar()` do frontend, o navegador se
      recusa a executar script que não venha da própria origem.
    """
    resposta = await proxima(requisicao)

    resposta.headers.setdefault("X-Content-Type-Options", "nosniff")
    resposta.headers.setdefault("X-Frame-Options", "DENY")
    resposta.headers.setdefault("Referrer-Policy", "same-origin")
    # O sistema não usa câmera, microfone nem localização em tela nenhuma.
    resposta.headers.setdefault(
        "Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()"
    )

    if cfg.csp:
        resposta.headers.setdefault("Content-Security-Policy", cfg.csp)

    # Só sobre HTTPS: mandar HSTS numa resposta HTTP é ignorado pelo navegador,
    # e em desenvolvimento (http://127.0.0.1) seria um tiro no pé — o navegador
    # passaria a exigir HTTPS do localhost e a tela pararia de abrir.
    #
    # Este `scheme` só diz "https" porque o Uvicorn sobe com `--proxy-headers`
    # e confia no `X-Forwarded-Proto` do proxy (veja o railway.toml).
    if cfg.producao and requisicao.url.scheme == "https":
        resposta.headers.setdefault(
            "Strict-Transport-Security", f"max-age={cfg.hsts_max_age}"
        )

    return resposta


@app.middleware("http")
async def nao_cachear_service_worker(requisicao, proxima):
    """Service worker nunca em cache — a mesma regra que o Caddyfile aplica.

    Em produção quem serve os PWAs é o Caddy, que já tem esta regra. Em
    desenvolvimento é o `StaticFiles` daqui, que não tinha — e a diferença
    custa caro: o navegador guarda o `sw.js` antigo, o service worker velho
    continua no comando e serve o `app.js` do cache dele. O resultado é uma
    tela que teima em rodar a versão anterior depois de qualquer alteração,
    sem erro nenhum aparecendo, e um "não está funcionando" que não se explica
    olhando o código.
    """
    resposta = await proxima(requisicao)
    if requisicao.url.path.endswith("/sw.js"):
        resposta.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return resposta

app.include_router(auth.rotas)
app.include_router(cardapio.rotas)
app.include_router(pedidos.rotas)
app.include_router(relatorios.rotas)
app.include_router(sabores.rotas)
app.include_router(usuarios.rotas)
app.include_router(ws.rotas)


@app.get("/", include_in_schema=False)
async def raiz():
    """A porta da frente é o balcão.

    Em produção quem fazia este desvio era o Caddy (`redir / /vendas/`). Numa
    hospedagem gerenciada não há Caddy nenhum, e sem esta rota o endereço que
    a pessoa recebe — o domínio pelado — abre num 404 do FastAPI. Quem vende é
    quem digita o endereço no celular; a tela do dono se alcança por
    `/dono/`.
    """
    return RedirectResponse("/vendas/")


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


# Quem serve os PWAs é este `StaticFiles`, em dev e na hospedagem gerenciada
# (Render) — lá tudo mora numa origem só, e por isso o `api.js` chama caminhos
# relativos e o `ws.js` monta a URL a partir do próprio host. O Caddyfile do
# repositório serve só ao caminho alternativo, com a VPS e o compose.
# São dois PWAs: vendas e dono. A tela da cozinha saiu junto com o PC da cozinha
# — quem imprime a comanda agora é o próprio celular do balcão, pelo RawBT.
_frontend = (Path(__file__).resolve().parent.parent / cfg.dir_frontend).resolve()
for _nome in ("vendas", "dono"):
    _pasta = _frontend / _nome
    if _pasta.is_dir():
        app.mount(f"/{_nome}", StaticFiles(directory=_pasta, html=True), name=_nome)
if (_frontend / "comum").is_dir():
    app.mount("/comum", StaticFiles(directory=_frontend / "comum"), name="comum")
