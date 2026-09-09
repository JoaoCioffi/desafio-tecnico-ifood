"""Integracao do ciclo de venda: publicar, pedir, cobrar, tirar do ar.

Roda DENTRO da imagem do MCP, contra um Postgres descartavel — nao contra o
banco do projeto. Quem orquestra e `provar.py`; este arquivo so faz as
verificacoes.

Aqui em vez de pytest porque estas asserções precisam de banco, de esquema
aplicado e de threads de verdade. A suite do `pytest` roda em 0,2s sem Docker
e sem rede, e misturar as duas coisas custaria essa propriedade.
"""
import os
import sys
import threading

sys.path.insert(0, os.environ.get("RAIZ_IMPORT", "/app"))

import mcp_server.repo as repo  # noqa: E402

repo.abrir()

ok = falhas = 0


def checar(rotulo, condicao, detalhe=""):
    global ok, falhas
    if condicao:
        ok += 1
        print(f"  ok      {rotulo}{('  ' + detalhe) if detalhe else ''}")
    else:
        falhas += 1
        print(f"  FALHOU  {rotulo}{('  ' + detalhe) if detalhe else ''}")


with repo._pool.connection() as c, c.cursor() as cur:
    cur.execute("DELETE FROM pedidos; DELETE FROM cardapio; "
                "DELETE FROM pratos_ingredientes; DELETE FROM compras; DELETE FROM pratos;")
    # As views partem de despensa/precos/orcamento — sem elas o vw_estoque nao
    # tem linha para o ingrediente e o vw_caixa nao tem linha nenhuma.
    cur.execute("""
        INSERT INTO despensa (ingrediente, unidade, quantidade, unidade_base, fator)
        VALUES ('Peito de frango', 'kg', 2, 'kg', 1)
        ON CONFLICT (ingrediente) DO NOTHING;
        INSERT INTO precos (ingrediente, unidade, qtd_comprada, preco_pago, unidade_base, fator)
        VALUES ('Peito de frango', 'kg', 2, 28.00, 'kg', 1)
        ON CONFLICT (ingrediente) DO NOTHING;
        INSERT INTO orcamento (id, valor_inicial) VALUES (1, 80.00)
        ON CONFLICT (id) DO UPDATE SET valor_inicial = 80.00;
    """)
    cur.execute("""
        INSERT INTO pratos (id, nome, porcoes, status, cmv, preco) VALUES
            (1, 'Frango com arroz', 4, 'aceito',   5.15, 17.00),
            (2, 'Prato sugerido',   4, 'sugerido', 9.00, 20.00);
        SELECT setval('pratos_id_seq', 2);
        INSERT INTO pratos_ingredientes (prato_id, ingrediente, quantidade, unidade_base)
        VALUES (1, 'Peito de frango', 0.400, 'kg');
    """)

print("\n  --- publicacao ---")
checar("prato SUGERIDO nao vai ao ar",
       repo.cardapio_publicar(2, 20.00, 1) is None)
checar("prato inexistente nao vai ao ar",
       repo.cardapio_publicar(999, 20.00, 1) is None)

pub = repo.cardapio_publicar(1, 17.00, 3)
checar("prato ACEITO vai ao ar", pub is not None)

item = repo.cardapio_listar()[0]
cid = item["cardapio_id"]
checar("oferta = lotes x porcoes", item["porcoes_disponiveis"] == 12,
       f"3 lotes x 4 porcoes = {item['porcoes_disponiveis']}")

repub = repo.cardapio_publicar(1, 19.00, 2)
checar("republicar ATUALIZA, nao duplica", len(repo.cardapio_listar()) == 1)
checar("republicar troca preco e lotes",
       float(repo.cardapio_listar()[0]["preco"]) == 19.00
       and repo.cardapio_listar()[0]["porcoes_disponiveis"] == 8)
repo.cardapio_publicar(1, 17.00, 3)

print("\n  --- publicar respeita a despensa ---")
# A despensa do teste tem 2 kg de frango e a receita pede 0,400 por lote.
# Cabem 5 lotes; o sexto nao existe.
repo.cardapio_retirar(1)
checar("recusa lotes que a despensa nao aguenta",
       repo.cardapio_publicar(1, 17.00, 6) is None, "pediu 6, cabem 5")
checar("aceita exatamente o que cabe",
       repo.cardapio_publicar(1, 17.00, 5) is not None)

cabe = repo.lotes_possiveis(1)
checar("lotes_possiveis diz o teto e quem limita",
       cabe["lotes"] == 5 and cabe["limitante"] == "Peito de frango",
       f"{cabe['lotes']} lotes, limita {cabe['limitante']}")

checar("republicar o MESMO numero nao se recusa sozinho",
       repo.cardapio_publicar(1, 18.00, 5) is not None,
       "o comprometido dele volta para a conta")

with repo._pool.connection() as cx, cx.cursor() as cur:
    neg = cur.execute("SELECT count(*) AS n FROM vw_estoque "
                      "WHERE disponivel < 0").fetchone()
checar("nenhum ingrediente ficou negativo", neg["n"] == 0)

repo.cardapio_publicar(1, 17.00, 3)   # volta ao cenario dos testes seguintes
# `cardapio_retirar` + publicar cria linha NOVA, com id novo. O `cid` de cima
# aponta para a publicacao aposentada — reler aqui e o que evita os testes
# seguintes cobrarem porcoes de um cardapio que saiu do ar.
cid = repo.cardapio_listar()[0]["cardapio_id"]

print("\n  --- pedido ---")
checar("nao vende prato que nao existe no cardapio",
       repo.pedido_registrar(99999, "ana", 1) is None)
checar("nao vende mais porcoes do que restam",
       repo.pedido_registrar(cid, "ana", 13) is None, "pediu 13 de 12")

p = repo.pedido_registrar(cid, "ana", 5)
checar("vende o que cabe", p is not None)
checar("o preco vem do CARDAPIO, nao do chamador",
       float(p["preco_unitario"]) == 17.00)
checar("a taxa de 10% e calculada pelo banco",
       float(p["valor_bruto"]) == 85.00 and float(p["taxa"]) == 8.50
       and float(p["valor_liquido"]) == 76.50,
       "85,00 - 8,50 = 76,50")
checar("as porcoes caem", repo.cardapio_listar()[0]["porcoes_disponiveis"] == 7)

print("\n  --- caixa ---")
c = repo.caixa()
checar("o liquido entra no saldo", float(c["saldo"]) == 156.50,
       f"80,00 + 76,50 = {float(c['saldo']):.2f}")

print("\n  --- estoque ---")
with repo._pool.connection() as cx, cx.cursor() as cur:
    linha = cur.execute("SELECT comprometido FROM vw_estoque "
                        "WHERE ingrediente = 'Peito de frango'").fetchone()
checar("publicar 3 lotes compromete 3x o ingrediente",
       linha and float(linha["comprometido"]) == 1.2, "0,400 x 3 = 1,200 kg")

print("\n  --- corrida: 12 pedidos simultaneos de 1 porcao, restam 7 ---")
resultados = []
trava = threading.Lock()


def pedir(n):
    r = repo.pedido_registrar(cid, f"cliente-{n}", 1)
    with trava:
        resultados.append(r is not None)


fios = [threading.Thread(target=pedir, args=(n,)) for n in range(12)]
for f in fios:
    f.start()
for f in fios:
    f.join()

vendidos = sum(resultados)
restam = repo.cardapio_listar()[0]["porcoes_disponiveis"]
checar("vendeu exatamente as 7 que restavam", vendidos == 7,
       f"{vendidos} aceitos, {12 - vendidos} recusados")
checar("nao ficou negativo", restam == 0, f"restam {restam}")

print("\n  --- despublicar ---")
antes = len(repo.pedidos_listar())
repo.cardapio_retirar(1)
checar("sai do cardapio", repo.cardapio_listar() == [])
checar("os pedidos sobrevivem", len(repo.pedidos_listar()) == antes,
       f"{antes} pedidos preservados")
checar("o dinheiro nao volta", float(repo.caixa()["receita_liquida"]) > 0,
       f"receita liquida R$ {float(repo.caixa()['receita_liquida']):.2f}")

repo.fechar()
print(f"\n  {ok} ok, {falhas} falha(s)")
sys.exit(1 if falhas else 0)
