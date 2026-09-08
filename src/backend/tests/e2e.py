"""Verificador de ponta a ponta: confere o enunciado contra o BANCO.

    python src/backend/tests/e2e.py

Nao e teste unitario e o pytest nao o coleta (o nome nao casa com `test_*`).
Roda depois de uma conversa real com o agente e responde uma pergunta so:
**o que o enunciado pede aconteceu de verdade?**

Por que contra o banco e nao contra a resposta do agente: a prosa dele ja
disse "Ingredientes Salvos" com o banco vazio, e ja disse "salvei as tres
receitas no cardapio" com os tres pratos em `sugerido`. Auto-relato nao e
evidencia. Linha de tabela e.

Cada verificacao aponta a secao do enunciado que ela cobre. Sai 0 se tudo
passou, 1 se algo falhou — entao serve de portao em CI tambem.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from decimal import ROUND_CEILING, Decimal

import psycopg
from psycopg.rows import dict_row

DSN = os.environ.get(
    "SABOR_DSN",
    "host=localhost port=5432 dbname=sabor_da_maria user=admin password=admin",
)

ORCAMENTO = Decimal("80.00")
TAXA = Decimal("0.90")  # ela recebe 0,90 x preco

# Importado do dominio, nao copiado: gate e verificador tem que concordar sobre
# o que e uma porcao. Ja divergiram — o gate aceitava de 100 g a 1,2 kg e o
# verificador exigia de 300 g a 800 g, entao um prato de 106 g passava no gate
# e reprovava aqui, sem que nenhum dos dois estivesse "errado".
from domain.viabilidade import PORCAO_MAXIMA as PORCAO_MAX  # noqa: E402
from domain.viabilidade import PORCAO_MINIMA as PORCAO_MIN  # noqa: E402

VERDE, VERM, AMAR, CINZA, R = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"


@dataclass
class Achado:
    secao: str
    titulo: str
    ok: bool
    detalhe: str = ""
    critico: bool = True


# --------------------------------------------------------------------------- #
# 2.1 — receita real, pesquisada na internet
# --------------------------------------------------------------------------- #
def secao_2_1(cur) -> list[Achado]:
    pratos = cur.execute("SELECT id, nome, fonte, status FROM pratos ORDER BY id").fetchall()
    if not pratos:
        return [Achado("2.1", "existe prato pesquisado", False, "nenhum prato no banco")]

    sem_fonte = [p["nome"] for p in pratos if not (p["fonte"] or "").startswith(("http://", "https://"))]
    return [
        Achado("2.1", "existe prato pesquisado", True, f"{len(pratos)} prato(s)"),
        Achado(
            "2.1",
            "toda receita tem fonte real",
            not sem_fonte,
            "todas com URL" if not sem_fonte else f"sem URL: {', '.join(sem_fonte)}",
        ),
    ]


# --------------------------------------------------------------------------- #
# 2.2 — elicitacao de restricoes ("o coracao do desafio")
# --------------------------------------------------------------------------- #
def secao_2_2(cur) -> list[Achado]:
    perfil = cur.execute(
        "SELECT categoria, item, resposta FROM perfil WHERE resposta IS NOT NULL"
    ).fetchall()
    achados = [
        Achado(
            "2.2",
            "algo foi elicitado e gravado",
            bool(perfil),
            ", ".join(f"{p['item']}={p['resposta']}" for p in perfil[:3]) or "perfil vazio",
        )
    ]

    # Todo requisito que os pratos exigem tem resposta? Se nao tiver, o gate
    # esta travando e ela ainda nao foi perguntada.
    faltando = cur.execute(
        """
        SELECT DISTINCT r->>'item' AS item
          FROM pratos p, jsonb_array_elements(p.requisitos) r
         WHERE NOT EXISTS (
               SELECT 1 FROM perfil f
                WHERE unaccent_lower(f.item) = unaccent_lower(r->>'item')
                  AND f.resposta IS NOT NULL)
        """
    ).fetchall()
    # Requisito sem resposta nao e violacao: e o gate segurando o prato ate
    # ela responder — exatamente o que a secao 2.2 pede. Vira falha so se o
    # prato ja tiver sido aceito mesmo assim.
    vazou = cur.execute(
        """
        SELECT p.nome FROM pratos p, jsonb_array_elements(p.requisitos) r
         WHERE p.status = 'aceito'
           AND NOT EXISTS (SELECT 1 FROM perfil f
                            WHERE unaccent_lower(f.item) = unaccent_lower(r->>'item')
                              AND f.resposta IS NOT NULL)
        """
    ).fetchall()
    achados.append(
        Achado(
            "2.2",
            "nenhum prato aceito com requisito em aberto",
            not vazou,
            "gate seguro" if not vazou else f"VAZOU: {[v['nome'] for v in vazou]}",
        )
    )
    achados.append(
        Achado(
            "2.2",
            "requisitos pendentes viraram pergunta",
            True,
            "nenhum em aberto" if not faltando
            else f"aguardando resposta dela: {[f['item'] for f in faltando]}",
            critico=False,
        )
    )

    # A ordem importa: checar viabilidade DEPOIS de precificar nao protege
    # ninguem. O evento guarda quem veio antes.
    primeiro_check = cur.execute(
        "SELECT min(id) AS i FROM evento WHERE ferramenta = 'prato_checar' AND fase = 'fim'"
    ).fetchone()["i"]
    primeiro_preco = cur.execute(
        "SELECT min(id) AS i FROM evento WHERE ferramenta IN ('cmv','cenarios') AND fase = 'fim'"
    ).fetchone()["i"]
    if primeiro_preco is None:
        ordem, detalhe = True, "nao houve precificacao ainda"
    elif primeiro_check is None:
        ordem, detalhe = False, "precificou sem nunca ter chamado prato_checar"
    else:
        ordem = primeiro_check < primeiro_preco
        detalhe = f"checar #{primeiro_check} {'<' if ordem else '>'} precificar #{primeiro_preco}"
    achados.append(Achado("2.2", "gate rodou ANTES de precificar", ordem, detalhe))
    return achados


# --------------------------------------------------------------------------- #
# 2.3 — casar receita com a despensa, faltantes e orcamento
# --------------------------------------------------------------------------- #
def _precificou(cur) -> bool:
    """Houve conta de preco? So depois disso falta de dado vira violacao."""
    return bool(
        cur.execute(
            "SELECT 1 FROM evento WHERE ferramenta IN ('cmv','cenarios') AND fase = 'fim' LIMIT 1"
        ).fetchone()
        or cur.execute("SELECT 1 FROM pratos WHERE status = 'aceito' LIMIT 1").fetchone()
    )


def secao_2_3(cur) -> list[Achado]:
    precificou = _precificou(cur)
    sem_custo = cur.execute(
        """
        SELECT p.nome AS prato, pi.ingrediente
          FROM pratos_ingredientes pi
          JOIN pratos p ON p.id = pi.prato_id
          LEFT JOIN ingredientes i ON i.nome = pi.ingrediente
         WHERE i.custo_unitario IS NULL
        """
    ).fetchall()
    itens = [s["ingrediente"] for s in sem_custo]
    achados = [
        Achado(
            "2.3",
            "nenhum CMV calculado sobre custo faltante",
            not (sem_custo and precificou),
            "nenhum ingrediente sem custo"
            if not sem_custo
            else (
                f"CMV SUBESTIMADO por: {itens}"
                if precificou
                # Perguntar o preco e parar e o comportamento certo do 2.3.
                # Reprovar isso puniria o agente por fazer o que foi pedido.
                else f"{len(itens)} aguardando o preco dela: {itens}"
            ),
            critico=bool(precificou),
        )
    ]

    orc = cur.execute("SELECT orcamento_total, gasto, restante FROM vw_orcamento").fetchone()
    achados.append(
        Achado(
            "2.3",
            "compras cabem nos R$ 80",
            Decimal(str(orc["restante"])) >= 0,
            f"gasto R$ {orc['gasto']} de R$ {orc['orcamento_total']}, "
            f"restam R$ {orc['restante']}",
        )
    )

    faltantes = cur.execute(
        "SELECT count(*) AS n FROM pratos_ingredientes WHERE comprar"
    ).fetchone()["n"]
    achados.append(
        Achado("2.3", "faltantes identificados", True, f"{faltantes} item(ns) a comprar", critico=False)
    )
    return achados


# --------------------------------------------------------------------------- #
# 2.4 — CMV, taxa de 10% e a decisao dela
# --------------------------------------------------------------------------- #
def secao_2_4(cur) -> list[Achado]:
    achados: list[Achado] = []

    cenarios = cur.execute(
        "SELECT count(*) AS n FROM evento WHERE ferramenta = 'cenarios' AND fase = 'fim'"
    ).fetchone()["n"]
    aceitou = bool(cur.execute("SELECT 1 FROM pratos WHERE status = 'aceito' LIMIT 1").fetchone())
    achados.append(
        Achado(
            "2.4",
            "nenhum prato fechado sem os tres cenarios",
            cenarios > 0 or not aceitou,
            f"{cenarios} chamada(s)" if cenarios else "conversa ainda nao chegou no preco",
            critico=aceitou,
        )
    )

    aceitos = cur.execute(
        "SELECT nome, cmv, preco FROM pratos WHERE status = 'aceito'"
    ).fetchall()
    if not aceitos:
        achados.append(
            Achado(
                "2.4",
                "prato fechado no cardapio",
                True,
                "nenhum aceito ainda — ela ainda nao escolheu, e a decisao e dela",
                critico=False,
            )
        )
        return achados

    ruins = []
    for p in aceitos:
        if p["cmv"] is None or p["preco"] is None:
            ruins.append(f"{p['nome']}: sem cmv/preco gravado")
            continue
        minimo = (Decimal(str(p["cmv"])) / TAXA).quantize(Decimal("0.01"), ROUND_CEILING)
        if Decimal(str(p["preco"])) < minimo:
            ruins.append(f"{p['nome']}: R$ {p['preco']} < minimo R$ {minimo}")
    achados.append(
        Achado(
            "2.4",
            "preco cobre CMV depois da taxa de 10%",
            not ruins,
            f"{len(aceitos)} aceito(s), todos acima do minimo" if not ruins else "; ".join(ruins),
        )
    )
    return achados


# --------------------------------------------------------------------------- #
# Integridade — as regras que este projeto se impos
# --------------------------------------------------------------------------- #
def integridade(cur) -> list[Achado]:
    porcoes = cur.execute(
        """
        SELECT p.nome, sum(pi.quantidade) AS kg
          FROM pratos p JOIN pratos_ingredientes pi ON pi.prato_id = p.id
         GROUP BY p.nome
        """
    ).fetchall()
    fora = [
        f"{p['nome']}: {Decimal(str(p['kg'])) * 1000:.0f} g"
        for p in porcoes
        if not (PORCAO_MIN <= Decimal(str(p["kg"])) <= PORCAO_MAX)
    ]
    achados = [
        Achado(
            "int",
            f"porcao entre {PORCAO_MIN * 1000:.0f} g e {PORCAO_MAX * 1000:.0f} g",
            not fora,
            f"{len(porcoes)} prato(s) na faixa" if not fora else f"fora da faixa: {fora}",
        )
    ]

    total = cur.execute("SELECT count(*) AS n FROM evento").fetchone()["n"]
    achados.append(
        Achado("int", "chamadas registradas em evento", total > 0, f"{total} evento(s)")
    )

    # Recusa e erro sao coisas diferentes e a versao anterior confundia as duas:
    # contava `erro` e rotulava "recusa", entao um roteiro com quatro recusas
    # aparecia como "0 recusas".
    #
    #   recusa   invariante do servidor disparando. E EVIDENCIA DE SAUDE.
    #   erro     defeito. So preocupa se nada deu certo depois.
    recusas = cur.execute(
        "SELECT ferramenta, count(*) AS n FROM evento WHERE fase = 'recusa'"
        " GROUP BY 1 ORDER BY 2 DESC"
    ).fetchall()
    achados.append(
        Achado(
            "int",
            "invariantes do servidor dispararam",
            True,
            ", ".join(f"{r['ferramenta']} {r['n']}x" for r in recusas) or "nenhuma recusa",
            critico=False,
        )
    )

    orfaos = cur.execute(
        """
        SELECT e.ferramenta, e.mensagem
          FROM evento e
         WHERE e.fase IN ('erro', 'cancelado')
           AND NOT EXISTS (SELECT 1 FROM evento f
                            WHERE f.ferramenta = e.ferramenta
                              AND f.fase = 'fim' AND f.id > e.id)
         ORDER BY e.id DESC LIMIT 3
        """
    ).fetchall()
    recuperados = cur.execute(
        """
        SELECT count(*) AS n FROM evento e
         WHERE e.fase IN ('erro', 'cancelado')
           AND EXISTS (SELECT 1 FROM evento f
                        WHERE f.ferramenta = e.ferramenta
                          AND f.fase = 'fim' AND f.id > e.id)
        """
    ).fetchone()["n"]
    achados.append(
        Achado(
            "int",
            "nenhum erro ficou sem correcao",
            not orfaos,
            (f"{recuperados} corrigido(s) na sequencia" if recuperados else "nenhum erro")
            if not orfaos
            else "; ".join(f"{e['ferramenta']}: {e['mensagem'][:55]}" for e in orfaos),
        )
    )
    return achados


# --------------------------------------------------------------------------- #
def progresso(cur) -> str:
    """Ate onde o atendimento chegou. Uma tabela toda verde num banco vazio
    nao prova nada — esta linha diz de que ponto o verde esta falando."""
    etapas = [
        ("receita pesquisada", "SELECT count(*) FROM pratos"),
        ("elicitacao gravada", "SELECT count(*) FROM perfil WHERE resposta IS NOT NULL"),
        ("precos informados", "SELECT count(*) FROM evento WHERE ferramenta='ingrediente_preco' AND fase='fim'"),
        ("gate rodado", "SELECT count(*) FROM evento WHERE ferramenta='prato_checar' AND fase='fim'"),
        ("preco apresentado", "SELECT count(*) FROM evento WHERE ferramenta='cenarios' AND fase='fim'"),
        ("prato no cardapio", "SELECT count(*) FROM pratos WHERE status='aceito'"),
    ]
    marcas = []
    for rotulo, sql in etapas:
        n = list(cur.execute(sql).fetchone().values())[0]
        marcas.append(f"{VERDE if n else CINZA}{'✓' if n else '·'} {rotulo}{R}")
    return "   ".join(marcas)


def main() -> int:
    try:
        conn = psycopg.connect(DSN, connect_timeout=5, row_factory=dict_row)
    except psycopg.OperationalError as e:
        print(f"\n  {VERM}banco fora do ar{R}  {CINZA}{str(e).splitlines()[0]}{R}\n")
        return 2

    with conn, conn.cursor() as cur:
        achados = secao_2_1(cur) + secao_2_2(cur) + secao_2_3(cur) + secao_2_4(cur) + integridade(cur)

    with psycopg.connect(DSN, connect_timeout=5, row_factory=dict_row) as c2, c2.cursor() as cur2:
        print(f"\n  {CINZA}ate onde chegou{R}  {progresso(cur2)}")
    print(f"\n  {'§':<5}{'verificacao':<42}resultado")
    print(f"  {CINZA}{'-' * 76}{R}")
    for a in achados:
        if a.ok:
            marca, cor = "✓", VERDE
        else:
            marca, cor = ("✕", VERM) if a.critico else ("▲", AMAR)
        print(f"  {CINZA}{a.secao:<5}{R}{a.titulo:<42}{cor}{marca}{R} {CINZA}{a.detalhe}{R}")

    falhas = [a for a in achados if not a.ok and a.critico]
    avisos = [a for a in achados if not a.ok and not a.critico]
    print(f"  {CINZA}{'-' * 76}{R}")
    if falhas:
        print(f"  {VERM}{len(falhas)} falha(s) critica(s){R}"
              + (f"  {AMAR}{len(avisos)} aviso(s){R}" if avisos else ""))
    else:
        print(f"  {VERDE}tudo o que o enunciado pede aconteceu{R}"
              + (f"  {AMAR}({len(avisos)} aviso){R}" if avisos else ""))
    print()
    return 1 if falhas else 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    raise SystemExit(main())
