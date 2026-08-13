"""Engine, sessão e dependência de banco."""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_config

_cfg = get_config()

engine = create_async_engine(
    _cfg.database_url,
    echo=False,
    pool_pre_ping=True,  # a VPS derruba conexão ociosa; sem isso a primeira query da manhã falha
)

Sessao = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_sessao() -> AsyncGenerator[AsyncSession, None]:
    async with Sessao() as sessao:
        try:
            yield sessao
            await sessao.commit()
        except Exception:
            await sessao.rollback()
            raise
