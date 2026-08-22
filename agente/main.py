"""Agente de impressão: o programa que transforma pedido em papel.

**Caminho alternativo, não o principal.** Quem imprime a comanda no dia a dia é
o próprio celular do balcão, que despacha o texto pro RawBT assim que o servidor
confirma a venda (`frontend/vendas/impressao.js`). Este agente continua aqui
para a loja que preferir uma térmica presa a um PC — e os dois **não devem
rodar juntos**: cada um imprimiria a sua via da mesma comanda.

Roda num PC ligado à impressora e é o único pedaço do sistema que toca hardware. O laço é
simples de propósito, porque ele precisa sobreviver a um sábado inteiro sem
ninguém olhando:

    WebSocket ─ pedido.novo ─┐
                             ├─→ fila ─→ imprime ─→ confirma pro servidor
    varredura periódica ─────┘

**São duas fontes pro mesmo trabalho, e isso é a defesa principal.** O
WebSocket dá o papel em menos de um segundo; a varredura de
`/pedidos/nao-impressos` é o que garante que nenhuma comanda se perca quando o
Wi-Fi cair, o socket morrer sem avisar ou a impressora estiver sem papel na
hora. Se o WebSocket sumisse pra sempre, o agente continuaria imprimindo tudo —
só que com até 30 segundos de atraso.

**Só confirma depois que imprimiu.** O `impresso_em` do servidor é o que tira
a comanda da fila de não-impressos. Confirmar antes de o papel sair transformaria uma impressora travada
numa comanda que ninguém nunca vai buscar.

**Erra pro lado de imprimir duas vezes.** Se o agente imprimir e cair antes de
confirmar, a comanda sai de novo no próximo arranque — marcada como
REIMPRESSÃO, pra ninguém montar o pedido duas vezes. O erro oposto — a
comanda que nunca sai — é o cliente esperando no balcão.
"""

import asyncio
import json
import logging
import signal
import sys

import httpx
import websockets
from websockets.asyncio.client import connect

import cupom as formatador
from api import Api, ErroApi
from config import Config, carregar
from impressoras import criar
from impressoras.base import ErroImpressao, Impressora

log = logging.getLogger("agente")

# A rede de segurança do laço. Curto o bastante pra que uma comanda perdida
# apareça antes do cliente reclamar, longo o bastante pra não pesar no servidor.
VARREDURA_S = 30

# O servidor derruba socket sem sinal de vida em 60s. Este ping é de aplicação,
# não o do protocolo: o PING do WebSocket é respondido pelo uvicorn sem chegar
# na rota, então ele não serviria de prova de vida pra quem conta os segundos.
PING_S = 25

RECONEXAO_INICIAL_S = 1
RECONEXAO_MAX_S = 30


class Agente:
    def __init__(self, cfg: Config, api: Api, impressora: Impressora) -> None:
        self.cfg = cfg
        self.api = api
        self.impressora = impressora

        self.fila: asyncio.Queue[dict] = asyncio.Queue()
        # Ids que já estão na fila ou na mão do impressor. Sem isto, o pedido
        # que chega pelo socket e aparece na varredura logo em seguida sairia
        # duas vezes.
        self._na_fila: set[str] = set()
        # Ids que já saíram no papel nesta sessão — é o que distingue uma
        # comanda de uma reimpressão.
        self._ja_saiu: set[str] = set()
        self._ws = None
        self._impressora_ok = True

    # ------------------------------------------------------------------ fila

    def enfileirar(self, pedido: dict) -> None:
        pedido_id = pedido["id"]
        if pedido_id in self._na_fila:
            return
        self._na_fila.add(pedido_id)
        self.fila.put_nowait(pedido)

    async def imprimir_sem_parar(self) -> None:
        """Um consumidor só, de propósito: existe uma impressora.

        Duas tarefas imprimindo dariam comandas intercaladas no meio da bobina,
        e a cozinha produz na ordem da venda.
        """
        while True:
            pedido = await self.fila.get()
            try:
                await self._imprimir(pedido)
            except Exception:
                log.exception("erro inesperado imprimindo o pedido #%s", pedido.get("numero_dia"))
            finally:
                self._na_fila.discard(pedido["id"])
                self.fila.task_done()

    async def _imprimir(self, pedido: dict) -> None:
        papel = formatador.montar(
            pedido,
            fuso=self.cfg.fuso,
            reimpressao=pedido["id"] in self._ja_saiu,
            largura=self.cfg.largura,
        )

        try:
            # Numa thread: falar com a térmica é bloqueante, e travar o laço
            # pararia os pings e a varredura junto.
            await asyncio.to_thread(self.impressora.imprimir, papel)
        except ErroImpressao as erro:
            log.error("não saiu papel do pedido #%s: %s", papel.numero, erro)
            await self._avisar_impressora(False, str(erro))
            return  # sem ACK: a varredura traz de volta em até 30s

        self._ja_saiu.add(pedido["id"])
        await self._avisar_impressora(True, "")

        try:
            await self.api.confirmar_impressao(pedido["id"])
            log.info("pedido #%s impresso e confirmado", papel.numero)
        except Exception as erro:
            # `Exception` e não só os erros de HTTP: a esta altura o papel já
            # saiu da impressora. Nada que aconteça no aviso — timeout, socket
            # derrubado, DNS — pode ser tratado como falha de impressão. A
            # varredura traz o pedido de novo e ele sai marcado como
            # REIMPRESSÃO, que é o desfecho certo pra um ACK perdido.
            log.warning("pedido #%s saiu mas o ACK falhou: %s", papel.numero, erro)

    async def _avisar_impressora(self, ok: bool, detalhe: str) -> None:
        """Conta pra cozinha e pro dono como está a impressora.

        Só quando muda: um evento por comanda impressa seria ruído, e o que
        interessa é a virada de "imprimindo" pra "travada".
        """
        if ok == self._impressora_ok:
            return
        self._impressora_ok = ok
        await self._mandar({"tipo": "impressora.status", "ok": ok, "detalhe": detalhe})

    # -------------------------------------------------------------- servidor

    async def varrer_sem_parar(self) -> None:
        """Pergunta ao servidor o que ficou sem imprimir, pra sempre."""
        while True:
            try:
                pendentes = await self.api.nao_impressos()
                for pedido in pendentes:
                    self.enfileirar(pedido)
                if pendentes:
                    log.info("varredura achou %d comanda(s) pendente(s)", len(pendentes))
            except (ErroApi, httpx.HTTPError) as erro:
                log.warning("varredura falhou (tento de novo em %ss): %s", VARREDURA_S, erro)

            await asyncio.sleep(VARREDURA_S)

    async def escutar_sem_parar(self) -> None:
        """Mantém o WebSocket de pé, com espera crescente entre as tentativas."""
        espera = RECONEXAO_INICIAL_S
        while True:
            try:
                if await self._sessao_ws():
                    # A paciência só zera depois de um aperto de mão que deu
                    # certo. Zerar por ter aberto a conexão faria uma credencial
                    # recusada virar uma batida por segundo, pra sempre.
                    espera = RECONEXAO_INICIAL_S
                else:
                    # Abriu e o servidor fechou sem aceitar: é a credencial, não
                    # a rede. Um socket já aberto não tem 401 pra disparar a
                    # renovação automática do `api.py`, então é aqui.
                    await self._renovar_credencial()
            except Exception as erro:
                # Rede fora. Não renova nada: o token provavelmente está bom, e
                # queimar um refresh por queda de Wi-Fi só gasta sessão.
                log.warning("websocket caiu (%s). Reconecto em %ss", erro, espera)

            await asyncio.sleep(espera)
            espera = min(espera * 2, RECONEXAO_MAX_S)

    async def _sessao_ws(self) -> bool:
        """Uma sessão inteira do socket. Devolve se o servidor chegou a aceitar."""
        if not self.api.acesso:
            await self.api.entrar()

        aceito = False
        async with connect(self.cfg.url_ws, open_timeout=10, max_queue=64) as ws:
            await ws.send(json.dumps({"tipo": "auth", "token": self.api.acesso}))
            self._ws = ws

            pinger = asyncio.create_task(self._pingar(ws))
            try:
                async for bruto in ws:
                    if self._receber(bruto) and not aceito:
                        aceito = True
                        log.info("websocket conectado em %s", self.cfg.url_ws)
                        # Reconectou: o que aconteceu enquanto estávamos fora
                        # não veio por evento nenhum. Perguntar fecha o buraco.
                        await self._varrer_agora()
            finally:
                pinger.cancel()
                self._ws = None

        if not aceito:
            log.warning("o servidor recusou a conexão (código %s)", ws.close_code)
        return aceito

    async def _renovar_credencial(self) -> None:
        try:
            if not await self.api.renovar():
                await self.api.entrar()
        except Exception as erro:
            log.warning("não consegui renovar a credencial: %s", erro)

    def _receber(self, bruto: str | bytes) -> bool:
        """Trata uma mensagem. Devolve `True` no `pronto` — o aceite do servidor."""
        try:
            mensagem = json.loads(bruto)
        except (ValueError, TypeError):
            return False

        if mensagem.get("evento") == "pronto":
            return True

        if mensagem.get("evento") == "pedido.novo":
            pedido = mensagem.get("dados") or {}
            if pedido.get("id"):
                log.info("chegou o pedido #%s pelo socket", pedido.get("numero_dia"))
                self.enfileirar(pedido)

        return False

    async def _pingar(self, ws) -> None:
        """Prova de vida periódica. Morre em silêncio quando o socket fecha.

        Sem o `try`, o tombo normal de uma conexão viraria um "Task exception
        was never retrieved" no console — e o console deste programa é a única
        ferramenta de suporte que a loja tem.
        """
        try:
            while True:
                await asyncio.sleep(PING_S)
                await ws.send(json.dumps({"tipo": "ping"}))
        except (websockets.exceptions.WebSocketException, RuntimeError):
            pass

    async def _mandar(self, mensagem: dict) -> None:
        """Manda pro servidor se o socket estiver de pé; senão, deixa passar.

        O status da impressora é informativo. Guardá-lo numa fila pra entregar
        depois avisaria a cozinha de um problema que já passou.
        """
        if self._ws is None:
            return
        try:
            await self._ws.send(json.dumps(mensagem))
        except (websockets.exceptions.WebSocketException, RuntimeError):
            log.debug("não deu pra mandar %s", mensagem.get("tipo"))

    async def _varrer_agora(self) -> None:
        try:
            for pedido in await self.api.nao_impressos():
                self.enfileirar(pedido)
        except (ErroApi, httpx.HTTPError) as erro:
            log.warning("varredura na conexão falhou: %s", erro)


# --------------------------------------------------------------------- setup

async def executar(caminho_config: str) -> None:
    cfg = carregar(caminho_config)
    impressora = criar(cfg.impressora, cfg.opcoes_impressora)
    api = Api(cfg.url, cfg.usuario, cfg.pin)

    log.info("impressora: %s", cfg.impressora)
    if not impressora.esta_ok():
        # Aviso, não parada: a impressora pode estar só sem papel, e o agente
        # precisa estar de pé pra imprimir quando alguém repuser a bobina.
        log.warning("a impressora não respondeu OK no arranque — confira papel e cabo")

    await api.entrar()

    agente = Agente(cfg, api, impressora)
    tarefas = [
        asyncio.create_task(agente.imprimir_sem_parar()),
        asyncio.create_task(agente.varrer_sem_parar()),
        asyncio.create_task(agente.escutar_sem_parar()),
    ]

    parar = asyncio.Event()
    _ouvir_sinais(parar)

    try:
        await parar.wait()
    finally:
        log.info("encerrando…")
        for tarefa in tarefas:
            tarefa.cancel()
        await asyncio.gather(*tarefas, return_exceptions=True)
        await api.fechar()
        impressora.fechar()


def _ouvir_sinais(parar: asyncio.Event) -> None:
    laco = asyncio.get_running_loop()
    for sinal in (signal.SIGINT, signal.SIGTERM):
        try:
            laco.add_signal_handler(sinal, parar.set)
        except NotImplementedError:
            # Windows não implementa add_signal_handler no laço do asyncio; lá
            # o Ctrl+C vira KeyboardInterrupt e quem trata é o `main()`.
            signal.signal(sinal, lambda *_: parar.set())


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%d/%m %H:%M:%S",
    )
    caminho = sys.argv[1] if len(sys.argv) > 1 else "config.ini"
    try:
        asyncio.run(executar(caminho))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
