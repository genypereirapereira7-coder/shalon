"""O sabor do dia: o dono escreve, o balcão lê."""

from fastapi import APIRouter

from app.dependencias import IdentidadeDep, SessaoDep, SoDono
from app.schemas.sabor import SaborDoDiaEntrada, SaborDoDiaSaida
from app.servicos import sabores
from app.servicos.eventos import Evento, publicar_apos_commit

rotas = APIRouter(prefix="/sabores", tags=["sabores"])


@rotas.get("", response_model=SaborDoDiaSaida)
async def atuais(sessao: SessaoDep, _: IdentidadeDep):
    """Qualquer conta autenticada lê — a tela de vendas depende disto.

    Não é rota pública: o nome do sabor não é segredo nenhum, mas abrir uma
    rota sem token só porque o dado é inócuo vira o precedente que a próxima
    rota usa.
    """
    return SaborDoDiaSaida.model_validate(await sabores.ler(sessao), from_attributes=True)


@rotas.put("", response_model=SaborDoDiaSaida)
async def trocar(dados: SaborDoDiaEntrada, sessao: SessaoDep, dono: SoDono):
    """Troca os sabores. Só o dono.

    O evento vai pro balcão na hora: sem ele, o celular que já está com a tela
    aberta continuaria oferecendo o sabor de ontem até alguém recarregar — e
    venderia chocolate num dia em que a máquina tem creme.
    """
    atual = await sabores.gravar(sessao, dados.sabor1, dados.sabor2, dono.usuario_id)
    saida = SaborDoDiaSaida.model_validate(atual, from_attributes=True)

    await publicar_apos_commit(
        sessao,
        Evento.SABOR_ALTERADO,
        {"sabor1": atual.sabor1, "sabor2": atual.sabor2},
    )
    return saida
