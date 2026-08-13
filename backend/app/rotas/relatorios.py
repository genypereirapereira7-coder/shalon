"""Relatórios e fechamento de caixa — tudo restrito ao dono.

É o faturamento do negócio: quem vende não precisa (nem deve) ver o total do
dia inteiro nem fechar o caixa.
"""

from datetime import date

from fastapi import APIRouter, HTTPException, Query, status

from app.dependencias import SessaoDep, SoDono
from app.schemas.relatorio import FechamentoSaida, FecharEntrada, ResumoDia
from app.servicos import relatorios as servico
from app.servicos.dia_operacional import dia_atual

rotas = APIRouter(tags=["relatorios"])


@rotas.get("/relatorios/hoje", response_model=ResumoDia)
async def hoje(sessao: SessaoDep, _: SoDono):
    """Movimento do dia operacional corrente.

    O PWA do dono recarrega isto de tempos em tempos. Enquanto o WebSocket da
    fase 4 não existe, é este endpoint que faz os números subirem sozinhos.
    """
    return await servico.resumo(sessao, dia_atual())


@rotas.get("/relatorios/dia/{data}", response_model=ResumoDia)
async def dia(data: date, sessao: SessaoDep, _: SoDono):
    """Mesmo resumo, para qualquer data — inclusive dia já fechado."""
    return await servico.resumo(sessao, data)


@rotas.get("/fechamento", response_model=list[FechamentoSaida])
async def listar_fechamentos(
    sessao: SessaoDep,
    _: SoDono,
    limite: int = Query(default=60, ge=1, le=365),
):
    return await servico.historico(sessao, limite)


@rotas.post("/fechamento", response_model=FechamentoSaida, status_code=status.HTTP_201_CREATED)
async def fechar_caixa(dados: FecharEntrada, sessao: SessaoDep, dono: SoDono):
    """Congela o total do dia. Só o dono, e só uma vez por dia."""
    try:
        return await servico.fechar(
            sessao,
            dados.data_operacional or dia_atual(),
            dono.usuario_id,
            dados.total_conferido_centavos,
        )
    except servico.FechamentoInvalido as erro:
        raise HTTPException(status.HTTP_409_CONFLICT, str(erro)) from erro
