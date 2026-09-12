"""Sobe tudo pra rodar na sua máquina, com um comando só.

    cd backend
    .venv/Scripts/python.exe dev.py      # Windows
    .venv/bin/python dev.py              # Linux/macOS

Existe porque o caminho anterior não perdoava: o padrão do `config.py` é o
Postgres de produção, então quem rodasse `uvicorn app.main:app` sem exportar
`SHALON_DATABASE_URL` antes ganhava um servidor que **sobe normalmente**, serve
as três telas, e responde 500 em toda chamada de API. A tela de vendas abre,
mostra "Não deu pra carregar" e não há nada na cara do erro que aponte pro
motivo — só uma parede de traceback do asyncpg no terminal.

Um sistema que precisa de duas variáveis de ambiente decoradas pra ligar não
está pronto pra ser aberto por outra pessoa. Este script assume os padrões de
desenvolvimento, cria o banco se não existir, semeia se estiver vazio e sobe o
servidor. Nada aqui vale em produção: lá quem manda são as variáveis do
`docker-compose.yml`.
"""

import asyncio
import os
import sys
from pathlib import Path

AQUI = Path(__file__).resolve().parent
BANCO = AQUI / "_dev.db"

# Linha a linha, e sem morrer em acento. O `semear()` também imprime, e num
# console que não aguente um "ã" um UnicodeEncodeError derrubaria o arranque
# inteiro por causa de uma mensagem de log.
sys.stdout.reconfigure(line_buffering=True, errors="replace")

# Antes de qualquer `import app.*`: o `get_config` é cacheado e o `app.db` cria
# a engine no import. Definido depois, isto não teria efeito nenhum.
PADROES = {
    "SHALON_DATABASE_URL": f"sqlite+aiosqlite:///{BANCO.as_posix()}",
    "SHALON_JWT_SEGREDO": "dev-local-inseguro-nao-use-em-producao-32bytes",
    "SHALON_AMBIENTE": "dev",
    # O `config.py` não tem mais senha padrão — a que estava lá era conhecida
    # por quem lesse o repositório. Aqui ela volta, porque aqui é a máquina de
    # quem está desenvolvendo: sem isto, cada `dev.py` sortearia uma senha nova
    # e o login local viraria uma caça ao log. Este valor não sai deste arquivo,
    # e produção lê a variável do painel.
    "SHALON_SENHA_DONO": "adriano212121",
}
for chave, valor in PADROES.items():
    # `setdefault`: quem já exportou a variável (pra apontar pro Postgres do
    # compose, por exemplo) continua mandando.
    os.environ.setdefault(chave, valor)


async def preparar() -> None:
    from app.db import engine
    from app.models import Base

    async with engine.begin() as conexao:
        # As migrations do Alembic não rodam em SQLite (a 0001 usa
        # postgresql.UUID), então o schema vem do metadata — o mesmo caminho
        # que os testes usam.
        await conexao.run_sync(Base.metadata.create_all)

    from app.seed import semear

    await semear(detalhado=False)
    await engine.dispose()


def main() -> None:
    porta = int(os.environ.get("PORTA") or 8000)
    novo = not BANCO.exists()

    # `flush` em tudo: sem ele o Python segura o stdout num buffer de bloco
    # quando a saída não é um terminal (redirecionada, num painel de IDE), e os
    # links só apareceriam quando alguém desse Ctrl+C. Justamente o contrário
    # do que este script existe pra fazer.
    print(f"banco:   {BANCO}{'  (criando agora)' if novo else ''}", flush=True)
    asyncio.run(preparar())

    print(
        "\ntelas pra abrir no navegador:"
        f"\n  vendas   http://127.0.0.1:{porta}/vendas/     toque em 'Criar minha conta'"
        f"\n  dono     http://127.0.0.1:{porta}/dono/       usuario: adriano"
        f"\n  API      http://127.0.0.1:{porta}/docs"
        "\n\nA comanda sai no proprio celular do balcao, pelo RawBT: abra o"
        "\n/vendas/ num Android com o RawBT instalado (veja o README).\n",
        flush=True,
    )

    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=os.environ.get("HOST", "127.0.0.1"),
        port=porta,
        # `reload_dirs` restrito ao `app/`: sem isto o reloader vigia o
        # `.venv/` inteiro e o arranque fica lento no Windows.
        reload=True,
        reload_dirs=[str(AQUI / "app")],
    )


if __name__ == "__main__":
    sys.exit(main())
