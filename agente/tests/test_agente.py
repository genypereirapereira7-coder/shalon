"""As regras do laço que custam dinheiro se estiverem erradas.

Nenhum destes testes precisa de servidor, de impressora ou de rede: a `Api` e a
`Impressora` são o suficiente pra fingir, e é justamente por isso que o §3
mandou o agente falar com um protocolo em vez de um modelo de impressora.
"""

import asyncio
import json

import pytest

from config import Config
from cupom import Cupom
from impressoras.base import ErroImpressao
from main import Agente

PEDIDO = {
    "id": "11111111-1111-1111-1111-111111111111",
    "numero_dia": 37,
    "usuario_nome": "João",
    "criado_em_cliente": "2026-08-11T22:42:00+00:00",
    "total_centavos": 800,
    "observacao": None,
    "itens": [
        {"nome": "Casquinha 1 bola", "quantidade": 1, "subtotal_centavos": 800, "opcoes": []}
    ],
}


class ImpressoraDeMentira:
    def __init__(self, falhar: bool = False) -> None:
        self.falhar = falhar
        self.saiu: list[Cupom] = []

    def imprimir(self, cupom: Cupom) -> None:
        if self.falhar:
            raise ErroImpressao("sem papel")
        self.saiu.append(cupom)

    def esta_ok(self) -> bool:
        return not self.falhar

    def fechar(self) -> None:
        pass


class ApiDeMentira:
    def __init__(self) -> None:
        self.confirmados: list[str] = []
        self.acesso = "token"
        self.renovacoes = 0
        self.logins = 0
        self.renovar_funciona = True

    async def confirmar_impressao(self, pedido_id: str) -> None:
        self.confirmados.append(pedido_id)

    async def nao_impressos(self) -> list[dict]:
        return []

    async def renovar(self) -> bool:
        self.renovacoes += 1
        return self.renovar_funciona

    async def entrar(self) -> None:
        self.logins += 1
        self.acesso = "token-novo"


@pytest.fixture
def cfg():
    return Config(
        url="http://teste", usuario="Agente", pin="0000", impressora="fake",
        fuso="America/Sao_Paulo", largura=48,
    )


@pytest.fixture
def montar(cfg):
    def _montar(falhar: bool = False):
        impressora = ImpressoraDeMentira(falhar)
        api = ApiDeMentira()
        return Agente(cfg, api, impressora), api, impressora

    return _montar


async def esvaziar(agente: Agente) -> None:
    """Roda o que está na fila, sem deixar o consumidor rodando pra sempre."""
    while not agente.fila.empty():
        pedido = agente.fila.get_nowait()
        try:
            await agente._imprimir(pedido)
        finally:
            agente._na_fila.discard(pedido["id"])
            agente.fila.task_done()


# ------------------------------------------------------------- não duplicar

async def test_socket_e_varredura_nao_imprimem_duas_vezes(montar):
    """O pedido chega pelo WebSocket e reaparece na varredura logo em seguida.

    São duas fontes pro mesmo trabalho de propósito (é isso que impede comanda
    perdida), então a fila precisa saber que é o mesmo pedido.
    """
    agente, api, impressora = montar()

    agente.enfileirar(PEDIDO)
    agente.enfileirar(dict(PEDIDO))  # a mesma comanda, outro objeto
    await esvaziar(agente)

    assert len(impressora.saiu) == 1
    assert api.confirmados == [PEDIDO["id"]]


async def test_pedido_ja_impresso_sai_marcado_como_reimpressao(montar):
    """Segundo papel do #37 sem aviso é um #37 montado duas vezes."""
    agente, _, impressora = montar()

    agente.enfileirar(PEDIDO)
    await esvaziar(agente)
    agente.enfileirar(PEDIDO)
    await esvaziar(agente)

    assert [c.reimpressao for c in impressora.saiu] == [False, True]
    assert "REIMPRESSAO" in impressora.saiu[1].texto


# ------------------------------------------------------- falha de impressão

async def test_impressora_travada_nao_confirma(montar):
    """Confirmar sem papel apagaria o alerta vermelho da tela da cozinha e
    tiraria a comanda da fila de não-impressos: ela nunca mais sairia.
    """
    agente, api, _ = montar(falhar=True)

    agente.enfileirar(PEDIDO)
    await esvaziar(agente)

    assert api.confirmados == []


async def test_comanda_que_falhou_volta_pela_varredura(montar):
    """Como não foi confirmada, o servidor continua devolvendo o pedido — e a
    fila precisa aceitá-lo de novo em vez de achar que já cuidou dele.
    """
    agente, api, impressora = montar(falhar=True)

    agente.enfileirar(PEDIDO)
    await esvaziar(agente)

    impressora.falhar = False  # repuseram a bobina
    agente.enfileirar(PEDIDO)
    await esvaziar(agente)

    assert len(impressora.saiu) == 1
    assert api.confirmados == [PEDIDO["id"]]
    # E sai como comanda normal: o primeiro papel nunca existiu.
    assert impressora.saiu[0].reimpressao is False


async def test_ack_perdido_deixa_a_comanda_sair_de_novo(montar):
    """O papel saiu, o aviso não chegou. A varredura traz o pedido de volta e
    ele sai marcado como REIMPRESSÃO — o desfecho certo pra um ACK perdido.
    """
    agente, api, impressora = montar()

    async def confirmar_falhando(_):
        raise ConnectionError("sem rede")

    api.confirmar_impressao = confirmar_falhando
    agente.enfileirar(PEDIDO)
    await esvaziar(agente)

    assert len(impressora.saiu) == 1
    assert api.confirmados == []

    api.confirmar_impressao = ApiDeMentira.confirmar_impressao.__get__(api)
    agente.enfileirar(PEDIDO)
    await esvaziar(agente)

    assert impressora.saiu[1].reimpressao is True
    assert api.confirmados == [PEDIDO["id"]]


# ------------------------------------------------------- aviso da impressora

async def test_status_da_impressora_so_avisa_quando_muda(montar):
    """Um evento por comanda impressa seria ruído na tela da cozinha; o que
    interessa é a virada de "imprimindo" pra "travada".
    """
    agente, _, impressora = montar()
    avisos: list[dict] = []
    agente._mandar = lambda m: avisos.append(m) or _nada()

    agente.enfileirar(PEDIDO)
    await esvaziar(agente)
    assert avisos == []  # já começa OK: nada mudou

    impressora.falhar = True
    agente.enfileirar({**PEDIDO, "id": "2", "numero_dia": 38})
    await esvaziar(agente)
    assert [a["ok"] for a in avisos] == [False]

    impressora.falhar = False
    agente.enfileirar({**PEDIDO, "id": "3", "numero_dia": 39})
    await esvaziar(agente)
    assert [a["ok"] for a in avisos] == [False, True]


async def _nada():
    return None


# ------------------------------------------------------- credencial vencida

async def test_recusa_do_servidor_renova_a_credencial(montar, monkeypatch):
    """O servidor derruba quem apresenta token vencido, e num socket já aberto
    não existe 401 pra disparar a renovação automática do `api.py`.

    Sem isto, o agente reconectaria pra sempre com a mesma credencial velha —
    de segundo em segundo, porque a espera crescente também zerava à toa. Uma
    cozinha inteira sem comanda e um log rolando sozinho.
    """
    monkeypatch.setattr("main.RECONEXAO_INICIAL_S", 0)
    agente, api, _ = montar()
    tentativas = []

    async def recusar():
        tentativas.append(1)
        if len(tentativas) >= 3:
            raise asyncio.CancelledError  # corta o laço infinito do teste
        return False  # o servidor não chegou a aceitar

    agente._sessao_ws = recusar

    with pytest.raises(asyncio.CancelledError):
        await agente.escutar_sem_parar()

    assert api.renovacoes == 2


async def test_queda_de_rede_nao_queima_refresh(montar, monkeypatch):
    """Renovar a cada tombo de Wi-Fi gastaria sessão à toa: o refresh é
    rotativo, e o token provavelmente está bom — quem caiu foi a rede.
    """
    monkeypatch.setattr("main.RECONEXAO_INICIAL_S", 0)
    agente, api, _ = montar()
    tentativas = []

    async def cair():
        tentativas.append(1)
        if len(tentativas) >= 3:
            raise asyncio.CancelledError
        raise ConnectionRefusedError("servidor fora do ar")

    agente._sessao_ws = cair

    with pytest.raises(asyncio.CancelledError):
        await agente.escutar_sem_parar()

    assert api.renovacoes == 0
    assert api.logins == 0


async def test_renovacao_recusada_faz_login_novo(montar):
    """Refresh vencido depois de um domingo fechado: relogar é a saída."""
    agente, api, _ = montar()
    api.renovar_funciona = False

    await agente._renovar_credencial()

    assert api.renovacoes == 1
    assert api.logins == 1
    assert api.acesso == "token-novo"


def test_pronto_e_o_aceite_do_servidor(montar):
    """É o `pronto` que diz que o token passou — não o socket ter aberto."""
    agente, _, _ = montar()

    assert agente._receber(json.dumps({"evento": "pronto", "dados": {}})) is True
    assert agente._receber(json.dumps({"evento": "pong", "dados": {}})) is False
    assert agente._receber("isto não é json") is False


def test_pedido_novo_do_socket_entra_na_fila(montar):
    agente, _, _ = montar()

    agente._receber(json.dumps({"evento": "pedido.novo", "dados": PEDIDO}))

    assert agente.fila.qsize() == 1


# ------------------------------------------------------------------ config

def test_url_do_ws_sai_do_mesmo_servidor():
    """Duas chaves apontando pro mesmo lugar é uma chance de divergirem."""
    http = Config(url="http://127.0.0.1:8000", usuario="Agente", pin="0", impressora="fake")
    https = Config(url="https://shalon.app.br/", usuario="Agente", pin="0", impressora="fake")

    assert http.url_ws == "ws://127.0.0.1:8000/ws"
    assert https.url_ws == "wss://shalon.app.br/ws"
