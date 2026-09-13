"""O relógio que fecha o caixa sozinho na hora marcada.

Mora no servidor, e não na tela do dono, porque a tela do dono não está aberta
à 1h da manhã. Um agendamento que depende de alguém ter deixado o celular
desbloqueado e o app na frente não é agendamento — é sorte.

Um processo só cuida disto: o `railway.toml` sobe com `--workers 1` e
`numReplicas = 1` (o gerenciador de WebSocket já exigia isso). Se um dia virar
mais de um, dois laços tentariam fechar o mesmo dia — e aí o que segura é o
banco, porque `data_operacional` é único e o segundo esbarra no
`FechamentoInvalido`. Nada se duplica; só sobra um log confuso.
"""

import asyncio
import logging
from datetime import UTC, datetime

from app.config import get_config
from app.db import Sessao
from app.servicos import relatorios
from app.servicos.dia_operacional import proximo_fechamento

log = logging.getLogger("shalon")
cfg = get_config()

# Teto de cada cochilo. A espera até a hora marcada pode passar de 20 horas, e
# dormir tudo de uma vez é confiar demais num relógio que pode dar salto — o
# container hiberna, o horário do sistema é corrigido, o fuso muda de lei. A
# cada quinze minutos o laço reabre os olhos e refaz a conta a partir da hora
# de agora. Quando falta pouco, ele dorme exatamente o que falta, então o
# fechamento sai no minuto certo e não quinze minutos depois.
PASSO_MAX_SEG = 15 * 60


async def _tentar() -> None:
    """Uma passada. Barata e sem efeito quando não há o que fechar."""
    try:
        async with Sessao() as sessao:
            fechado = await relatorios.fechar_automatico(sessao)
            if fechado is None:
                return
            await sessao.commit()
            log.info(
                "Caixa de %s fechado automaticamente: %d pedidos, R$ %d,%02d",
                fechado.data_operacional,
                fechado.qtd_pedidos,
                fechado.total_centavos // 100,
                fechado.total_centavos % 100,
            )
    except Exception:
        # Um tombo aqui não pode matar o laço: amanhã ele tenta de novo, e o
        # dia que ficou aberto continua fechável à mão. Morrer calado deixaria
        # a loja sem fechamento automático até o próximo deploy, sem ninguém
        # perceber.
        log.exception("Fechamento automático falhou; tento de novo no próximo passo")


async def laco() -> None:
    """Fecha o que ficou pra trás e depois dorme até a próxima hora marcada."""
    # Antes de qualquer espera: o servidor pode ter ficado fora do ar
    # justamente à 1h — deploy, reinício, queda. O `dia_a_fechar` devolve o dia
    # cuja hora já passou, então esta primeira passada é o que conserta isso.
    await _tentar()

    while True:
        falta = (proximo_fechamento() - datetime.now(UTC)).total_seconds()
        await asyncio.sleep(min(max(falta, 1.0), PASSO_MAX_SEG))
        await _tentar()


def agendar() -> asyncio.Task | None:
    """Liga o laço, se estiver ligado na configuração. Devolve a tarefa."""
    if not cfg.fechamento_automatico:
        log.info("Fechamento automático desligado (SHALON_FECHAMENTO_AUTOMATICO)")
        return None

    log.info(
        "Fechamento automático às %02dh (fuso %s); próximo em %s",
        cfg.fechamento_automatico_hora,
        cfg.fuso,
        proximo_fechamento().strftime("%d/%m %H:%M"),
    )
    return asyncio.create_task(laco(), name="fechamento-automatico")
