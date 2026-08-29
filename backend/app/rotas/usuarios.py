"""Administração de contas de funcionário, pelo dono."""

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from app.dependencias import SessaoDep, SoDono
from app.models.usuario import Papel, SessaoAuth, Usuario
from app.schemas.usuario import AtivoEntrada, FuncionarioResumo
from app.servicos.eventos import Evento, publicar_apos_commit

rotas = APIRouter(prefix="/usuarios", tags=["usuarios"])


@rotas.get("", response_model=list[FuncionarioResumo])
async def listar_funcionarios(sessao: SessaoDep, _: SoDono):
    """Só funcionário aparece aqui — dono, cozinha e agente não se gerenciam por esta tela."""
    consulta = (
        select(Usuario).where(Usuario.papel == Papel.FUNCIONARIO).order_by(Usuario.nome)
    )
    return (await sessao.execute(consulta)).scalars().all()


@rotas.patch("/{usuario_id}", response_model=FuncionarioResumo)
async def pausar_ou_reativar(
    usuario_id: int, dados: AtivoEntrada, sessao: SessaoDep, _: SoDono
):
    """Pausar barra login e qualquer chamada nova na hora — sem apagar histórico nenhum.

    Quem já está com o app aberto não sente a pausa até a próxima chamada ao
    servidor — o token de acesso é assinado e não consultado a cada requisição.
    O evento por WebSocket fecha essa janela: o aparelho pausado cai na hora,
    mesmo no meio de uma venda.
    """
    usuario = await sessao.get(Usuario, usuario_id)
    if usuario is None or usuario.papel != Papel.FUNCIONARIO:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Funcionário não existe")

    usuario.ativo = dados.ativo
    await sessao.flush()
    saida = FuncionarioResumo.model_validate(usuario)

    if not dados.ativo:
        await publicar_apos_commit(
            sessao, Evento.USUARIO_DESATIVADO, {"usuario_id": usuario_id}
        )
    return saida


@rotas.delete("/{usuario_id}", status_code=status.HTTP_204_NO_CONTENT)
async def excluir(usuario_id: int, sessao: SessaoDep, _: SoDono):
    """Some com a conta — mas não se ela já vendeu algo.

    Pedido e fechamento apontam pro usuário que os criou, e a comanda impressa
    tem esse nome escrito. Apagar a conta apagaria a autoria de vendas já
    feitas, e o banco recusa — é o que a foreign key está aqui pra fazer. Quem
    saiu da loja sem nunca ter vendido nada some de verdade; quem já vendeu, o
    dono pausa.

    As sessões de login da conta somem junto — não há nada pra revogar numa
    conta que deixou de existir. Quem estiver com o app aberto na hora recebe
    o mesmo aviso de desativação por WebSocket que a pausa manda — apagar não
    é motivo pra deixar o aparelho vendendo mais dez minutos por engano.
    """
    usuario = await sessao.get(Usuario, usuario_id)
    if usuario is None or usuario.papel != Papel.FUNCIONARIO:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Funcionário não existe")

    try:
        async with sessao.begin_nested():
            await sessao.execute(delete(SessaoAuth).where(SessaoAuth.usuario_id == usuario_id))
            await sessao.delete(usuario)
            await sessao.flush()
    except IntegrityError as erro:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Este funcionário já tem vendas registradas — pause em vez de excluir",
        ) from erro

    await publicar_apos_commit(sessao, Evento.USUARIO_DESATIVADO, {"usuario_id": usuario_id})
