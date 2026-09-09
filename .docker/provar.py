"""
Prova o esquema e o ciclo de venda contra um Postgres DESCARTAVEL.

    python .docker/provar.py

Nao toca no banco do projeto: sobe um container proprio, aplica o `db.sql` num
banco vazio, roda as duas provas e apaga tudo no fim — inclusive se falhar.

Sao duas porque respondem a perguntas diferentes:

    db.prova.sql    o esquema sozinho. Constraints, indices e views. Roda em
                    psql, sem Python no meio, entao o que ele afirma vale sobre
                    o BANCO e nao sobre a nossa camada de acesso.

    db.integra.py   o repo.py falando com esse esquema, inclusive com threads
                    concorrentes. Roda dentro da imagem do MCP porque e la que
                    o psycopg_pool existe — o venv do host nao o tem, e testar
                    no runtime de verdade vale mais que testar num parecido.

O que estas provas cobrem e o que o `pytest` NAO cobre: aquele roda o dominio
puro em 0,2s, sem Docker e sem rede, e essa propriedade vale manter. Aqui
mora o que so o banco pode responder.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

AQUI = Path(__file__).resolve().parent
RAIZ = AQUI.parent

CONTAINER = "sabor-prova-descartavel"
REDE = "sabor-prova-rede"
IMAGEM_DB = "postgres:alpine"
IMAGEM_MCP = "sabor-da-maria-mcp:local"
BANCO = "prova"

VERDE, VERM, AMAR, D, R = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"


def _imprimivel(texto: str) -> bool:
    """Mesma checagem do runner: o console do Windows nem sempre e UTF-8, e
    descobrir isso no meio da prova troca o resultado por um traceback."""
    try:
        texto.encode(sys.stdout.encoding or "utf-8")
        return True
    except (UnicodeEncodeError, LookupError):
        return False


OK, FALHA = ("✓", "✗") if _imprimivel("✓✗") else ("+", "x")
TRACO = "─" if _imprimivel("─") else "-"


def secao(titulo: str) -> str:
    return f"  {D}{TRACO * 2} {titulo} {TRACO * (54 - len(titulo))}{R}"


def rodar(*cmd: str, checar: bool = True) -> subprocess.CompletedProcess:
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    if checar and proc.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd[:3])}…\n{(proc.stderr or proc.stdout)[:400]}")
    return proc


def limpar() -> None:
    rodar("docker", "rm", "-f", CONTAINER, checar=False)
    rodar("docker", "network", "rm", REDE, checar=False)


def main() -> int:
    print(f"\n  {D}prova em banco descartavel: o banco do projeto nao e tocado{R}\n")
    limpar()  # sobra de uma execucao interrompida

    rodar("docker", "network", "create", REDE, checar=False)
    rodar("docker", "run", "-d", "--name", CONTAINER, "--network", REDE,
          "-e", "POSTGRES_PASSWORD=x", "-e", f"POSTGRES_DB={BANCO}", IMAGEM_DB)

    # pg_isready em vez de sleep fixo: a primeira subida do postgres cria o
    # cluster e demora bem mais que as seguintes.
    for _ in range(60):
        if rodar("docker", "exec", CONTAINER, "pg_isready", "-U", "postgres",
                 "-d", BANCO, checar=False).returncode == 0:
            break
        time.sleep(0.5)
    else:
        raise RuntimeError("o postgres descartavel nao ficou pronto")
    print(f"  {VERDE}{OK}{R} postgres de pe")

    for arquivo in ("db.sql", "db.prova.sql"):
        rodar("docker", "cp", str(AQUI / arquivo), f"{CONTAINER}:/tmp/{arquivo}")

    rodar("docker", "exec", CONTAINER, "psql", "-v", "ON_ERROR_STOP=1",
          "-U", "postgres", "-d", BANCO, "-q", "-f", "/tmp/db.sql")
    print(f"  {VERDE}{OK}{R} db.sql aplicado num banco vazio\n")

    print(secao("esquema"))
    esquema = rodar("docker", "exec", CONTAINER, "psql", "-U", "postgres",
                    "-d", BANCO, "-q", "-f", "/tmp/db.prova.sql", checar=False)
    # As duas saidas juntas, e nao `stdout or stderr`: o psql manda resultado de
    # consulta para stdout e RAISE para stderr, e o teste do indice unico se
    # anuncia justamente por RAISE. Olhando so stdout, aquele teste nunca
    # poderia falhar — e teste que nao pode falhar nao esta testando.
    esquema_texto = esquema.stdout + esquema.stderr
    print(esquema_texto)

    print(secao("repo + concorrencia"))
    integra = rodar(
        "docker", "run", "--rm", "--network", REDE,
        "-e", f"DB_HOST={CONTAINER}", "-e", "DB_PORT=5432",
        "-e", f"DB_NAME={BANCO}", "-e", "DB_USER=postgres", "-e", "DB_PASSWORD=x",
        "-v", f"{RAIZ / 'src' / 'backend'}:/app:ro",
        "-v", f"{AQUI / 'db.integra.py'}:/tmp/db.integra.py:ro",
        IMAGEM_MCP, "python", "/tmp/db.integra.py", checar=False,
    )
    print(integra.stdout or integra.stderr)

    # O psql nao devolve codigo de erro para um `SELECT` que imprime 'FALHOU':
    # a consulta funcionou, o resultado e que nao era o esperado. Entao o
    # veredito do esquema precisa dos dois sinais — o texto E o codigo de saida,
    # que o ON_ERROR_STOP do db.prova.sql levanta quando algo aborta de fato.
    ruim = ("FALHOU" in esquema_texto
            or esquema.returncode != 0
            or integra.returncode != 0)
    if ruim:
        print(f"  {VERM}{FALHA} alguma prova falhou{R}\n")
        return 1
    print(f"  {VERDE}{OK} tudo verde{R}\n")
    return 0


if __name__ == "__main__":
    try:
        codigo = main()
    except KeyboardInterrupt:
        codigo = 130
    except RuntimeError as erro:
        print(f"\n  {VERM}{FALHA}{R} {erro}\n")
        codigo = 1
    finally:
        # Sempre. Container descartavel que sobrevive a uma falha deixa de ser
        # descartavel e vira lixo com nome bonito.
        limpar()
    sys.exit(codigo)
