"""O gate — a garantia que o enunciado exige na secao 2.2.

    "O agente nao pode deixar ela comprar ingredientes e descobrir depois
     que nao consegue cozinhar."

O que estes testes protegem nao e um calculo, e uma PROPRIEDADE: nao existe
combinacao de entrada que aprove um prato com requisito nao confirmado. E a
diferenca entre um agente instruido a perguntar e um que nao consegue aceitar
sem ter perguntado.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from domain.viabilidade import (
    FatoPerfil,
    ItemDespensa,
    ItemReceita,
    RequisitoPerfil,
    achatar,
    avaliar,
    possui,
)

D = Decimal

DESPENSA = [
    ItemDespensa("Peito de frango", "kg", D("2.0"), D("14.00")),
    ItemDespensa("Batata", "kg", D("3.0"), D("6.00")),
    ItemDespensa("Queijo mussarela", "kg", D("0.5"), D("40.00")),
]

RECEITA = [
    ItemReceita("Peito de frango", D("0.5"), "kg"),
    ItemReceita("Batata", D("0.6"), "kg"),
]

FORNO = RequisitoPerfil("utensilio", "forno")
BECHAMEL = RequisitoPerfil("tecnica", "bechamel")


def test_bloqueia_sem_o_requisito_confirmado():
    v = avaliar(RECEITA, DESPENSA, [FORNO], perfil=[])
    assert v.apto is False
    assert any("forno" in p.pergunta.lower() for p in v.pendencias)


def test_libera_com_o_requisito_confirmado():
    perfil = [FatoPerfil("utensilio", "forno", "tem", "confirmado")]
    assert avaliar(RECEITA, DESPENSA, [FORNO], perfil).apto is True


def test_pendente_nao_conta_como_confirmado():
    """Perguntar nao e o mesmo que ter resposta.

    Uma linha 'pendente' significa que o agente ja perguntou e ela ainda nao
    respondeu. Tratar isso como confirmado transformaria a propria pergunta
    em autorizacao.
    """
    perfil = [FatoPerfil("utensilio", "forno", None, "pendente")]
    assert avaliar(RECEITA, DESPENSA, [FORNO], perfil).apto is False


def test_resposta_negativa_bloqueia():
    perfil = [FatoPerfil("utensilio", "forno", "nao tem", "confirmado")]
    assert avaliar(RECEITA, DESPENSA, [FORNO], perfil).apto is False


def test_acento_nao_pode_furar_o_gate():
    """O bug que ja aconteceu: 'panela de pressao' nunca casava com
    'panela de pressão'. Ela respondia, o dado entrava, e a pergunta voltava
    na rodada seguinte — para sempre."""
    requisito = RequisitoPerfil("utensilio", "panela de pressao")
    perfil = [FatoPerfil("utensilio", "Panela de Pressão", "tem", "confirmado")]
    assert achatar("Panela de Pressão") == "panela de pressao"
    assert avaliar(RECEITA, DESPENSA, [requisito], perfil).apto is True


def test_toda_pendencia_traz_a_pergunta_pronta():
    """O agente nao inventa a pergunta — ela vem escrita."""
    v = avaliar(RECEITA, DESPENSA, [FORNO, BECHAMEL], perfil=[])
    assert len(v.pendencias) == 2
    assert all(p.pergunta.strip().endswith("?") for p in v.pendencias)
    assert len(v.perguntas()) == 2


@pytest.mark.parametrize("resposta,esperado", [
    ("sim", True), ("tem", True), ("possui", True),
    ("nao", False), ("nao tem", False), ("nenhum", False),
    ("so 2 bocas", True),          # descreveu o que tem: e afirmativa
    ("uma panela pequena", True),
    (None, None), ("", None),      # nao sabemos ainda
])
def test_leitura_de_resposta_livre(resposta, esperado):
    assert possui(resposta) is esperado


# --------------------------------------------------------------------------- #
# Estoque e orcamento
# --------------------------------------------------------------------------- #
def test_falta_de_estoque_vira_compra_quando_cabe_no_orcamento():
    receita = [ItemReceita("Queijo mussarela", D("0.8"), "kg")]  # tem 0,5
    v = avaliar(receita, DESPENSA, [], [], orcamento_restante=D("80.00"))
    assert v.compras and v.compras[0].ingrediente == "Queijo mussarela"
    assert v.compras[0].quantidade == D("0.3")
    assert v.custo_compras == D("12.00")


def test_compra_que_nao_cabe_no_orcamento_bloqueia():
    receita = [ItemReceita("Queijo mussarela", D("0.8"), "kg")]
    v = avaliar(receita, DESPENSA, [], [], orcamento_restante=D("5.00"))
    assert v.apto is False
    assert any(p.tipo == "orcamento" for p in v.pendencias)


def test_ingrediente_fora_da_despensa_vira_pergunta_de_preco():
    """Sem preco nao da para saber se cabe no orcamento — entao pergunta."""
    receita = [ItemReceita("Creme de leite", D("0.2"), "L")]
    v = avaliar(receita, DESPENSA, [], [], orcamento_restante=D("80.00"))
    assert v.apto is False
    assert any("creme de leite" in p.pergunta.lower() for p in v.pendencias)


def test_receita_em_grama_de_item_contado_bloqueia():
    """A cobertura de chocolate: 'un' sem peso na planilha."""
    despensa = [*DESPENSA, ItemDespensa("Cobertura de chocolate", "un", D("1"), D("79.90"))]
    receita = [ItemReceita("Cobertura de chocolate", D("80"), "g")]
    v = avaliar(receita, despensa, [], [], orcamento_restante=D("80.00"))
    assert v.apto is False
    assert any(p.tipo in ("unidade", "estoque") for p in v.pendencias)


def test_sem_requisito_e_com_estoque_o_prato_passa():
    """O caminho feliz precisa existir: o gate barra o que falta, nao tudo."""
    v = avaliar(RECEITA, DESPENSA, [], [], orcamento_restante=D("80.00"))
    assert v.apto is True
    assert v.pendencias == ()
