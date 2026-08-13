"""Dependências de autenticação/autorização das rotas."""

from collections.abc import Callable, Coroutine
from typing import Annotated, Any

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_sessao
from app.models.usuario import Papel, Usuario
from app.seguranca import Identidade, ler_token_acesso

esquema = HTTPBearer(auto_error=False)

SessaoDep = Annotated[AsyncSession, Depends(get_sessao)]


async def identidade_atual(
    credencial: Annotated[HTTPAuthorizationCredentials | None, Depends(esquema)],
) -> Identidade:
    if credencial is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sem token")
    ident = ler_token_acesso(credencial.credentials)
    if ident is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token inválido ou expirado")
    return ident


IdentidadeDep = Annotated[Identidade, Depends(identidade_atual)]


async def usuario_atual(ident: IdentidadeDep, sessao: SessaoDep) -> Usuario:
    usuario = await sessao.get(Usuario, ident.usuario_id)
    if usuario is None or not usuario.ativo:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Usuário inativo")
    return usuario


UsuarioDep = Annotated[Usuario, Depends(usuario_atual)]


def exige(*papeis: Papel) -> Callable[..., Coroutine[Any, Any, Identidade]]:
    """Uso: `_: Annotated[Identidade, Depends(exige(Papel.DONO))]`."""

    async def verificar(ident: IdentidadeDep) -> Identidade:
        if ident.papel not in papeis:
            permitidos = ", ".join(p.value for p in papeis)
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, f"Requer papel: {permitidos}"
            )
        return ident

    return verificar


SoDono = Annotated[Identidade, Depends(exige(Papel.DONO))]

# O dono entra em tudo: é o celular dele que salva o expediente quando a tela
# da cozinha trava ou o agente cai.
SoCozinha = Annotated[Identidade, Depends(exige(Papel.COZINHA, Papel.DONO))]
SoAgente = Annotated[Identidade, Depends(exige(Papel.AGENTE, Papel.DONO))]
