"""A armadilha central da planilha, item a item.

O enunciado define custo unitario como `preco total pago / quantidade
comprada`. Em 30 dos 37 itens isso basta. Em sete nao — porque a coluna
`Unidade` traz a EMBALAGEM, nao a medida:

    Alcaparras · 1 · "balde 2kg" · R$ 82,00

A divisao literal da R$ 82,00 por balde. Receita nenhuma pede um balde.

Os valores aqui sao os da planilha de verdade, nao inventados. Se alguem
mexer na conversao, o teste diz exatamente qual ingrediente quebrou e em
quanto — nao um "assert falhou" generico.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from domain.unidades import UnidadeIncompativel, converter, normalizar

# nome, unidade da planilha, qtd comprada, preco pago, custo/base esperado
ARMADILHAS = [
    ("Alcaparras", "balde 2kg", "1", "82.00", "41.00", "kg"),
    ("Chantilly", "un 500g", "1", "23.67", "47.34", "kg"),
    ("Leite ninho em po", "un 400g", "1", "15.18", "37.95", "kg"),
    ("Azeite de oliva", "un 500ml", "1", "30.99", "61.98", "L"),
    ("Aceto balsamico", "un 500ml", "1", "12.99", "25.98", "L"),
    ("Adocante liquido", "un 100ml", "1", "1.90", "19.00", "L"),
]

LIMPOS = [
    ("Arroz branco tipo 1", "kg", "5", "24.90", "4.98", "kg"),
    ("Peito de frango", "kg", "2", "28.00", "14.00", "kg"),
    ("Ovos", "un", "30", "24.00", "0.80", "un"),
    ("Leite integral", "L", "2", "10.00", "5.00", "L"),
]


def _custo_unitario(unidade: str, qtd: str, preco: str) -> tuple[Decimal, str]:
    """A conta que o banco faz: preco / (quantidade * fator)."""
    base, fator = normalizar(unidade)
    return Decimal(preco) / (Decimal(qtd) * fator), base


@pytest.mark.parametrize("nome,unidade,qtd,preco,esperado,base", ARMADILHAS)
def test_unidade_de_embalagem_normaliza(nome, unidade, qtd, preco, esperado, base):
    custo, obtida = _custo_unitario(unidade, qtd, preco)
    assert obtida == base, f"{nome}: unidade base errada"
    assert custo == Decimal(esperado), f"{nome}: custo por {base} errado"


@pytest.mark.parametrize("nome,unidade,qtd,preco,esperado,base", ARMADILHAS)
def test_ingenuo_diverge_do_correto(nome, unidade, qtd, preco, esperado, base):
    """A divisao literal e a normalizada NAO podem coincidir nestes itens.

    Se coincidirem, a normalizacao parou de fazer efeito — e o balde de
    alcaparra volta a valer R$ 82,00 numa porcao de 10 g.
    """
    ingenuo = Decimal(preco) / Decimal(qtd)
    correto, _ = _custo_unitario(unidade, qtd, preco)
    assert ingenuo != correto, f"{nome}: normalizacao nao mudou nada"


@pytest.mark.parametrize("nome,unidade,qtd,preco,esperado,base", LIMPOS)
def test_unidade_limpa_passa_direto(nome, unidade, qtd, preco, esperado, base):
    """Nos 30 itens limpos a divisao literal ja esta certa.

    Vale testar porque uma normalizacao zelosa demais poderia "corrigir" o
    que ja estava certo — 30 ovos a R$ 0,80 nao viram outra coisa.
    """
    custo, obtida = _custo_unitario(unidade, qtd, preco)
    assert obtida == base
    assert custo == Decimal(esperado)
    assert custo == Decimal(preco) / Decimal(qtd)


def test_alcaparra_numa_porcao_real():
    """10 g de alcaparra num prato: R$ 0,41, nao R$ 82,00.

    O numero da esquerda e o que a Dona Maria paga. O da direita e o que ela
    veria se o agente cobrasse o balde inteiro — e o prato seria descartado
    como inviavel sem ninguem entender por que.
    """
    custo_kg, _ = _custo_unitario("balde 2kg", "1", "82.00")
    assert Decimal("0.010") * custo_kg == Decimal("0.41000")


def test_cobertura_de_chocolate_nao_tem_resposta():
    """O item que NENHUMA conta resolve.

    "Cobertura de chocolate · 1 · un · R$ 79,90". A planilha nao diz quanto
    pesa a embalagem, entao "quanto custam 80 g" nao tem resposta derivavel.

    A ferramenta certa aqui e a pergunta, e por isso o dominio LEVANTA em vez
    de devolver um numero: um chute silencioso viraria preco errado.
    """
    base, fator = normalizar("un")
    assert (base, fator) == ("un", Decimal("1"))

    with pytest.raises(UnidadeIncompativel):
        converter(Decimal("80"), "g", "un")


def test_unidade_desconhecida_nao_inventa_fator():
    """Texto que nao permite extrair medida devolve None, nao um palpite."""
    assert normalizar("caixa").conhecida is False
    assert normalizar("").conhecida is False
