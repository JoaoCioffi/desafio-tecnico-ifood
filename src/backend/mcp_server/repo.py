"""Acesso ao Postgres.

Unica camada que fala SQL. O `domain/` continua puro; o `server.py` orquestra.

Toda escrita que muda o cardapio passa por transacao: ou grava tudo, ou nada.
"""

from __future__ import annotations

import json
import os
import unicodedata
from decimal import Decimal
from typing import Any

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

DSN = os.environ.get(
    "SABOR_DSN",
    "host=localhost port=5432 dbname=sabor_da_maria user=admin password=admin",
)

_pool = ConnectionPool(DSN, min_size=1, max_size=4, open=False, kwargs={"row_factory": dict_row})


def abrir() -> None:
    _pool.open()
    _pool.wait(timeout=15)


def fechar() -> None:
    _pool.close()


def _ler(sql: str, params: tuple = ()) -> list[dict]:
    with _pool.connection() as conn:
        return conn.execute(sql, params).fetchall()


def _um(sql: str, params: tuple = ()) -> dict | None:
    linhas = _ler(sql, params)
    return linhas[0] if linhas else None


# --------------------------------------------------------------------------- #
# Ingredientes
# --------------------------------------------------------------------------- #
_ORDENS = {
    "nome": "nome ASC",
    "custo": "custo_unitario DESC NULLS LAST",
    "disponivel": "disponivel DESC",
}


def despensa(
    ingrediente: str | None = None,
    ordenar_por: str = "nome",
    limite: int | None = None,
) -> list[dict]:
    """Despensa com o comprometido pelos pratos aceitos ja descontado.

    Filtro e ordenacao acontecem no Postgres, nao no modelo. "Qual o
    ingrediente mais caro" e um ORDER BY, nao uma varredura de 37 linhas
    dentro do contexto do LLM — devolver a despensa inteira para responder
    sobre um item custa token e tempo em toda rodada.
    """
    onde, params = "", []
    if ingrediente:
        onde = "WHERE unaccent_lower(nome) LIKE unaccent_lower(%s)"
        params.append(f"%{ingrediente}%")

    ordem = _ORDENS.get(ordenar_por, _ORDENS["nome"])
    lim = ""
    if limite and limite > 0:
        lim = "LIMIT %s"
        params.append(int(limite))

    return _ler(
        f"""
        SELECT nome, unidade_base, unidade_planilha,
               estoque_total, comprometido, disponivel, custo_unitario
          FROM vw_estoque
          {onde}
         ORDER BY {ordem}
          {lim}
        """,
        tuple(params),
    )


def _chave(texto: str) -> str:
    """Normaliza para comparar nome de ingrediente: sem acento, minusculo."""
    sem_acento = "".join(
        c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn"
    )
    return " ".join(sem_acento.lower().split())


def casar_ingrediente(texto: str) -> str | None:
    """Casa o nome que a receita usa com o SKU da despensa.

    Comparacao normalizada (sem acento, minusculo) e, em seguida, por
    conteudo — 'cebola roxa' casa com 'Cebola'. Devolve None quando nao ha
    correspondencia: o chamador transforma isso em pergunta, nao em chute.
    """
    alvo = _chave(texto)
    nomes = [linha["nome"] for linha in _ler("SELECT nome FROM ingredientes")]

    for nome in nomes:
        if _chave(nome) == alvo:
            return nome
    candidatos = [n for n in nomes if _chave(n) in alvo or alvo in _chave(n)]
    return min(candidatos, key=len) if candidatos else None


# --------------------------------------------------------------------------- #
# Perfil
# --------------------------------------------------------------------------- #
def perfil() -> list[dict]:
    return _ler(
        "SELECT categoria, item, resposta, status, atualizado_em"
        "  FROM perfil ORDER BY categoria, item"
    )


def perfil_gravar(categoria: str, item: str, resposta: str) -> dict:
    with _pool.connection() as conn:
        linha = conn.execute(
            """
            INSERT INTO perfil (categoria, item, resposta, status)
            VALUES (%s, %s, %s, 'confirmado')
            ON CONFLICT (categoria, item) DO UPDATE
               SET resposta = EXCLUDED.resposta,
                   status = 'confirmado',
                   atualizado_em = now()
            RETURNING categoria, item, resposta, status
            """,
            (categoria, item, resposta),
        ).fetchone()
    return linha


def perfil_pendente() -> list[dict]:
    """O que os pratos exigem e o perfil ainda nao confirma."""
    return _ler(
        """
        SELECT DISTINCT
               req->>'categoria' AS categoria,
               req->>'item'      AS item,
               p.nome            AS prato
          FROM pratos p
          CROSS JOIN LATERAL jsonb_array_elements(p.requisitos) AS req
          LEFT JOIN perfil f
                 ON f.categoria = req->>'categoria'
                AND f.item      = req->>'item'
                AND f.status    = 'confirmado'
         WHERE p.status <> 'recusado'
           AND f.item IS NULL
         ORDER BY categoria, item
        """
    )


# --------------------------------------------------------------------------- #
# Pratos
# --------------------------------------------------------------------------- #
# Um LLM nao acerta o nome da chave toda vez. Em vez de estourar KeyError e
# travar o fluxo, aceitamos as grafias plausiveis e so reclamamos quando nao
# ha nenhuma — com uma mensagem que diz COMO corrigir.
_ALIAS_NOME = ("ingrediente", "nome", "name", "item", "ingredient")
_ALIAS_QTD = ("quantidade", "qtd", "quantity", "amount", "qty")
_ALIAS_CATEG = ("categoria", "category", "tipo", "type")


def _campo(dado: dict, aliases: tuple[str, ...]):
    for chave in aliases:
        if chave in dado and dado[chave] not in (None, ""):
            return dado[chave]
    return None


def normalizar_itens(ingredientes: list[dict]) -> list[dict]:
    """Aceita as grafias comuns de chave e devolve o formato canonico.

    Erro aqui e devolvido como ValueError com instrucao — o agente consegue
    corrigir e tentar de novo. Um KeyError cru so trava o fluxo.
    """
    limpos, ruins = [], []
    for i, bruto in enumerate(ingredientes or []):
        if not isinstance(bruto, dict):
            ruins.append(f"item {i}: esperava objeto, veio {type(bruto).__name__}")
            continue
        nome = _campo(bruto, _ALIAS_NOME)
        qtd = _campo(bruto, _ALIAS_QTD)
        if nome is None:
            ruins.append(f"item {i}: sem nome do ingrediente (chaves: {list(bruto)})")
            continue
        if qtd is None:
            ruins.append(f"item {i} ({nome}): sem quantidade (chaves: {list(bruto)})")
            continue
        try:
            valor = Decimal(str(qtd))
        except Exception:
            ruins.append(f"item {i} ({nome}): quantidade {qtd!r} nao e numero")
            continue
        if valor <= 0:
            ruins.append(f"item {i} ({nome}): quantidade precisa ser maior que zero")
            continue
        limpos.append({"ingrediente": str(nome).strip(), "quantidade": valor})

    if ruins:
        raise ValueError(
            "Nao consegui ler a lista de ingredientes. Formato esperado:\n"
            '  [{"ingrediente": "Feijao preto", "quantidade": 0.120}]\n'
            "quantidade em kg (ou L) por porcao, numero decimal.\n"
            "Problemas encontrados:\n  - " + "\n  - ".join(ruins)
        )
    if not limpos:
        raise ValueError("A lista de ingredientes veio vazia — a receita precisa de itens.")
    return limpos


def normalizar_requisitos(requisitos: list[dict]) -> list[dict]:
    """Mesma tolerancia para os requisitos do prato."""
    limpos = []
    for bruto in requisitos or []:
        if not isinstance(bruto, dict):
            continue
        item = _campo(bruto, _ALIAS_NOME)
        if item is None:
            continue
        categoria = _campo(bruto, _ALIAS_CATEG) or "utensilio"
        if categoria not in ("utensilio", "tecnica", "restricao"):
            categoria = "utensilio"
        limpos.append({"categoria": categoria, "item": str(item).strip()})
    return limpos


def prato_salvar(
    nome: str,
    fonte: str | None,
    ingredientes: list[dict],
    requisitos: list[dict],
) -> dict:
    """Grava um prato candidato e seus ingredientes. Idempotente pelo nome."""
    ingredientes = normalizar_itens(ingredientes)
    requisitos = normalizar_requisitos(requisitos)
    with _pool.connection() as conn, conn.transaction():
        prato = conn.execute(
            """
            INSERT INTO pratos (nome, fonte, requisitos)
            VALUES (%s, %s, %s::jsonb)
            ON CONFLICT (nome) DO UPDATE
               SET fonte = EXCLUDED.fonte,
                   requisitos = EXCLUDED.requisitos
            RETURNING id, nome, status
            """,
            (nome, fonte, json.dumps(requisitos)),
        ).fetchone()

        conn.execute("DELETE FROM pratos_ingredientes WHERE prato_id = %s", (prato["id"],))
        novos: list[str] = []
        for ing in ingredientes:
            nome = ing["ingrediente"]
            sku = casar_ingrediente(nome)

            if sku is None:
                # A receita pede algo que a despensa nao tem — carne seca, paio,
                # linguica. Descartar em silencio esconderia o custo do prato:
                # o CMV sairia baixo e o gate aprovaria um prato impossivel.
                #
                # Um ingrediente faltante E um item de despensa: estoque zero e
                # custo desconhecido. Registrado assim, todo o resto ja funciona
                # sem codigo novo — calcular_cmv devolve em `sem_custo`,
                # viabilidade gera "quanto custa?", e prato_aceitar recusa ate
                # a Dona Maria informar o preco.
                conn.execute(
                    """
                    INSERT INTO ingredientes (nome, unidade, estoque, unidade_base, fator)
                    VALUES (%s, 'kg', 0, 'kg', 1)
                    ON CONFLICT (nome) DO NOTHING
                    """,
                    (nome,),
                )
                sku = nome
                novos.append(nome)

            conn.execute(
                """
                INSERT INTO pratos_ingredientes (prato_id, ingrediente, quantidade, comprar)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (prato_id, ingrediente) DO UPDATE
                   SET quantidade = EXCLUDED.quantidade,
                       comprar    = EXCLUDED.comprar
                """,
                (prato["id"], sku, ing["quantidade"], sku in novos),
            )

    prato["fora_da_despensa"] = novos
    prato["total_ingredientes"] = len(ingredientes)
    return prato


def prato(prato_id: int) -> dict | None:
    return _um(
        "SELECT id, nome, fonte, status, cmv, preco, requisitos FROM pratos WHERE id = %s",
        (prato_id,),
    )


def prato_ingredientes(prato_id: int) -> list[dict]:
    return _ler(
        """
        SELECT pi.ingrediente, pi.quantidade, pi.comprar,
               v.unidade_base, v.disponivel, v.custo_unitario
          FROM pratos_ingredientes pi
          JOIN vw_estoque v ON v.nome = pi.ingrediente
         WHERE pi.prato_id = %s
         ORDER BY pi.ingrediente
        """,
        (prato_id,),
    )


def orcamento() -> dict:
    return _um("SELECT orcamento_total, gasto, restante FROM vw_orcamento") or {
        "orcamento_total": Decimal("80.00"),
        "gasto": Decimal("0"),
        "restante": Decimal("80.00"),
    }


def prato_aceitar(prato_id: int, preco: Decimal, cmv: Decimal, compras: list[dict]) -> dict:
    """Fecha o prato. Chamado APENAS depois de o gate aprovar.

    Transacao: marca aceito, grava CMV e preco, e sinaliza as compras
    complementares. Se qualquer passo falhar, nada e gravado.
    """
    with _pool.connection() as conn, conn.transaction():
        for compra in compras:
            conn.execute(
                "UPDATE pratos_ingredientes SET comprar = TRUE"
                " WHERE prato_id = %s AND ingrediente = %s",
                (prato_id, compra["ingrediente"]),
            )
        linha = conn.execute(
            """
            UPDATE pratos
               SET status = 'aceito', preco = %s, cmv = %s
             WHERE id = %s
            RETURNING id, nome, status, cmv, preco
            """,
            (preco, cmv, prato_id),
        ).fetchone()
    return linha


def cardapio() -> list[dict]:
    return _ler(
        "SELECT id, nome, status, cmv, preco FROM pratos"
        " WHERE status = 'aceito' ORDER BY nome"
    )


def json_seguro(valor: Any) -> Any:
    """Decimal -> float na fronteira do JSON. Só aqui, nunca no meio da conta."""
    if isinstance(valor, Decimal):
        return float(valor)
    if isinstance(valor, dict):
        return {k: json_seguro(v) for k, v in valor.items()}
    if isinstance(valor, (list, tuple)):
        return [json_seguro(v) for v in valor]
    return valor
