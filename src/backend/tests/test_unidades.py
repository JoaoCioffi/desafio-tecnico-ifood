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

from domain.unidades import UnidadeIncompativel, consolidar, converter, normalizar

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


# --------------------------------------------------------------------------- #
# Ingrediente repetido na mesma receita
# --------------------------------------------------------------------------- #
def test_lasanha_com_parmesao_duas_vezes():
    """O caso que derrubou a ferramenta em producao, com os numeros dele.

    Uma lasanha de 21 ingredientes citou parmesao duas vezes — no molho e para
    gratinar, que e como a receita e escrita de verdade. A chave primaria de
    `pratos_ingredientes` e (prato_id, ingrediente), entao a segunda linha
    estourava:

        duplicate key value violates unique constraint
        "pratos_ingredientes_pkey"

    E o que se via no Telegram nao era um erro: era o agente tentando de novo e
    escrevendo OUTRA receita. Ferramenta que quebra faz o modelo improvisar, e
    improviso silencioso e pior que a falha.
    """
    itens = [
        {"ingrediente": "Massa de lasanha", "quantidade": Decimal("0.500"),
         "unidade_base": "kg", "comprar": True, "custo_compra": Decimal("8.90")},
        {"ingrediente": "Parmesao ralado", "quantidade": Decimal("0.200"),
         "unidade_base": "kg", "comprar": True, "custo_compra": Decimal("14.00")},
        {"ingrediente": "Parmesao ralado", "quantidade": Decimal("0.050"),
         "unidade_base": "kg", "comprar": True, "custo_compra": Decimal("3.50")},
    ]
    juntos, avisos = consolidar(itens)

    assert avisos == []
    assert len(juntos) == 2
    parmesao = next(i for i in juntos if i["ingrediente"] == "Parmesao ralado")
    assert parmesao["quantidade"] == Decimal("0.250")
    assert parmesao["custo_compra"] == Decimal("17.50")


def test_a_ordem_da_receita_e_preservada():
    """Consolidar nao pode reordenar: a receita continua legivel na ordem em
    que foi escrita, e o primeiro item de um prato costuma ser o principal."""
    itens = [{"ingrediente": n, "quantidade": Decimal("1"), "unidade_base": "kg",
              "comprar": False, "custo_compra": None}
             for n in ("Frango", "Arroz", "Frango", "Batata")]
    juntos, _ = consolidar(itens)
    assert [i["ingrediente"] for i in juntos] == ["Frango", "Arroz", "Batata"]


def test_unidade_incompativel_no_mesmo_nome_vira_aviso():
    """Somar 2 un com 0,05 kg daria um numero que PARECE certo.

    O aviso vai pelo mesmo canal do resto do que nao converte, entao o agente
    pergunta em vez de gravar um total inventado.
    """
    itens = [
        {"ingrediente": "Ovo", "quantidade": Decimal("2"), "unidade_base": "un",
         "comprar": False, "custo_compra": None},
        {"ingrediente": "Ovo", "quantidade": Decimal("0.05"), "unidade_base": "kg",
         "comprar": False, "custo_compra": None},
    ]
    juntos, avisos = consolidar(itens)
    assert len(juntos) == 1
    assert len(avisos) == 1 and "Ovo" in avisos[0]
