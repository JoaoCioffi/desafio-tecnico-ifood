"""Servidor MCP: expoe o dominio como ferramentas para o agente.

Fronteira de determinismo — todo calculo e toda gravacao passam por aqui.
O LLM nao soma e nao grava: ele chama uma ferramenta e recebe o resultado.
"""
