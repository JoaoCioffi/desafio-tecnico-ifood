# Servidor MCP do Sabor da Maria.
#
# Imagem so com as dependencias. O CODIGO entra por bind mount no compose —
# um `docker compose restart mcp` recarrega o servidor sem rebuild, e o
# avaliador que clonar o repo recebe o codigo do proprio clone, nao uma copia
# congelada dentro de uma camada.

FROM python:3.12-slim

# --no-install-recommends e a limpeza do apt no mesmo RUN: camada intermediaria
# nao e apagada por um RUN posterior, so cresce a imagem.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
        "fastmcp>=4.0" \
        "psycopg[binary,pool]>=3.2"

# `src/backend` e a raiz de import: os pacotes ficam importaveis como `domain`
# e `mcp_server`, sem prefixo — o mesmo arranjo do pyproject do projeto.
ENV PYTHONPATH=/app \
    PYTHONUNBUFFERED=1 \
    MCP_HOST=0.0.0.0 \
    MCP_PORT=8000

WORKDIR /app
EXPOSE 8000

CMD ["python", "-m", "mcp_server"]
