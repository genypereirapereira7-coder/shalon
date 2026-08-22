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

# O dono entra em tudo: é o celular dele que salva o expediente quando o
# balcão trava ou o agente cai.
SoCozinha = Annotated[Identidade, Depends(exige(Papel.COZINHA, Papel.DONO))]

# Leitura da fila de impressão: conta de máquina. É a varredura que o agente
# faz do dia inteiro, incluindo venda de outro atendente.
SoAgente = Annotated[Identidade, Depends(exige(Papel.AGENTE, Papel.DONO))]

# Quem confirma que a comanda saiu. O funcionário entrou aqui quando a
# impressão mudou de lugar: hoje quem manda o cupom pro papel é o celular do
# balcão, pelo RawBT (`frontend/vendas/impressao.js`), e sem esta permissão a
# venda ficaria pra sempre na fila de não-impressos — fazendo o agente do PC,
# se alguém o mantiver ligado, imprimir uma segunda via de tudo.
#
# É a permissão mais fraca do sistema de propósito: marca uma data num pedido
# que o próprio aparelho acabou de criar, não mexe em dinheiro nem em status.
SoImpressor = Annotated[
    Identidade, Depends(exige(Papel.AGENTE, Papel.FUNCIONARIO, Papel.DONO))
]

# Quem pode cancelar uma venda. O balcão entra porque é lá que o erro acontece
# e é lá que o cliente está — mandar chamar o dono pra desfazer um pedido
# digitado errado deixaria a fila parada.
#
# A permissão é ampla, o poder não: a rota restringe o funcionário aos pedidos
# que ele mesmo criou, no dia de hoje. Quem cancela venda de outro atendente ou
# de outro dia é o dono. E todo cancelamento grava motivo e autor, e aparece
# destacado no painel do dono — cancelar é dinheiro saindo do caixa, e o que
# protege isso é o registro, não a dificuldade.
SoCaixa = Annotated[Identidade, Depends(exige(Papel.FUNCIONARIO, Papel.DONO))]
