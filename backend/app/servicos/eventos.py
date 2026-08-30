"""Hub do WebSocket: quem está conectado e quem recebe cada evento.

O roteamento é **por papel**, não por usuário (§5 da arquitetura): toda tela de
cozinha recebe `pedido.novo`, todo dono recebe `metricas.tick`. Quem está
conectado mora num dicionário em memória, o que é uma decisão consciente e não
um atalho: a v1 roda com um worker só (§9). Com dois workers, um pedido criado
no worker A não chegaria nas telas conectadas no worker B — e a saída seria
trocar este dicionário por um pub/sub no Redis, sem mexer nas rotas.

**Publicar nunca pode derrubar a rota que publicou.** Uma tela que fechou o
navegador sem encerrar o socket, ou um PC de cozinha que congelou, não pode
fazer o `POST /pedidos` falhar — a venda já aconteceu e o dinheiro já entrou na
gaveta. Por isso todo envio é isolado e o pior caso é a conexão morta sair do
hub e a tela se virar com o polling até reconectar.
"""

import asyncio
import enum
import logging
from collections import defaultdict
from typing import Any, Protocol

from app.models.usuario import Papel

log = logging.getLogger(__name__)

# Uma tela travada não segura o broadcast das outras — e muito menos a resposta
# HTTP de quem publicou. Passou disto, a conexão é tratada como morta.
ENVIO_TIMEOUT_S = 2.0


class Evento(str, enum.Enum):
    """Os eventos da tabela da §5. Enum e não string solta: um typo em
    `"pedido.novo"` viraria um evento que ninguém assina e sumiria calado.
    """

    PEDIDO_NOVO = "pedido.novo"
    PEDIDO_STATUS = "pedido.status"
    PEDIDO_IMPRESSO = "pedido.impresso"
    PRECO_ALTERADO = "preco.alterado"
    METRICAS_TICK = "metricas.tick"
    IMPRESSORA_STATUS = "impressora.status"
    USUARIO_DESATIVADO = "usuario.desativado"
    USUARIO_PENDENTE = "usuario.pendente"


# Quem recebe o quê. Mora aqui, junto do enum, pra que o destino seja uma
# decisão só — se cada rota escolhesse a plateia na hora de publicar, uma delas
# esqueceria a cozinha e ninguém perceberia até faltar comanda.
#
# O dono entra em quase tudo de propósito: é o celular dele que salva o
# expediente quando a tela da cozinha trava.
DESTINOS: dict[Evento, tuple[Papel, ...]] = {
    Evento.PEDIDO_NOVO: (Papel.COZINHA, Papel.AGENTE, Papel.DONO),
    Evento.PEDIDO_STATUS: (Papel.FUNCIONARIO, Papel.COZINHA, Papel.DONO),
    Evento.PEDIDO_IMPRESSO: (Papel.COZINHA, Papel.DONO),
    Evento.PRECO_ALTERADO: (Papel.FUNCIONARIO, Papel.COZINHA, Papel.DONO),
    Evento.METRICAS_TICK: (Papel.DONO,),
    # Papel sem papel na impressora: quem precisa saber que ela travou é a
    # cozinha (que vai buscar a comanda na mão) e o dono.
    Evento.IMPRESSORA_STATUS: (Papel.COZINHA, Papel.DONO),
    # Só o balcão precisa ouvir isto. O roteamento é por papel, não por usuário
    # (topo do arquivo) — todo FUNCIONARIO conectado recebe o aviso com o id de
    # quem foi pausado ou excluído, e cada tela decide sozinha se é ela mesma.
    Evento.USUARIO_DESATIVADO: (Papel.FUNCIONARIO,),
    # Alguém pediu uma conta. Só o dono libera, então só o dono ouve — e ouve
    # na hora: uma pessoa parada no balcão esperando pra começar a vender é a
    # diferença entre um toque agora e um telefonema.
    Evento.USUARIO_PENDENTE: (Papel.DONO,),
}


class Canal(Protocol):
    """O mínimo que o hub precisa de uma conexão.

    É um Protocol e não o `WebSocket` do Starlette pra deixar explícito que o
    hub só escreve — quem lê a conexão é a rota — e pra que o teste possa
    assinar um evento sem levantar transporte nenhum.
    """

    async def send_json(self, dados: Any) -> None: ...


class Hub:
    def __init__(self) -> None:
        self._por_papel: dict[Papel, set[Canal]] = defaultdict(set)

    def entrar(self, canal: Canal, papel: Papel) -> None:
        self._por_papel[papel].add(canal)

    def sair(self, canal: Canal, papel: Papel) -> None:
        self._por_papel[papel].discard(canal)

    def conectados(self, papel: Papel) -> int:
        return len(self._por_papel[papel])

    async def publicar(self, evento: Evento, dados: Any) -> None:
        """Manda o evento pra quem o `DESTINOS` diz que assina.

        Não levanta exceção: ver o cabeçalho do módulo.
        """
        alvos = {canal for papel in DESTINOS[evento] for canal in self._por_papel[papel]}
        if not alvos:
            return

        mensagem = {"evento": evento.value, "dados": dados}
        mortas = await asyncio.gather(*(self._enviar(c, mensagem) for c in alvos))

        for canal in filter(None, mortas):
            for conexoes in self._por_papel.values():
                conexoes.discard(canal)

    async def _enviar(self, canal: Canal, mensagem: dict) -> Canal | None:
        """Devolve a conexão quando ela morreu, pro `publicar` removê-la."""
        try:
            async with asyncio.timeout(ENVIO_TIMEOUT_S):
                await canal.send_json(mensagem)
        except Exception:
            log.debug("conexão morta descartada do hub", exc_info=True)
            return canal
        return None


hub = Hub()


async def publicar_apos_commit(sessao, evento: Evento, dados: Any) -> None:
    """Grava de verdade e só então anuncia.

    A ordem não é preciosismo. O `get_sessao` só commita depois que a rota
    retorna, então publicar no meio do handler anunciaria um pedido que ainda
    pode não existir: se o commit falhar, a cozinha fica com uma comanda
    fantasma na tela e o agente já imprimiu papel de uma venda que não
    aconteceu. Commitar antes de publicar troca esse erro pelo único aceitável
    — a venda existir e o aviso não sair, que o polling conserta sozinho.
    """
    await sessao.commit()
    await hub.publicar(evento, dados)
