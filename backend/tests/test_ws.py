"""O canal WebSocket: quem entra, quem recebe o quê, e o que não pode quebrar.

Os testes não sobem transporte de WebSocket. O `hub` fala com um `Protocol` de
uma função só (`send_json`), então um objeto de três linhas assina os eventos e
o teste passa a ser sobre a regra — quem recebe `pedido.novo`, o que acontece
quando uma conexão morre — em vez de ser sobre o aperto de mão do protocolo.

O aperto de mão tem os seus próprios testes logo abaixo, chamando a rota
direto: é lá que mora a decisão de autenticar pela primeira mensagem em vez da
URL, e ela precisa valer.
"""

import asyncio

import pytest

from app.models.usuario import Papel
from app.rotas import ws as rota_ws
from app.seguranca import criar_token_acesso
from app.servicos.eventos import DESTINOS, Evento, Hub


class Espiao:
    """Uma conexão que só guarda o que recebeu."""

    def __init__(self) -> None:
        self.recebidos: list[dict] = []

    async def send_json(self, dados) -> None:
        self.recebidos.append(dados)

    def eventos(self) -> list[str]:
        return [m["evento"] for m in self.recebidos]


class Morta:
    """A tela que fechou o navegador sem encerrar o socket."""

    async def send_json(self, dados) -> None:
        raise ConnectionResetError("foi-se")


class Travada:
    """O PC de cozinha que congelou: aceita a conexão e nunca responde."""

    async def send_json(self, dados) -> None:
        await asyncio.sleep(3600)


# ------------------------------------------------------------------ roteamento

async def test_cada_evento_vai_pra_quem_a_arquitetura_manda():
    hub = Hub()
    cozinha, agente, dono, vendas = Espiao(), Espiao(), Espiao(), Espiao()
    hub.entrar(cozinha, Papel.COZINHA)
    hub.entrar(agente, Papel.AGENTE)
    hub.entrar(dono, Papel.DONO)
    hub.entrar(vendas, Papel.FUNCIONARIO)

    await hub.publicar(Evento.PEDIDO_NOVO, {"id": "1"})

    # O agente precisa pra imprimir; a cozinha, pra montar; o dono, pro total.
    # O balcão não: quem vendeu já tem o pedido na mão.
    assert cozinha.eventos() == ["pedido.novo"]
    assert agente.eventos() == ["pedido.novo"]
    assert dono.eventos() == ["pedido.novo"]
    assert vendas.eventos() == []


async def test_metricas_so_vao_pro_dono():
    hub = Hub()
    dono, cozinha = Espiao(), Espiao()
    hub.entrar(dono, Papel.DONO)
    hub.entrar(cozinha, Papel.COZINHA)

    await hub.publicar(Evento.METRICAS_TICK, {"total_centavos": 100})

    assert dono.eventos() == ["metricas.tick"]
    assert cozinha.eventos() == []


async def test_todo_evento_tem_destino_declarado():
    """Um evento sem linha no DESTINOS estouraria KeyError na hora de publicar
    — no meio de uma venda, que é o pior momento pra descobrir.
    """
    assert set(DESTINOS) == set(Evento)
    assert all(DESTINOS[e] for e in Evento)


async def test_quem_esta_em_dois_papeis_nao_recebe_duas_vezes():
    """O dono loga nos três PWAs. Uma comanda apitando duas vezes na mesma tela
    é a cozinha achando que entraram dois pedidos.
    """
    hub = Hub()
    espiao = Espiao()
    hub.entrar(espiao, Papel.COZINHA)
    hub.entrar(espiao, Papel.DONO)

    await hub.publicar(Evento.PEDIDO_NOVO, {"id": "1"})

    assert espiao.eventos() == ["pedido.novo"]


# --------------------------------------------------------- conexões problema

async def test_conexao_morta_nao_derruba_as_outras():
    """Uma tela que fechou o navegador não pode fazer o `POST /pedidos` falhar:
    a venda já aconteceu e o dinheiro já entrou na gaveta.
    """
    hub = Hub()
    viva = Espiao()
    hub.entrar(Morta(), Papel.COZINHA)
    hub.entrar(viva, Papel.COZINHA)

    await hub.publicar(Evento.PEDIDO_NOVO, {"id": "1"})

    assert viva.eventos() == ["pedido.novo"]


async def test_conexao_morta_sai_do_hub_sozinha():
    hub = Hub()
    hub.entrar(Morta(), Papel.COZINHA)
    assert hub.conectados(Papel.COZINHA) == 1

    await hub.publicar(Evento.PEDIDO_NOVO, {"id": "1"})

    assert hub.conectados(Papel.COZINHA) == 0


async def test_tela_travada_nao_segura_a_venda(monkeypatch):
    """Sem prazo de envio, um PC congelado penduraria a resposta HTTP de quem
    publicou — e o funcionário ficaria olhando o botão ENVIAR rodando.
    """
    monkeypatch.setattr("app.servicos.eventos.ENVIO_TIMEOUT_S", 0.05)
    hub = Hub()
    viva = Espiao()
    hub.entrar(Travada(), Papel.COZINHA)
    hub.entrar(viva, Papel.COZINHA)

    async with asyncio.timeout(2):
        await hub.publicar(Evento.PEDIDO_NOVO, {"id": "1"})

    assert viva.eventos() == ["pedido.novo"]
    assert hub.conectados(Papel.COZINHA) == 1  # a travada foi descartada


async def test_publicar_sem_ninguem_conectado_nao_faz_nada():
    await Hub().publicar(Evento.PEDIDO_NOVO, {"id": "1"})


# ------------------------------------------------------------- aperto de mão

class SocketFalso:
    """O mínimo de `WebSocket` que a rota usa."""

    def __init__(self, mensagens: list) -> None:
        self.aceitou = False
        self.enviados: list[dict] = []
        self.fechou_com: tuple[int, str] | None = None
        self._entrada = list(mensagens)

    async def accept(self) -> None:
        self.aceitou = True

    async def receive_json(self):
        if not self._entrada:
            # Nada mais a dizer: fica em silêncio até o prazo do servidor.
            await asyncio.sleep(3600)
        proxima = self._entrada.pop(0)
        if isinstance(proxima, Exception):
            raise proxima
        return proxima

    async def send_json(self, dados) -> None:
        self.enviados.append(dados)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.fechou_com = (code, reason)


@pytest.fixture
def token_cozinha():
    token, _ = criar_token_acesso(7, "Cozinha", Papel.COZINHA)
    return token


async def test_entra_com_o_token_na_primeira_mensagem(token_cozinha):
    """O token vai no corpo, não em `/ws?token=…`: a URL acabaria no log de
    requisições do Caddy e no histórico do navegador.
    """
    socket = SocketFalso([{"tipo": "auth", "token": token_cozinha}])

    tarefa = asyncio.create_task(rota_ws.canal(socket))
    await asyncio.sleep(0.05)

    assert socket.aceitou
    assert socket.enviados[0]["evento"] == "pronto"
    assert socket.enviados[0]["dados"]["papel"] == "COZINHA"
    # A hora do servidor vai junto: é com ela que a tela da cozinha corrige o
    # relógio do PC antes de julgar comanda atrasada.
    assert socket.enviados[0]["dados"]["agora"]
    assert rota_ws.hub.conectados(Papel.COZINHA) == 1

    tarefa.cancel()
    await asyncio.gather(tarefa, return_exceptions=True)
    rota_ws.hub.sair(socket, Papel.COZINHA)


async def test_token_invalido_fecha_a_conexao():
    socket = SocketFalso([{"tipo": "auth", "token": "nao-e-um-jwt"}])

    await rota_ws.canal(socket)

    assert socket.fechou_com == (rota_ws.FECHAR_POLITICA, "Token inválido ou expirado")
    assert socket.enviados == []


async def test_primeira_mensagem_sem_token_fecha():
    socket = SocketFalso([{"tipo": "ping"}])

    await rota_ws.canal(socket)

    assert socket.fechou_com == (rota_ws.FECHAR_POLITICA, "Token inválido ou expirado")


async def test_quem_conecta_e_fica_mudo_e_desligado(monkeypatch):
    """Sem prazo, um socket que abriu e nunca se identificou ocuparia lugar até
    o próximo restart do servidor.
    """
    monkeypatch.setattr(rota_ws, "PRAZO_AUTENTICACAO_S", 0.05)
    socket = SocketFalso([])

    await rota_ws.canal(socket)

    assert socket.fechou_com == (rota_ws.FECHAR_POLITICA, "Sem autenticação")


async def test_ping_recebe_pong(token_cozinha):
    socket = SocketFalso([{"tipo": "auth", "token": token_cozinha}, {"tipo": "ping"}])

    tarefa = asyncio.create_task(rota_ws.canal(socket))
    await asyncio.sleep(0.05)

    assert [m["evento"] for m in socket.enviados] == ["pronto", "pong"]

    tarefa.cancel()
    await asyncio.gather(tarefa, return_exceptions=True)
    rota_ws.hub.sair(socket, Papel.COZINHA)


async def test_socket_sem_sinal_de_vida_e_derrubado(monkeypatch):
    """O Wi-Fi da loja cai sem FIN e sem close. Sem este teto, sockets fantasmas
    se acumulariam — e um token vencido continuaria valendo.
    """
    monkeypatch.setattr(rota_ws, "PRAZO_OCIOSO_S", 0.05)
    token, _ = criar_token_acesso(7, "Cozinha", Papel.COZINHA)
    socket = SocketFalso([{"tipo": "auth", "token": token}])

    await rota_ws.canal(socket)

    assert socket.fechou_com == (1001, "Sem sinal de vida")
    assert rota_ws.hub.conectados(Papel.COZINHA) == 0


async def test_so_o_agente_fala_da_impressora():
    """Sem a checagem de papel, qualquer celular logado acenderia o alerta de
    impressora travada na cozinha.
    """
    token, _ = criar_token_acesso(2, "João", Papel.FUNCIONARIO)
    socket = SocketFalso([
        {"tipo": "auth", "token": token},
        {"tipo": "impressora.status", "ok": False, "detalhe": "sem papel"},
    ])
    espiao = Espiao()
    rota_ws.hub.entrar(espiao, Papel.COZINHA)

    tarefa = asyncio.create_task(rota_ws.canal(socket))
    await asyncio.sleep(0.05)

    assert espiao.eventos() == []

    tarefa.cancel()
    await asyncio.gather(tarefa, return_exceptions=True)
    rota_ws.hub.sair(espiao, Papel.COZINHA)
    rota_ws.hub.sair(socket, Papel.FUNCIONARIO)


async def test_agente_avisa_a_cozinha_que_a_impressora_travou():
    token, _ = criar_token_acesso(9, "Agente", Papel.AGENTE)
    socket = SocketFalso([
        {"tipo": "auth", "token": token},
        {"tipo": "impressora.status", "ok": False, "detalhe": "sem papel"},
    ])
    espiao = Espiao()
    rota_ws.hub.entrar(espiao, Papel.COZINHA)

    tarefa = asyncio.create_task(rota_ws.canal(socket))
    await asyncio.sleep(0.05)

    assert espiao.eventos() == ["impressora.status"]
    assert espiao.recebidos[0]["dados"] == {"ok": False, "detalhe": "sem papel"}

    tarefa.cancel()
    await asyncio.gather(tarefa, return_exceptions=True)
    rota_ws.hub.sair(espiao, Papel.COZINHA)
    rota_ws.hub.sair(socket, Papel.AGENTE)
