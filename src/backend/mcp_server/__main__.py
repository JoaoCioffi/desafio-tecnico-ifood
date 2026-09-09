"""Sobe o servidor MCP.

    python -m mcp_server        # http://0.0.0.0:8000/mcp
"""

from __future__ import annotations

import logging
import os
import re

from . import repo
from .server import mcp

HOST = os.environ.get("MCP_HOST", "0.0.0.0")
PORTA = int(os.environ.get("MCP_PORT", "8000"))


# --------------------------------------------------------------------------- #
# Ruido de probe
# --------------------------------------------------------------------------- #
#  O transporte streamable-http so aceita POST com JSON-RPC, ou GET com
#  Accept: text/event-stream. O Hermes sonda o endpoint com HEAD e GET simples
#  antes de conectar, e leva 405 / 400 — comportamento correto dos dois lados.
#
#  Nao da para "consertar" isso sem violar a especificacao do MCP. O que da
#  para consertar e o log de acesso tratar probe conhecido como se fosse erro,
#  escondendo o que realmente importa.
#
#  So estas duas linhas sao silenciadas. Qualquer outro 4xx/5xx continua
#  aparecendo — inclusive um POST malformado, que ai seria problema de verdade.
# --------------------------------------------------------------------------- #
_PROBE = re.compile(r'"(HEAD /mcp[^"]*" 405|GET /mcp[^"]*" 400)')


class SemRuidoDeProbe(logging.Filter):
    def filter(self, registro: logging.LogRecord) -> bool:
        return not _PROBE.search(registro.getMessage())


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("uvicorn.access").addFilter(SemRuidoDeProbe())

    # O pool abre ANTES do servidor: se o banco nao responder, o container
    # morre agora com erro claro, em vez de subir saudavel e falhar na
    # primeira pergunta da Dona Maria.
    repo.abrir()
    logging.getLogger("mcp_server").info("banco conectado; servindo em %s:%s/mcp", HOST, PORTA)
    try:
        mcp.run(transport="http", host=HOST, port=PORTA)
    finally:
        repo.fechar()


if __name__ == "__main__":
    main()
