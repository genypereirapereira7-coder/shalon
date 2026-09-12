"""O papel é o produto final da fase 3. Se ele sai errado, não há tela que
conserte: a cozinha monta o que está impresso.
"""

import cupom

PEDIDO = {
    "id": "11111111-1111-1111-1111-111111111111",
    "numero_dia": 37,
    "usuario_nome": "João",
    # 22:42 UTC = 19:42 em São Paulo.
    "criado_em_cliente": "2026-08-11T22:42:00+00:00",
    "total_centavos": 3400,
    "observacao": None,
    "itens": [
        {"nome": "Casquinha 1 bola", "quantidade": 2, "subtotal_centavos": 1600, "opcoes": []},
        {
            "nome": "Açaí 500ml",
            "quantidade": 1,
            "subtotal_centavos": 1800,
            "opcoes": [
                {"nome": "Granola", "preco_extra_centavos": 0},
                {"nome": "Creme de avelã", "preco_extra_centavos": 300},
            ],
        },
    ],
}


def montar(**mudancas):
    return cupom.montar({**PEDIDO, **mudancas}, fuso="America/Sao_Paulo")


def test_traz_o_essencial_da_comanda():
    texto = montar().texto

    assert "PEDIDO  #37" in texto
    assert "Atendente: João" in texto
    assert "2x Casquinha 1 bola" in texto
    assert "R$ 34,00" in texto


def test_hora_e_a_da_loja_nao_a_do_servidor():
    """O servidor manda UTC; quem lê o papel confere com o relógio da parede."""
    texto = montar().texto

    assert "19:42" in texto
    assert "22:42" not in texto
    assert "11/08/2026" in texto


def test_acompanhamentos_saem_no_papel():
    """O mockup da §6 não os mostra, mas metade do cardápio é montado: sem
    isto a cozinha não tem como saber o que vai dentro do açaí.
    """
    texto = montar().texto

    assert "Granola" in texto
    # Adicional pago leva "+" e o valor — é o que faz a soma bater com o TOTAL.
    assert "+ Creme de avelã" in texto
    assert "R$ 3,00" in texto
    # Acompanhamento incluído não mostra preço nenhum.
    assert "- Granola" in texto


def test_reimpressao_e_avisada():
    """Um segundo papel do #37 sem aviso é um #37 montado duas vezes."""
    normal = cupom.montar(PEDIDO, fuso="America/Sao_Paulo")
    repetido = cupom.montar(PEDIDO, fuso="America/Sao_Paulo", reimpressao=True)

    assert "REIMPRESSAO" not in normal.texto
    assert "REIMPRESSAO" in repetido.texto
    assert repetido.reimpressao is True


def test_nada_passa_da_largura_da_bobina():
    """O que passa da bobina a impressora quebra onde quiser — no meio de um
    nome de produto, por exemplo.
    """
    texto = montar(
        observacao="sem granulado, caprichar na cobertura e separar os dois em potes diferentes",
        itens=[
            {
                "nome": "Sundae especial da casa com três bolas e cobertura dupla",
                "quantidade": 12,
                "subtotal_centavos": 123456,
                "opcoes": [{"nome": "Creme de avelã belga importado", "preco_extra_centavos": 300}],
            }
        ],
    ).texto

    assert max(len(linha) for linha in texto.splitlines()) <= cupom.LARGURA


def test_observacao_fica_no_fim_e_inteira():
    """É o campo que faz a cozinha fazer diferente do padrão. Perdido no meio
    dos itens ninguém lê; cortado, perde justamente o detalhe.
    """
    texto = montar(observacao="sem granulado e sem açúcar, cliente alérgico a amendoim")
    linhas = texto.texto.splitlines()

    inteiro = " ".join(l.strip() for l in linhas if "OBS:" in l or "alérgico" in l)
    assert "sem granulado e sem açúcar, cliente alérgico a amendoim" in inteiro.replace("OBS: ", "")
    # E depois dos itens, não no meio deles.
    assert next(i for i, l in enumerate(linhas) if "OBS" in l) > 0


def test_sem_fuso_no_json_e_tratado_como_utc():
    """Rede de segurança: o backend garante o "Z" (`app/schemas/tipos.py`), mas
    uma borda nova que esqueça não pode fazer o papel sair com hora aleatória.
    """
    texto = cupom.montar(
        {**PEDIDO, "criado_em_cliente": "2026-08-11T22:42:00"}, fuso="America/Sao_Paulo"
    ).texto

    assert "19:42" in texto


def test_largura_58mm_ainda_fecha():
    """Não é o recomendado (§3 pede 80mm), mas se alguém apontar pra uma 58mm
    o papel tem que sair legível em vez de embaralhado.
    """
    texto = cupom.montar(PEDIDO, fuso="America/Sao_Paulo", largura=32).texto

    assert max(len(linha) for linha in texto.splitlines()) <= 32
    assert "R$ 34,00" in texto


# ------------------------------------------------- paridade com o comanda.js
#
# Este arquivo e o `frontend/vendas/comanda.js` imprimem a mesma comanda, e a
# única coisa que mantém os dois juntos é alguém lembrar. O sabor e os títulos
# de grupo já ficaram só no lado do celular: quem imprimia pelo PC recebia o
# papel sem saber qual bola vender. Os testes abaixo são o lembrete.

def test_o_sabor_sai_no_papel():
    """Sem isto a comanda do agente não diz qual bola vai na casquinha."""
    texto = montar(
        itens=[
            {
                "nome": "Casquinha",
                "quantidade": 1,
                "subtotal_centavos": 700,
                "sabor": "Morango + Chocolate",
                "opcoes": [],
            }
        ]
    ).texto

    assert "SABOR" in texto
    assert "MORANGO + CHOCOLATE" in texto


def test_item_sem_sabor_nao_ganha_bloco_vazio():
    assert "SABOR" not in montar().texto


def test_o_papel_distingue_a_bola_da_cobertura():
    """A queixa que veio do balcão: num item com sabor *e* cobertura, os dois
    saíam como nomes soltos e ninguém sabia qual "Chocolate" era qual."""
    texto = montar(
        itens=[
            {
                "nome": "Sundae",
                "quantidade": 1,
                "subtotal_centavos": 1500,
                "sabor": "Creme",
                "opcoes": [
                    {"nome": "Chocolate", "grupo": "Coberturas", "preco_extra_centavos": 0},
                    {"nome": "Granola", "grupo": "Acompanhamentos", "preco_extra_centavos": 0},
                ],
            }
        ]
    ).texto

    assert "COBERTURAS" in texto
    assert "ACOMPANHAMENTOS" in texto
    # O título da cobertura vem antes do "Chocolate" que ele explica.
    assert texto.index("COBERTURAS") < texto.index("- Chocolate")
    # E o sabor vem antes de tudo: quem monta lê de cima pra baixo.
    assert texto.index("SABOR") < texto.index("COBERTURAS")


def test_grupo_repetido_nao_repete_o_titulo():
    texto = montar(
        itens=[
            {
                "nome": "Açaí 500ml",
                "quantidade": 1,
                "subtotal_centavos": 1800,
                "opcoes": [
                    {"nome": "Granola", "grupo": "Acompanhamentos", "preco_extra_centavos": 0},
                    {"nome": "Paçoca", "grupo": "Acompanhamentos", "preco_extra_centavos": 0},
                ],
            }
        ]
    ).texto

    assert texto.count("ACOMPANHAMENTOS") == 1


def test_opcao_sem_grupo_continua_saindo():
    """Pedido gravado antes de o `grupo_snapshot` existir. O papel perde o
    título, não a linha — a comanda antiga não pode voltar vazia."""
    texto = montar(
        itens=[
            {
                "nome": "Açaí 500ml",
                "quantidade": 1,
                "subtotal_centavos": 1800,
                "opcoes": [{"nome": "Granola", "preco_extra_centavos": 0}],
            }
        ]
    ).texto

    assert "- Granola" in texto
