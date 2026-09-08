"""O que o modelo mandou de verdade, virado teste.

Cada caso aqui saiu de um log do MCP em execucao real. Nenhum foi inventado:
sao as formas que os modelos acharam de escrever a mesma coisa quando
`prato_salvar` nao tinha schema publicado.

Importar `repo` nao abre conexao — o pool nasce com `open=False`.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from mcp_server.repo import normalizar_itens, normalizar_requisitos


def nomes(itens: list[dict]) -> list[str]:
    return [i["ingrediente"] for i in itens]


# --------------------------------------------------------------------------- #
# Ingredientes
# --------------------------------------------------------------------------- #
def test_formato_canonico():
    assert normalizar_itens([{"ingrediente": "Feijao preto", "quantidade": 0.12}]) == [
        {"ingrediente": "Feijao preto", "quantidade": Decimal("0.12")}
    ]


def test_chave_descritiva_copiada_do_prompt():
    """O caso real: o prompt dizia "nome e quantidade em kg por porcao".

    O modelo transformou a frase inteira em nome de chave. Comparar por
    igualdade reprovava os 10 ingredientes de uma vez.
    """
    bruto = [
        {"nome": "Feijao preto", "quantidade em kg por porcao": 0.120},
        {"nome": "Carne seca", "quantidade em kg por porcao": 0.080},
    ]
    assert normalizar_itens(bruto) == [
        {"ingrediente": "Feijao preto", "quantidade": Decimal("0.120")},
        {"ingrediente": "Carne seca", "quantidade": Decimal("0.080")},
    ]


@pytest.mark.parametrize(
    "bruto",
    [
        {"ingrediente": "Bacon", "quantidade": 0.03},
        {"nome": "Bacon", "qtd": 0.03},
        {"item": "Bacon", "quantity": 0.03},
        {"name": "Bacon", "amount": 0.03},
        {"Ingrediente": "Bacon", "Quantidade (kg)": 0.03},
        {"nome_do_ingrediente": "Bacon", "peso em kg": 0.03},
        {"ingrediente": "Bacon", "quantidade": "0,03"},  # virgula decimal
    ],
)
def test_grafias_aceitas(bruto):
    assert normalizar_itens([bruto]) == [
        {"ingrediente": "Bacon", "quantidade": Decimal("0.03")}
    ]


def test_chave_desconhecida_com_um_numero_so():
    """Ultima camada: sobrou uma chave e ela guarda um numero.

    Nao importa como ela se chama — em um objeto de ingrediente, o unico
    numero que resta e a quantidade.
    """
    assert normalizar_itens([{"nome": "Louro", "gramatura_por_prato": 0.001}]) == [
        {"ingrediente": "Louro", "quantidade": Decimal("0.001")}
    ]


def test_dois_numeros_desconhecidos_nao_adivinha():
    """Com dois candidatos nao da para escolher — melhor perguntar que errar."""
    with pytest.raises(ValueError, match="sem quantidade"):
        normalizar_itens([{"nome": "Louro", "aaa": 0.001, "bbb": 12}])


def test_erro_ensina_o_formato():
    """A mensagem e o unico canal que o agente tem para se corrigir sozinho."""
    with pytest.raises(ValueError) as exc:
        normalizar_itens([{"nome": "Feijao preto"}])
    texto = str(exc.value)
    assert '"ingrediente"' in texto and '"quantidade"' in texto
    assert "Feijao preto" in texto  # diz QUAL item falhou
    assert "['nome']" in texto  # e o que chegou no lugar


def test_quantidade_invalida():
    with pytest.raises(ValueError, match="maior que zero"):
        normalizar_itens([{"ingrediente": "Sal", "quantidade": 0}])
    with pytest.raises(ValueError, match="nao e numero"):
        normalizar_itens([{"ingrediente": "Sal", "quantidade": "a gosto"}])


def test_lista_vazia():
    with pytest.raises(ValueError, match="vazia"):
        normalizar_itens([])


def test_item_que_nao_e_objeto():
    with pytest.raises(ValueError, match="esperava objeto"):
        normalizar_itens(["Feijao preto"])


# --------------------------------------------------------------------------- #
# Requisitos
# --------------------------------------------------------------------------- #
def test_rotulo_grudado_no_valor():
    """O caso real: a pergunta saiu "A senhora tem utensilio panela de pressao?"."""
    assert normalizar_requisitos(
        [{"item": "utensilio panela de pressao", "categoria": "utensilio"}]
    ) == [{"categoria": "utensilio", "item": "panela de pressao"}]


@pytest.mark.parametrize(
    "bruto,esperado",
    [
        ({"item": "panela de pressao"}, "panela de pressao"),
        ({"item": "Utensilio: panela de pressao"}, "panela de pressao"),
        ({"item": "utensilios panela de pressao"}, "panela de pressao"),
        ({"item": "utensilio"}, "utensilio"),  # so o rotulo: preserva
        ({"item": "panela"}, "panela"),
    ],
)
def test_limpeza_do_rotulo(bruto, esperado):
    assert normalizar_requisitos([bruto])[0]["item"] == esperado


def test_categoria_invalida_vira_utensilio():
    assert normalizar_requisitos([{"item": "forno", "categoria": "equipamento"}]) == [
        {"categoria": "utensilio", "item": "forno"}
    ]


def test_categoria_com_acento_e_caixa():
    assert normalizar_requisitos([{"nome": "refogar", "tipo": "Técnica"}]) == [
        {"categoria": "tecnica", "item": "refogar"}
    ]


def test_requisito_sem_item_e_ignorado():
    assert normalizar_requisitos([{"categoria": "utensilio"}, "panela", None]) == []
