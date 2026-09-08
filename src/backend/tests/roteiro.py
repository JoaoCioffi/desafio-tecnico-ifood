"""Roteiro de ponta a ponta pelas ferramentas do MCP, sem modelo nenhum.

    python -m mcp_server                      # noutro terminal
    python src/backend/tests/roteiro.py

O agente e uma das formas de dirigir este backend, nao a unica. Aqui o roteiro
faz o papel dele — as MESMAS chamadas, na mesma ordem, contra o servidor de
verdade pela rede. O que muda e o custo: milissegundos em vez de minutos, e o
resultado nao depende de qual token o modelo sorteou.

Serve para tres coisas:

  * exercitar caminhos que uma conversa raramente alcanca — preco abaixo do
    minimo, orcamento estourado, ferramenta chamada fora de ordem
  * provar que as recusas do servidor sao invariantes, e nao instrucoes de
    prompt: aqui nao ha prompt nenhum e elas continuam valendo
  * dar um teste de regressao do backend que roda sem GPU

Sai 0 se tudo aconteceu como esperado, 1 na primeira divergencia.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

import psycopg
from fastmcp import Client

URL = "http://127.0.0.1:9000/mcp"
DSN = os.environ.get(
    "SABOR_DSN",
    "host=localhost port=5432 dbname=sabor_da_maria user=admin password=admin",
)

# Teste com estado precisa zerar o estado. A primeira versao passava so na
# primeira execucao: na segunda o prato ja existia, os precos ja estavam
# gravados e o utensilio ja respondido — e seis conferencias viravam falsas
# sem que nada tivesse quebrado.
#
# Zera o transacional e devolve a despensa ao que o ETL carregou: apaga os
# ingredientes que o roteiro inventa e limpa o preco dos que ele mexe.
INVENTADOS = ("Bacalhau importado", "Azeite extra virgem", "Champignon fatiado")
MEXIDOS = ("Peito de frango", "Creme de leite", "Arroz")

# Uma instrucao por execute: com parametro, o psycopg usa prepared statement e
# ele nao aceita varios comandos de uma vez.
RESET = (
    ("TRUNCATE pratos_ingredientes, pratos, perfil, evento RESTART IDENTITY CASCADE", None),
    ("DELETE FROM ingredientes WHERE nome = ANY(%s)", (list(INVENTADOS),)),
    ("UPDATE ingredientes SET qtd_comprada = NULL, preco_pago = NULL WHERE nome = ANY(%s)",
     (list(MEXIDOS),)),
)


def zerar() -> None:
    with psycopg.connect(DSN, connect_timeout=5) as conn:
        for sql, args in RESET:
            conn.execute(sql, args)
        conn.commit()

VERDE, VERM, AMAR, CINZA, R = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"

falhas: list[str] = []


def conferir(rotulo: str, condicao: bool, detalhe: str = "") -> None:
    marca = f"{VERDE}✓{R}" if condicao else f"{VERM}✕{R}"
    print(f"    {marca} {rotulo}{CINZA}{'  ' + detalhe if detalhe else ''}{R}")
    if not condicao:
        falhas.append(rotulo)


def etapa(texto: str) -> None:
    print(f"\n  {CINZA}—{R} {texto}")


async def chamar(cli: Client, _ferramenta: str, **args):
    """Chama a ferramenta e devolve (dados, recusa). Recusa nao levanta aqui.

    O parametro leva underscore de proposito: sem ele, um prato com a chave
    `nome` colidia com o nome da ferramenta no **args.
    """
    try:
        r = await cli.call_tool(_ferramenta, args)
    except Exception as erro:
        return None, str(erro)
    bruto = r.structured_content if r.structured_content is not None else r.content
    if isinstance(bruto, list) and bruto and hasattr(bruto[0], "text"):
        bruto = json.loads(bruto[0].text)
    if isinstance(bruto, dict) and set(bruto) == {"result"}:
        bruto = bruto["result"]
    return bruto, None


# --------------------------------------------------------------------------- #
STROGONOFF = {
    "nome": "Strogonoff de Frango",
    "fonte": "https://www.tudogostoso.com.br/receita/6178-strogonoff-de-frango.html",
    "ingredientes": [
        {"ingrediente": "Peito de frango", "quantidade": 0.180},
        {"ingrediente": "Creme de leite", "quantidade": 0.100},
        {"ingrediente": "Cebola", "quantidade": 0.040},
        {"ingrediente": "Alho", "quantidade": 0.008},
        {"ingrediente": "Arroz", "quantidade": 0.120},
        {"ingrediente": "Champignon fatiado", "quantidade": 0.030},
        {"ingrediente": "Sal", "quantidade": 0.003},
    ],
    "requisitos": [{"categoria": "utensilio", "item": "frigideira grande"}],
}

# Precos que a Dona Maria responderia, na embalagem em que ela compra. O creme
# de leite testa a normalizacao: R$ 4,50 a caixinha de 200 g e R$ 22,50/kg.
PRECOS = [
    ("Peito de frango", 22.90, 1, "kg"),
    ("Creme de leite", 4.50, 1, "caixinha 200g"),
    ("Arroz", 28.00, 1, "pacote 5kg"),
    ("Champignon fatiado", 7.90, 1, "vidro 200g"),
]

# Porcao dentro da faixa (600 g) mas com ingrediente caro o bastante para as
# compras nao caberem no que sobrou dos R$ 80. E o caso que uma conversa
# raramente alcanca: o orcamento e do CARDAPIO, entao ele so estoura depois de
# um prato ja aceito ter consumido parte dele.
BANQUETE = {
    "nome": "Bacalhoada de Domingo",
    "fonte": "https://www.tudogostoso.com.br/receita/1234-bacalhoada.html",
    "ingredientes": [
        {"ingrediente": "Bacalhau importado", "quantidade": 0.500},
        {"ingrediente": "Azeite extra virgem", "quantidade": 0.100},
    ],
    "requisitos": [],
}


async def main() -> int:
    zerar()
    async with Client(URL) as cli:
        print(f"\n  {CINZA}roteiro contra {URL}{R}  {CINZA}(estado zerado){R}")

        # ------------------------------------------------------------------ #
        etapa("1. gravar um prato com ingredientes fora da despensa")
        r, recusa = await chamar(cli, "prato_salvar", **STROGONOFF)
        conferir("prato_salvar aceitou", recusa is None, recusa or "")
        if recusa:
            return 1
        pid = r["id"]
        fora = r.get("fora_da_despensa") or []
        # `fora_da_despensa` e "a despensa nao conhece este item" — diferente de
        # "conhece mas nao sabe o preco", que vira pendencia de estoque no gate.
        # Confundir os dois foi o que fez esta conferencia falhar sozinha.
        conferir("apontou o que a despensa nao conhece", "Champignon fatiado" in fora,
                 f"{len(fora)}: {fora}")
        conferir("gravou todos os ingredientes",
                 r.get("total_ingredientes") == len(STROGONOFF["ingredientes"]),
                 f"{r.get('total_ingredientes')} de {len(STROGONOFF['ingredientes'])}")
        conferir("veio o proximo passo", "proximo_passo" in r, r.get("proximo_passo", "")[:60])

        # ------------------------------------------------------------------ #
        etapa("2. a fonte e obrigatoria")
        _, recusa = await chamar(cli, "prato_salvar", nome="Sem Procedencia",
                                 fonte="receita da vovo", ingredientes=STROGONOFF["ingredientes"])
        conferir("recusou receita sem URL", recusa is not None and "fonte" in (recusa or "").lower())

        # ------------------------------------------------------------------ #
        etapa("3. precificar antes do gate e proibido")
        _, recusa = await chamar(cli, "cenarios", prato_id=pid)
        conferir("cenarios recusou prato nao aprovado", recusa is not None,
                 (recusa or "").split("\n")[0][:70])
        _, recusa = await chamar(cli, "cmv", prato_id=pid)
        conferir("cmv recusou tambem", recusa is not None)

        # ------------------------------------------------------------------ #
        etapa("4. o gate diz o que falta, com a chamada pronta")
        check, _ = await chamar(cli, "prato_checar", prato_id=pid)
        conferir("prato nao esta apto", check["apto"] is False,
                 f"{len(check['pendencias'])} pendencia(s)")
        tipos = {p["tipo"] for p in check["pendencias"]}
        conferir("cobrou preco do que falta", "estoque" in tipos, str(sorted(tipos)))
        conferir("cobrou o utensilio", "utensilio" in tipos)
        conferir("toda pendencia traz como_gravar",
                 all("como_gravar" in p for p in check["pendencias"]))

        # ------------------------------------------------------------------ #
        etapa("5. registrar o que ela paga, na embalagem que ela compra")
        for nome, preco, qtd, unidade in PRECOS:
            r, recusa = await chamar(cli, "ingrediente_preco", ingrediente=nome,
                                     preco_pago=preco, quantidade=qtd, unidade=unidade)
            if recusa:
                conferir(f"{nome}", False, recusa[:60])
                continue
            conferir(f"{nome:<18} R$ {preco:>6.2f} / {unidade:<14}",
                     r["custo_unitario"] is not None,
                     f"-> R$ {float(r['custo_unitario']):.2f}/{r['unidade_base']}")
        # A caixinha de 200 g precisa virar R$ 22,50/kg — nao R$ 4,50.
        creme, _ = await chamar(cli, "despensa", ingrediente="Creme de leite")
        unitario = float(creme[0]["custo_unitario"])
        conferir("caixinha de 200 g normalizada", abs(unitario - 22.50) < 0.01,
                 f"R$ {unitario:.2f}/kg")

        _, recusa = await chamar(cli, "ingrediente_preco", ingrediente="Coentro",
                                 preco_pago=3, quantidade=1, unidade="maco")
        conferir("recusou unidade que nao converte", recusa is not None)

        # ------------------------------------------------------------------ #
        etapa("6. responder o utensilio e reavaliar")
        await chamar(cli, "perfil_gravar", categoria="utensilio",
                     item="frigideira grande", resposta="tem")
        check, _ = await chamar(cli, "prato_checar", prato_id=pid)
        conferir("gate liberou", check["apto"] is True,
                 f"compras R$ {check['custo_compras']}")
        pend, _ = await chamar(cli, "perfil_pendente")
        conferir("agenda de elicitacao vazia", pend == [], str(pend)[:60])

        # ------------------------------------------------------------------ #
        etapa("7. preco: tres cenarios e o minimo que cobre a taxa")
        cen, recusa = await chamar(cli, "cenarios", prato_id=pid)
        conferir("cenarios liberou depois do gate", recusa is None)
        cmv, minimo = float(cen["cmv"]), float(cen["preco_minimo"])
        conferir("tres opcoes", len(cen["cenarios"]) == 3,
                 " · ".join(f"R$ {c['preco']}" for c in cen["cenarios"]))
        conferir("minimo cobre o CMV depois dos 10%", minimo * 0.90 >= cmv,
                 f"CMV {cmv:.2f} · minimo {minimo:.2f} · ela recebe {minimo * 0.90:.2f}")

        # ------------------------------------------------------------------ #
        etapa("8. aceitar abaixo do minimo e proibido")
        r, recusa = await chamar(cli, "prato_aceitar", prato_id=pid, preco=minimo - 1)
        negado = recusa is not None or (isinstance(r, dict) and r.get("aceito") is False)
        conferir("recusou preco de prejuizo", negado)

        r, recusa = await chamar(cli, "prato_aceitar", prato_id=pid,
                                 preco=float(cen["cenarios"][1]["preco"]))
        conferir("aceitou no preco escolhido", recusa is None and r.get("aceito") is not False,
                 f"R$ {cen['cenarios'][1]['preco']}")

        # ------------------------------------------------------------------ #
        etapa("9. o orcamento e do cardapio inteiro, nao do prato")
        r, _ = await chamar(cli, "prato_salvar", **BANQUETE)
        pid2 = r["id"]
        for nome, preco in (("Bacalhau importado", 189.90), ("Azeite extra virgem", 64.00)):
            await chamar(cli, "ingrediente_preco", ingrediente=nome,
                         preco_pago=preco, quantidade=1, unidade="kg")
        check, _ = await chamar(cli, "prato_checar", prato_id=pid2)
        tipos = {p["tipo"] for p in check["pendencias"]}
        conferir("gate travou por orcamento", "orcamento" in tipos,
                 f"compras R$ {check['custo_compras']} · restam R$ {check['orcamento_restante']}")
        _, recusa = await chamar(cli, "cenarios", prato_id=pid2)
        conferir("nao precificou o que nao cabe", recusa is not None)

        # ------------------------------------------------------------------ #
        etapa("10. o cardapio fechado")
        card, _ = await chamar(cli, "cardapio")
        conferir("cardapio devolve os aceitos", isinstance(card, list) and len(card) >= 1,
                 f"{len(card) if isinstance(card, list) else '?'} prato(s)")

    print(f"\n  {CINZA}{'-' * 68}{R}")
    if falhas:
        print(f"  {VERM}{len(falhas)} divergencia(s){R}: {'; '.join(falhas[:4])}\n")
        return 1
    print(f"  {VERDE}o backend se comporta como o desenho promete{R}\n")
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    raise SystemExit(asyncio.run(main()))
