"""Cenario completo da feijoada, com os precos reais da planilha.

Nao sobe banco, nao chama modelo. Se estes testes passam, a matematica do
desafio esta certa — o resto e orquestracao.
"""

from decimal import Decimal

import pytest

from domain.precificacao import (
    ItemCusto,
    calcular_cmv,
    lucro,
    montar_cenarios,
    preco_minimo,
)
from domain.viabilidade import (
    FatoPerfil,
    ItemDespensa,
    ItemReceita,
    RequisitoPerfil,
    avaliar,
)

# custo_unitario tal como o banco calcula: preco_pago / (qtd_comprada * fator)
CUSTO = {
    "Feijao preto": Decimal("9.60"),
    "Carne de panela (acem)": Decimal("23.20"),
    "Bacon": Decimal("23.90"),
    "Arroz branco tipo 1": Decimal("4.98"),
    "Couve": Decimal("12.00"),
    "Farinha de mandioca": Decimal("8.60"),
    "Cebola": Decimal("4.00"),
    "Alho": Decimal("20.00"),
    "Oleo de soja": Decimal("9.00"),
    "Caldo de carne": Decimal("7.10"),
    "Sal": Decimal("1.85"),
    "Linguica calabresa": Decimal("22.00"),  # compra complementar
}

# uma marmita
RECEITA = [
    ("Feijao preto", "0.120"),
    ("Carne de panela (acem)", "0.100"),
    ("Bacon", "0.040"),
    ("Linguica calabresa", "0.050"),
    ("Arroz branco tipo 1", "0.100"),
    ("Couve", "0.040"),
    ("Farinha de mandioca", "0.030"),
    ("Cebola", "0.030"),
    ("Alho", "0.005"),
    ("Oleo de soja", "0.010"),
    ("Caldo de carne", "0.005"),
    ("Sal", "0.003"),
]


@pytest.fixture
def cmv():
    return calcular_cmv(
        ItemCusto(nome, Decimal(qtd), CUSTO[nome], "kg") for nome, qtd in RECEITA
    )


# --------------------------------------------------------------------------- #
# CMV
# --------------------------------------------------------------------------- #
def test_cmv_da_marmita(cmv):
    assert cmv.total == Decimal("7.12")
    assert cmv.completo
    assert len(cmv.linhas) == len(RECEITA)


def test_ingrediente_sem_custo_nao_entra_na_soma():
    """Nao inventamos valor: o item volta em `sem_custo` e o CMV fica incompleto."""
    r = calcular_cmv(
        [
            ItemCusto("Arroz branco tipo 1", Decimal("0.100"), Decimal("4.98"), "kg"),
            ItemCusto("Cobertura de chocolate", Decimal("0.080"), None, "un"),
        ]
    )
    assert r.total == Decimal("0.50")
    assert r.sem_custo == ("Cobertura de chocolate",)
    assert not r.completo


def test_alcaparra_normalizada_custa_metade():
    """O balde de 2 kg sai a R$ 41,00/kg, nao a R$ 82,00."""
    porcao = Decimal("0.020")
    assert calcular_cmv(
        [ItemCusto("Alcaparras", porcao, Decimal("41.00"), "kg")]
    ).total == Decimal("0.82")


# --------------------------------------------------------------------------- #
# Preco
# --------------------------------------------------------------------------- #
def test_preco_minimo_cobre_o_cmv(cmv):
    """7,12/0,90 = 7,9111... Cobrar 7,91 devolveria 7,119 — abaixo do CMV."""
    p = preco_minimo(cmv.total)
    assert p == Decimal("7.92")
    assert lucro(p, cmv.total) >= 0


@pytest.mark.parametrize("valor", ["0.01", "1.00", "7.12", "13.37", "99.99", "150.00"])
def test_preco_minimo_nunca_da_prejuizo(valor):
    c = Decimal(valor)
    assert lucro(preco_minimo(c), c) >= 0


def test_taxa_incide_sobre_a_venda_nao_sobre_o_lucro(cmv):
    """A armadilha: 7,90 parece cobrir um CMV de 7,12, mas nao cobre."""
    assert lucro(Decimal("7.90"), cmv.total) < 0


def test_tres_cenarios(cmv):
    a, b, c = montar_cenarios(
        cmv.total,
        [
            ("Volume", Decimal("19.90")),
            ("Equilibrio", Decimal("26.90")),
            ("Premium", Decimal("34.90")),
        ],
    )
    assert (b.taxa, b.recebe, b.lucro) == (
        Decimal("2.69"),
        Decimal("24.21"),
        Decimal("17.09"),
    )
    assert all(x.viavel for x in (a, b, c))
    assert a.lucro < b.lucro < c.lucro
    assert a.cmv_pct > b.cmv_pct > c.cmv_pct  # quanto mais caro, menor o % de comida


def test_identidade_cmv_taxa_margem(cmv):
    """CMV% + taxa 10% + margem% = 100%."""
    cen = montar_cenarios(cmv.total, [("x", Decimal("26.90"))])[0]
    assert cen.cmv_pct + Decimal("10") + cen.margem == pytest.approx(
        Decimal("100"), abs=Decimal("0.2")
    )


# --------------------------------------------------------------------------- #
# Gate
# --------------------------------------------------------------------------- #
def despensa_cheia():
    return [
        ItemDespensa(nome, "kg", Decimal("5"), CUSTO[nome]) for nome, _ in RECEITA
    ]


def receita_itens():
    return [ItemReceita(nome, Decimal(qtd), "kg") for nome, qtd in RECEITA]


def test_prato_viavel_quando_nada_falta():
    r = avaliar(receita_itens(), despensa_cheia())
    assert r.apto
    assert r.pendencias == ()


def test_utensilio_nao_perguntado_trava_o_prato():
    r = avaliar(
        receita_itens(),
        despensa_cheia(),
        requisitos=[RequisitoPerfil("utensilio", "panela de pressao")],
    )
    assert not r.apto
    assert r.perguntas() == ("A senhora tem panela de pressao?",)


def test_utensilio_confirmado_libera_o_prato():
    r = avaliar(
        receita_itens(),
        despensa_cheia(),
        requisitos=[RequisitoPerfil("utensilio", "panela de pressao")],
        perfil=[FatoPerfil("utensilio", "panela de pressao", "sim", "confirmado")],
    )
    assert r.apto


def test_utensilio_que_ela_nao_tem_e_impedimento():
    r = avaliar(
        receita_itens(),
        despensa_cheia(),
        requisitos=[RequisitoPerfil("utensilio", "forno")],
        perfil=[FatoPerfil("utensilio", "forno", "nao tem", "confirmado")],
    )
    assert not r.apto
    assert "nao tem" in r.pendencias[0].pergunta


def test_unidade_indeterminavel_vira_pergunta():
    """Receita pede grama, despensa so sabe 'un'. Nao chuta — pergunta."""
    r = avaliar(
        [ItemReceita("Cobertura de chocolate", Decimal("80"), "g")],
        [ItemDespensa("Cobertura de chocolate", "un", Decimal("1"), Decimal("79.90"))],
    )
    assert not r.apto
    assert r.pendencias[0].tipo == "unidade"


def test_compra_complementar_dentro_do_orcamento():
    r = avaliar(
        [ItemReceita("Bacon", Decimal("0.500"), "kg")],
        [ItemDespensa("Bacon", "kg", Decimal("0"), Decimal("23.90"))],
        orcamento_restante=Decimal("80.00"),
    )
    assert r.apto
    assert r.custo_compras == Decimal("11.950")
    assert r.compras[0].quantidade == Decimal("0.500")


def test_estouro_de_orcamento_trava_o_prato():
    # 0,5 kg de bacon e porcao de marmita. A versao anterior usava 2 kg, que o
    # gate hoje reprova como receita inteira — o dado de teste e que estava
    # irreal, nao a regra.
    r = avaliar(
        [ItemReceita("Bacon", Decimal("0.5"), "kg")],
        [ItemDespensa("Bacon", "kg", Decimal("0"), Decimal("23.90"))],
        orcamento_restante=Decimal("2.00"),
    )
    assert not r.apto
    assert r.pendencias[0].tipo == "orcamento"
    assert "faltam R$ 9.95" in r.pendencias[0].detalhe


def test_pendencias_acumulam_em_vez_de_parar_na_primeira():
    """A Dona Maria recebe TODAS as perguntas de uma vez, nao uma por rodada."""
    r = avaliar(
        [
            ItemReceita("Bacon", Decimal("0.5"), "kg"),
            ItemReceita("Cobertura de chocolate", Decimal("80"), "g"),
        ],
        [
            ItemDespensa("Bacon", "kg", Decimal("0"), Decimal("23.90")),
            ItemDespensa("Cobertura de chocolate", "un", Decimal("1"), Decimal("79.90")),
        ],
        requisitos=[RequisitoPerfil("utensilio", "forno")],
        orcamento_restante=Decimal("5.00"),
    )
    assert not r.apto
    assert {p.tipo for p in r.pendencias} == {"utensilio", "unidade", "orcamento"}
