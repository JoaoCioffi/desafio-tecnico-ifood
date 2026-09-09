#!/usr/bin/env python3
"""Gate de viabilidade: bloqueia `aceitar_prato` enquanto houver pendencia.

    "O agente nao pode deixar ela comprar ingredientes e descobrir depois
     que nao consegue cozinhar."              -- enunciado, secao 2.2

Isso e uma GARANTIA, nao um pedido. Um requisito escrito em prompt e uma
sugestao: o modelo obedece quase sempre, e falha exatamente quando alguem
insiste — "confia em mim, pode aceitar". Nao ha como provar que um prompt
sempre funciona; ha como provar que um bloqueio determinista sempre funciona.

Como funciona: o Hermes chama este script antes de despachar a ferramenta,
manda a chamada em JSON pelo stdin e le a decisao do stdout. O modelo nao ve
este arquivo, nao pode desliga-lo e nao tem como ser convencido a pula-lo —
e outro processo, fora do alcance dele.

Roda DENTRO do container do Hermes, que nao tem psycopg. Por isso ele nao
consulta o banco: pergunta ao servidor MCP por HTTP e le a resposta. Assim
fica em stdlib pura, e a regra do gate continua existindo num lugar so.

Configurado no config.yaml com `fail_closed: true`, entao se este script
travar, estourar o tempo ou devolver lixo, a ferramenta e BLOQUEADA. Um
guarda que cai e libera a passagem nao e um guarda.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

GATE = os.environ.get("SABOR_GATE_URL", "http://mcp:8000/gate")
TIMEOUT = 8


def bloquear(motivo: str) -> None:
    """Recusa a chamada. As duas formas de dizer isso ao Hermes, juntas:
    o JSON no stdout e o codigo 2 — se uma nao for lida, a outra e."""
    print(json.dumps({"decision": "block", "reason": motivo}, ensure_ascii=False))
    sys.exit(2)


def liberar() -> None:
    print("{}")
    sys.exit(0)


def main() -> None:
    try:
        chamada = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        bloquear("gate: nao consegui ler a chamada")
        return

    # O matcher do config ja filtra, mas conferir aqui torna o script seguro
    # de reusar em outro matcher sem virar bloqueio universal por engano.
    if not chamada.get("tool_name", "").endswith("aceitar_prato"):
        liberar()
        return

    prato_id = (chamada.get("tool_input") or {}).get("prato_id")
    if prato_id is None:
        bloquear("gate: chamada sem prato_id — proponha o prato antes de aceitar")
        return

    try:
        with urllib.request.urlopen(f"{GATE}/{prato_id}", timeout=TIMEOUT) as resposta:
            veredito = json.loads(resposta.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError) as erro:
        bloquear(f"gate: nao consegui verificar a viabilidade ({type(erro).__name__}). "
                 f"Nao feche o prato sem essa checagem.")
        return

    if veredito.get("apto"):
        liberar()
        return

    perguntas = veredito.get("pendencias") or ["ha pendencia de viabilidade"]
    bloquear(
        f"Ainda nao da para fechar '{veredito.get('prato', prato_id)}' no cardapio. "
        f"Falta resolver com a Dona Maria:\n- " + "\n- ".join(perguntas)
    )


if __name__ == "__main__":
    main()
