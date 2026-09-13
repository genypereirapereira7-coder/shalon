"""Teste de fumaça: exercita contra o servidor de verdade o que foi corrigido."""
import json, urllib.request, urllib.error, uuid
from datetime import datetime, timezone

B = "http://127.0.0.1:8123"
ok = falhas = 0

def chamar(metodo, caminho, corpo=None, token=None):
    req = urllib.request.Request(B + caminho, method=metodo)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    dados = None
    if corpo is not None:
        req.add_header("Content-Type", "application/json")
        dados = json.dumps(corpo).encode()
    try:
        with urllib.request.urlopen(req, dados) as r:
            texto = r.read().decode()
            return r.status, (json.loads(texto) if texto else None), dict(r.headers)
    except urllib.error.HTTPError as e:
        texto = e.read().decode()
        return e.code, (json.loads(texto) if texto else None), dict(e.headers)

def confere(rotulo, condicao, detalhe=""):
    global ok, falhas
    if condicao:
        ok += 1
        print(f"  ✅ {rotulo}")
    else:
        falhas += 1
        print(f"  ❌ {rotulo}  {detalhe}")

print("\n#1 segurança: a senha do repositório e o PIN 0000")
s, _, _ = chamar("POST", "/auth/login", {"usuario": "Agente de impressão", "segredo": "0000"})
confere("conta do agente com PIN 0000 não entra", s == 401, f"HTTP {s}")

print("\n#2 login do dono e a sessão que não pede senha de novo")
s, tok, _ = chamar("POST", "/auth/login",
                   {"usuario": "Adriano", "segredo": "adriano212121", "dispositivo": "fumaca"})
confere("dono entra com a senha do dev.py", s == 200 and tok and "acesso" in tok, f"HTTP {s} {tok}")
acesso, refresh = tok["acesso"], tok["refresh"]

s, novo, _ = chamar("POST", "/auth/renovar", {"refresh": refresh})
# O access token pode sair idêntico quando os dois são assinados no mesmo
# segundo — `iat`/`exp` têm resolução de segundo e o resto do corpo é igual.
# O que prova a renovação é o refresh ter rotacionado e o token novo funcionar.
confere("refresh renova sem pedir senha (é isto que evita o re-login)",
        s == 200 and novo["refresh"] != refresh, f"HTTP {s}")
acesso, refresh = novo["acesso"], novo["refresh"]
s, eu, _ = chamar("GET", "/auth/eu", token=acesso)
confere("token renovado é aceito", s == 200 and eu["nome"] == "Adriano", f"HTTP {s} {eu}")
s, _, _ = chamar("POST", "/auth/renovar", {"refresh": refresh})
confere("refresh rotacionado: o novo também serve", s == 200, f"HTTP {s}")

print("\n#4 cabeçalhos: o frame-src que a impressão do RawBT precisa")
s, _, cab = chamar("GET", "/health")
csp = cab.get("content-security-policy", "")
confere("CSP traz frame-src 'self' intent:", "frame-src 'self' intent:" in csp, csp[:90])

print("\n#5 sabor do dia")
s, _, _ = chamar("PUT", "/sabores", {"sabor1": "Morango", "sabor2": " Creme "}, acesso)
confere("dono grava os sabores", s == 200, f"HTTP {s}")
s, sab, _ = chamar("GET", "/sabores", token=acesso)
confere("espaço nas pontas some", sab["sabor2"] == "Creme", sab)

print("\n#16 produto novo em categoria de bola já nasce pedindo sabor")
s, cardapio, _ = chamar("GET", "/cardapio?incluir_inativos=true", token=acesso)
sorvetes = next(c for c in cardapio["categorias"] if c["nome"] == "Sorvetes")

# Só cria se ainda não existe, e sai deixando o produto desativado.
#
# A primeira versão criava um produto a cada execução: depois de três rodadas o
# cardápio tinha três "Casquinha de fumaça" e o balcão via as três. Teste que
# suja o banco em que roda é teste que ninguém roda duas vezes — ou pior, que
# alguém roda contra a loja de verdade sem pensar. Não há rota pra apagar
# produto (de propósito: o histórico de vendas aponta pra ele), então o que dá
# pra fazer é reaproveitar e desligar.
NOME_COBAIA = "Casquinha de fumaça"
cobaia = next((p for p in sorvetes["produtos"] if p["nome"] == NOME_COBAIA), None)

if cobaia is None:
    s, novo_prod, _ = chamar("POST", "/produtos",
        {"categoria_id": sorvetes["id"], "nome": NOME_COBAIA, "preco_centavos": 750}, acesso)
    confere("POST /produtos força pede_sabor em Sorvetes",
            s == 201 and novo_prod["pede_sabor"] is True, f"HTTP {s} {novo_prod}")
else:
    confere("POST /produtos força pede_sabor em Sorvetes (produto da rodada anterior)",
            cobaia["pede_sabor"] is True, cobaia)
    novo_prod = cobaia

chamar("PATCH", f"/produtos/{novo_prod['id']}", {"ativo": False}, acesso)

s, antes, _ = chamar("GET", "/relatorios/hoje", token=acesso)
base_qtd, base_total = antes["qtd_pedidos"], antes["total_centavos"]

print("\n#10 venda de ponta a ponta, com sabor e numeração")
casquinha = next(p for p in sorvetes["produtos"] if p["nome"] == "Casquinha")
grupo = casquinha["grupos"][0]
cobertura = grupo["opcoes"][0]["id"]
agora = datetime.now(timezone.utc).isoformat()
idc = str(uuid.uuid4())
pedido = {
    "id_cliente": idc, "criado_em_cliente": agora,
    "itens": [{"produto_id": casquinha["id"], "quantidade": 2,
               "opcoes": [cobertura], "sabores": ["SABOR_1", "SABOR_2"]}],
    "observacao": "sem guardanapo", "total_centavos": casquinha["preco_centavos"] * 2,
}
s, venda, _ = chamar("POST", "/pedidos", pedido, acesso)
confere("venda criada (201)", s == 201, f"HTTP {s} {venda}")
confere("sabor congelado no item", venda["itens"][0]["sabor"] == "Morango + Creme",
        venda["itens"][0])
confere("total é o do servidor", venda["total_centavos"] == casquinha["preco_centavos"] * 2, venda["total_centavos"])
primeiro_numero = venda["numero_dia"]

s, repetido, _ = chamar("POST", "/pedidos", pedido, acesso)
confere("reenvio do mesmo id_cliente é idempotente (200, não 201)",
        s == 200 and repetido["duplicado"] is True, f"HTTP {s}")

pedido2 = dict(pedido, id_cliente=str(uuid.uuid4()))
s, venda2, _ = chamar("POST", "/pedidos", pedido2, acesso)
confere("numeração do dia não pula (#%d → #%d)" % (primeiro_numero, venda2["numero_dia"]),
        venda2["numero_dia"] == primeiro_numero + 1, venda2["numero_dia"])

print("\n#relatório do dono")
s, resumo, _ = chamar("GET", "/relatorios/hoje", token=acesso)
# Relativo ao que já havia: o banco de dev sobrevive entre rodadas, e fixar o
# número absoluto fazia a segunda execução "falhar" sem nada estar errado.
confere("resumo soma exatamente as duas vendas desta rodada",
        resumo["qtd_pedidos"] == base_qtd + 2
        and resumo["total_centavos"] == base_total + casquinha["preco_centavos"] * 4,
        f'antes={base_qtd}/{base_total} agora={resumo["qtd_pedidos"]}/{resumo["total_centavos"]}')

print(f"\n{'='*52}\n  {ok} passaram, {falhas} falharam\n{'='*52}")
raise SystemExit(1 if falhas else 0)
