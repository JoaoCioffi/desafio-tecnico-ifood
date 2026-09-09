"""A conta do delivery: CMV, taxa de 10% e os cenarios de margem.

Os numeros aqui saem de um prato que a Dona Maria de fato precificou na
conversa — frango ao molho de alcaparras — para que uma regressao apareca
como "o prato tal mudou de preco", e nao como aritmetica abstrata.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

import pytest

from domain.precificacao import (
    TAXA_PLATAFORMA,
    ItemCusto,
    calcular_cmv,
    lucro,
    montar_cenarios,
    preco_minimo,
    preco_por_alvo,
)

D = Decimal

# Custo por unidade base, ja normalizado — como sai da tabela `precos`.
PRATO = [
    ItemCusto("Peito de frango", D("0.180"), D("14.00"), "kg"),
    ItemCusto("Alcaparras", D("0.010"), D("41.00"), "kg"),
    ItemCusto("Azeite de oliva", D("0.010"), D("61.98"), "L"),
    ItemCusto("Batata", D("0.200"), D("6.00"), "kg"),
    ItemCusto("Arroz branco tipo 1", D("0.080"), D("4.98"), "kg"),
]


def test_cmv_do_prato():
    assert calcular_cmv(PRATO).total == D("5.15")


def test_arredondamento_nao_muda_este_prato():
    """Neste prato as duas formas coincidem — e vale registrar que coincidem.

    Um teste que so exercita o caso divergente deixa passar o oposto: uma
    mudanca que quebrasse o caso simples nao seria vista.
    """
    por_linha = sum(
        (i.quantidade * i.custo_unitario).quantize(D("0.01"), rounding=ROUND_HALF_UP)
        for i in PRATO
    )
    assert por_linha == calcular_cmv(PRATO).total == D("5.15")


def test_arredonda_uma_vez_so_no_fim():
    """Onde as duas formas divergem, e por que a regra importa.

    Oito ingredientes custando R$ 0,126 cada. Arredondados um a um viram
    R$ 0,13, e o total vira R$ 1,04 — tres centavos acima do R$ 1,01
    verdadeiro.

    O erro cresce com o numero de ingredientes, ou seja, fica maior
    justamente nas receitas em que e mais dificil conferir na mao. Por isso
    o dominio soma em precisao cheia e arredonda uma vez so, na saida.

    O ROUND_HALF_UP aqui e o mesmo do dominio, de proposito: o que se testa
    e QUANDO se arredonda, nao COMO. Com o default do Decimal (HALF_EVEN) os
    dois lados usariam regras diferentes e o teste mediria outra coisa.
    """
    itens = [ItemCusto(f"item {n}", D("0.126"), D("1.00"), "kg") for n in range(8)]

    por_linha = sum(
        (i.quantidade * i.custo_unitario).quantize(D("0.01"), rounding=ROUND_HALF_UP)
        for i in itens
    )
    assert por_linha == D("1.04")
    assert calcular_cmv(itens).total == D("1.01")


def test_ingrediente_sem_custo_nao_entra_e_e_denunciado():
    """Custo desconhecido nao vira zero: vira pendencia.

    Somado como zero, o CMV sairia menor que o real e o preco sairia barato
    demais — o erro mais caro possivel aqui, porque some do relatorio e
    aparece na conta bancaria dela.
    """
    resultado = calcular_cmv([*PRATO, ItemCusto("Cobertura de chocolate", D("0.080"), None)])
    assert resultado.total == D("5.15")
    assert resultado.completo is False
    assert resultado.sem_custo == ("Cobertura de chocolate",)


# --------------------------------------------------------------------------- #
# Taxa e preco minimo
# --------------------------------------------------------------------------- #
def test_taxa_incide_sobre_a_venda():
    """10% saem do PRECO, nao do lucro. Por isso divide, nao soma."""
    assert TAXA_PLATAFORMA == D("0.10")
    assert lucro(D("10.00"), D("4.00")) == D("5.00")  # 9,00 - 4,00


@pytest.mark.parametrize("cmv", ["5.15", "7.12", "2.76", "0.01", "123.45"])
def test_preco_minimo_sempre_cobre_o_custo(cmv):
    """A propriedade que o arredondamento para CIMA existe para garantir.

    Com CMV 7,12 a conta da 7,9111...; cobrar 7,91 devolveria 7,119 — um
    decimo de centavo A MENOS que o custo. Um minimo que nao cobre o custo
    nao e minimo, e arredondar para o mais proximo o quebraria em metade dos
    casos sem ninguem perceber.
    """
    assert lucro(preco_minimo(D(cmv)), D(cmv)) >= 0


def test_vender_perto_do_custo_da_prejuizo():
    """A armadilha que a Dona Maria cairia sozinha.

    R$ 6,50 com R$ 6,02 de ingrediente PARECE R$ 0,48 de lucro. Chegam
    R$ 5,85, entao da R$ 0,17 de prejuizo.
    """
    assert lucro(D("6.50"), D("6.02")) == D("-0.17")
    assert preco_minimo(D("6.02")) == D("6.69")


# --------------------------------------------------------------------------- #
# Cenarios
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("alvo", ["0.35", "0.30", "0.25"])
def test_as_tres_fatias_somam_cem(alvo):
    """comida% + taxa 10% + margem% = 100%.

    E a leitura que a Dona Maria entende sem formula, e ela precisa fechar
    exatamente — uma sobra de 0,1 ponto viraria "de cada R$ 10, sobram
    R$ 5,99" e destruiria a explicacao.
    """
    cmv = D("5.15")
    preco = preco_por_alvo(cmv, D(alvo))
    c = montar_cenarios(cmv, [("x", preco)])[0]
    assert c.cmv_pct + D("10.0") + c.margem == D("100.0")


def test_preco_por_alvo_respeita_a_fatia():
    """Comida em 30% do preco: o preco e a comida dividida por 0,30."""
    assert preco_por_alvo(D("5.16"), D("0.30")) == D("17.20")


def test_cenario_mais_caro_lucra_mais():
    cmv = D("5.15")
    precos = [(a, preco_por_alvo(cmv, D(a))) for a in ("0.35", "0.30", "0.25")]
    lucros = [c.lucro for c in montar_cenarios(cmv, precos)]
    assert lucros == sorted(lucros)
    assert all(c.viavel for c in montar_cenarios(cmv, precos))
