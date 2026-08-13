"""Login por PIN/senha, renovação de token e logout."""

from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, status
from sqlalchemy import select

from app.config import get_config
from app.dependencias import IdentidadeDep, SessaoDep
from app.models.base import agora
from app.models.usuario import Papel, SessaoAuth, Usuario
from app.schemas.auth import LoginEntrada, RenovarEntrada, TokensSaida, UsuarioPublico
from app.seguranca import (
    conferir_hash,
    criar_token_acesso,
    gerar_refresh,
    hash_refresh,
    trava_login,
)

cfg = get_config()
rotas = APIRouter(prefix="/auth", tags=["auth"])


@rotas.get("/usuarios", response_model=list[UsuarioPublico])
async def listar_usuarios(
    sessao: SessaoDep,
    papel: Annotated[list[Papel] | None, Query()] = None,
):
    """Lista pra tela de login escolher quem vai entrar.

    Só nome e papel — nenhum dado sensível. PIN continua sendo o segredo.

    Sem filtro devolve quem loga no balcão (dono e funcionário), que é o padrão
    do PWA de vendas. A tela da cozinha pede `?papel=COZINHA&papel=DONO`: ela
    também precisa de uma lista pra login, e sem isto não teria como descobrir
    o id do próprio usuário.

    O agente de impressão nunca aparece, com ou sem filtro: é conta de máquina,
    não loga por tela nenhuma, e o PIN dela é fraco de propósito.
    """
    pedidos = papel or [Papel.DONO, Papel.FUNCIONARIO]
    visiveis = [p for p in pedidos if p is not Papel.AGENTE]
    if not visiveis:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "O agente de impressão não entra por tela de login"
        )

    consulta = (
        select(Usuario)
        .where(Usuario.ativo.is_(True), Usuario.papel.in_(visiveis))
        .order_by(Usuario.nome)
    )
    return list((await sessao.execute(consulta)).scalars())


@rotas.post("/login", response_model=TokensSaida)
async def login(dados: LoginEntrada, request: Request, sessao: SessaoDep):
    chave = f"{request.client.host if request.client else '?'}:{dados.usuario_id}"

    bloqueio = trava_login.segundos_restantes(chave)
    if bloqueio:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"Muitas tentativas. Tente de novo em {bloqueio}s.",
        )

    usuario = await sessao.get(Usuario, dados.usuario_id)
    # Mesma resposta pra usuário inexistente e PIN errado: não entregamos
    # de graça a informação de quais ids existem.
    if usuario is None or not usuario.ativo or not conferir_hash(dados.segredo, usuario.pin_hash):
        trava_login.registrar_falha(chave)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Usuário ou PIN inválido")

    trava_login.limpar(chave)
    return await _emitir_tokens(sessao, usuario, dados.dispositivo)


@rotas.post("/renovar", response_model=TokensSaida)
async def renovar(dados: RenovarEntrada, sessao: SessaoDep):
    """Troca o refresh por um par novo.

    O refresh antigo é revogado na troca (rotação). Se um refresh já usado
    aparecer de novo, é sinal de token roubado — derrubamos todas as sessões
    daquele usuário.
    """
    consulta = select(SessaoAuth).where(SessaoAuth.refresh_hash == hash_refresh(dados.refresh))
    sessao_auth = (await sessao.execute(consulta)).scalar_one_or_none()

    if sessao_auth is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Refresh inválido")

    if sessao_auth.usado_em is not None:
        await _revogar_todas(sessao, sessao_auth.usuario_id)
        # Commit explícito: a dependência de sessão faz rollback em qualquer
        # exceção — inclusive HTTPException. Sem isto a revogação seria desfeita
        # e o token roubado continuaria valendo.
        await sessao.commit()
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "Refresh reutilizado — sessões encerradas"
        )

    if not sessao_auth.valida:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sessão expirada")

    usuario = await sessao.get(Usuario, sessao_auth.usuario_id)
    if usuario is None or not usuario.ativo:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Usuário inativo")

    sessao_auth.usado_em = agora()
    sessao_auth.revogado_em = agora()
    return await _emitir_tokens(sessao, usuario, sessao_auth.dispositivo)


@rotas.post("/sair", status_code=status.HTTP_204_NO_CONTENT)
async def sair(dados: RenovarEntrada, sessao: SessaoDep):
    consulta = select(SessaoAuth).where(SessaoAuth.refresh_hash == hash_refresh(dados.refresh))
    sessao_auth = (await sessao.execute(consulta)).scalar_one_or_none()
    if sessao_auth and sessao_auth.revogado_em is None:
        sessao_auth.revogado_em = agora()


@rotas.get("/eu", response_model=UsuarioPublico)
async def eu(ident: IdentidadeDep, sessao: SessaoDep):
    usuario = await sessao.get(Usuario, ident.usuario_id)
    if usuario is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Usuário não existe mais")
    return usuario


# ------------------------------------------------------------------ internos

async def _emitir_tokens(sessao, usuario: Usuario, dispositivo: str | None) -> TokensSaida:
    acesso, validade = criar_token_acesso(usuario.id, usuario.nome, usuario.papel)
    bruto, guardado = gerar_refresh()

    sessao.add(
        SessaoAuth(
            usuario_id=usuario.id,
            refresh_hash=guardado,
            dispositivo=dispositivo,
            expira_em=agora() + timedelta(days=cfg.refresh_expira_dias),
        )
    )

    return TokensSaida(
        acesso=acesso,
        refresh=bruto,
        expira_em=validade,
        usuario_id=usuario.id,
        nome=usuario.nome,
        papel=usuario.papel,
    )


async def _revogar_todas(sessao, usuario_id: int) -> None:
    consulta = select(SessaoAuth).where(
        SessaoAuth.usuario_id == usuario_id, SessaoAuth.revogado_em.is_(None)
    )
    for s in (await sessao.execute(consulta)).scalars():
        s.revogado_em = agora()
