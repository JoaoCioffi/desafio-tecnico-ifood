"""
Sobe o Postgres, aplica o schema, carrega a despensa e SEGURA O TERMINAL.
O container vive enquanto o script viver: Ctrl+C derruba tudo.

    python .docker/run_services.py            # sobe, aplica schema, ETL e fica no ar
    python .docker/run_services.py --reset    # DESTROI o volume e recomeca do zero
    python .docker/run_services.py --status   # so mostra o estado atual e sai
    python .docker/run_services.py --down     # derruba um container ja rodando

Garantias:
  * o schema usa CREATE TABLE IF NOT EXISTS  -> nunca derruba tabela
  * o ETL usa ON CONFLICT DO NOTHING         -> nunca sobrescreve nem duplica
  * os dados vivem num volume nomeado do Docker, fora do repositorio,
    entao quem clonar recebe um banco vazio e o carrega a partir da planilha
"""

from __future__ import annotations

import argparse
import itertools
import subprocess
import sys
import threading
import time
from decimal import Decimal
from pathlib import Path

import openpyxl
import psycopg
from colorama import Fore, Style
from colorama import init as colorama_init

# --------------------------------------------------------------------------- #
# Caminhos
# --------------------------------------------------------------------------- #
AQUI = Path(__file__).resolve().parent
RAIZ = AQUI.parent
COMPOSE = AQUI / "docker-compose.yaml"
SCHEMA = AQUI / "db.sql"
PLANILHA = RAIZ / "shared" / "despensa_dona_maria.xlsx"

# A regra de conversao de unidade e dominio, nao ETL: o MCP tambem usa.
from domain.unidades import normalizar

from painel import acompanhar, saude as _saude_container

DSN = "host=localhost port=5432 dbname=sabor_da_maria user=admin password=admin"
VOLUME = "sabor-da-maria-pgdata"
CONTAINER = "sabor-da-maria-db"

ABA_DESPENSA = "Despensa"
ABA_PRECOS = "Precos"


# --------------------------------------------------------------------------- #
# Terminal
# --------------------------------------------------------------------------- #
D = Style.DIM
B = Style.BRIGHT
R = Style.RESET_ALL
VERDE, VERM, AMAR, AZUL, CIANO = Fore.GREEN, Fore.RED, Fore.YELLOW, Fore.BLUE, Fore.CYAN

OK, FALHA, AVISO, INFO, SETA = "✓", "✕", "▲", "•", "›"
BANCO = "🐘"
QUADROS = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


def titulo(txt: str) -> None:
    print(f"\n{B}{txt}{R}")


def item(txt: str, cor: str = "") -> None:
    print(f"  {D}{INFO}{R} {cor}{txt}{R}")


def aviso(txt: str) -> None:
    print(f"  {AMAR}{AVISO}{R} {AMAR}{txt}{R}")


def falhou(txt: str) -> None:
    print(f"  {VERM}{FALHA} {txt}{R}", file=sys.stderr)


class Etapa:
    """Spinner enquanto roda, marca de OK ou falha no fim."""

    def __init__(self, rotulo: str) -> None:
        self.rotulo = rotulo
        self.detalhe = ""
        self._parar = threading.Event()
        self._t: threading.Thread | None = None

    def __enter__(self) -> "Etapa":
        self._t = threading.Thread(target=self._girar, daemon=True)
        self._t.start()
        return self

    def _girar(self) -> None:
        for q in itertools.cycle(QUADROS):
            if self._parar.is_set():
                return
            print(f"\r  {CIANO}{q}{R} {self.rotulo}   ", end="", flush=True)
            time.sleep(0.08)

    def __exit__(self, tipo, valor, tb) -> bool:
        self._parar.set()
        if self._t:
            self._t.join(timeout=1)
        print("\r" + " " * (len(self.rotulo) + 30) + "\r", end="")
        if tipo is None:
            extra = f" {D}{self.detalhe}{R}" if self.detalhe else ""
            print(f"  {VERDE}{OK}{R} {self.rotulo}{extra}")
        else:
            print(f"  {VERM}{FALHA} {self.rotulo}{R}")
        return False


# --------------------------------------------------------------------------- #
# Docker
# --------------------------------------------------------------------------- #
def compose(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    """Roda docker compose capturando a saida — o spinner cuida do visual."""
    proc = subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if check and proc.returncode != 0:
        print()
        falhou((proc.stderr or proc.stdout or "").strip()[:800])
        raise SystemExit(proc.returncode)
    return proc


def subir_container() -> None:
    with Etapa("subindo o Postgres") as e:
        compose("up", "-d")
        e.detalhe = CONTAINER


def esperar_banco(timeout: int = 90) -> None:
    with Etapa("aguardando o banco aceitar conexao") as e:
        limite = time.monotonic() + timeout
        ultimo = ""
        while time.monotonic() < limite:
            try:
                with psycopg.connect(DSN, connect_timeout=3):
                    e.detalhe = "localhost:5432"
                    return
            except psycopg.OperationalError as exc:
                ultimo = (str(exc).strip().splitlines() or [""])[0]
                time.sleep(1.0)
        raise SystemExit(f"banco nao respondeu em {timeout}s — {ultimo}")


def saude() -> str:
    return _saude_container(CONTAINER)


# --------------------------------------------------------------------------- #
# Schema
# --------------------------------------------------------------------------- #
def aplicar_schema(conn: psycopg.Connection) -> None:
    if not SCHEMA.is_file():
        raise SystemExit(f"{SCHEMA} nao encontrado")

    with Etapa("aplicando o schema") as e:
        antes = tabelas(conn)
        conn.execute(SCHEMA.read_text(encoding="utf-8"))
        conn.commit()
        novas = sorted(tabelas(conn) - antes)
        e.detalhe = f"{len(novas)} criada(s)" if novas else "ja estava aplicado"

    for t in novas:
        item(f"criada  {B}{t}{R}", VERDE)
    for t in sorted(antes):
        item(f"mantida {t}", D)


def tabelas(conn: psycopg.Connection) -> set[str]:
    cur = conn.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema='public' AND table_type='BASE TABLE'"
    )
    return {l[0] for l in cur.fetchall()}


# --------------------------------------------------------------------------- #
# ETL
# --------------------------------------------------------------------------- #
def ler_planilha() -> list[dict]:
    """Junta as duas abas. Nao assume que casam 1:1 — verifica."""
    if not PLANILHA.is_file():
        raise SystemExit(f"planilha nao encontrada em {PLANILHA}")

    wb = openpyxl.load_workbook(PLANILHA, data_only=True)
    if faltando := {ABA_DESPENSA, ABA_PRECOS} - set(wb.sheetnames):
        raise SystemExit(f"abas ausentes: {sorted(faltando)}")

    despensa = {n: (q, u) for n, q, u in _linhas(wb[ABA_DESPENSA], 3)}
    precos = {n: (q, u, p) for n, q, u, p in _linhas(wb[ABA_PRECOS], 4)}

    if so_d := sorted(set(despensa) - set(precos)):
        raise SystemExit(f"so em {ABA_DESPENSA}: {so_d}")
    if so_p := sorted(set(precos) - set(despensa)):
        raise SystemExit(f"so em {ABA_PRECOS}: {so_p}")
    if div := [n for n in despensa if str(despensa[n][1]).strip() != str(precos[n][1]).strip()]:
        raise SystemExit(f"unidade diferente entre as abas: {div}")

    itens = []
    for nome, (estoque, unidade) in despensa.items():
        qtd_comprada, _, preco_pago = precos[nome]
        texto = str(unidade).strip()
        base, fator = normalizar(texto)
        itens.append(
            {
                "nome": nome,
                "unidade": texto,
                "estoque": _num(estoque),
                "qtd_comprada": _num(qtd_comprada),
                "preco_pago": _num(preco_pago),
                "unidade_base": base,
                "fator": fator,
            }
        )
    return itens


def _linhas(aba, n: int):
    for linha in aba.iter_rows(min_row=2, values_only=True):
        if linha and linha[0] and str(linha[0]).strip():
            yield (str(linha[0]).strip(), *linha[1:n])


def _num(v) -> Decimal | None:
    return None if v is None else Decimal(str(v).replace(",", "."))


def _tamanho(n: int) -> str:
    for unidade in ("B", "KB", "MB"):
        if n < 1024:
            return f"{n:.0f} {unidade}" if unidade == "B" else f"{n:.1f} {unidade}"
        n /= 1024
    return f"{n:.1f} GB"


def carregar(conn: psycopg.Connection, itens: list[dict]) -> None:
    antes = contar(conn)
    bytes_planilha = PLANILHA.stat().st_size if PLANILHA.is_file() else 0
    inicio = time.monotonic()

    convertidos = [i for i in itens if i["fator"] is not None and i["fator"] != 1]
    if convertidos:
        with Etapa("normalizando unidade de embalagem") as e:
            e.detalhe = f"{len(convertidos)} de {len(itens)} itens"

    with Etapa("gravando no Postgres") as e:
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO ingredientes
                       (nome, unidade, estoque, qtd_comprada, preco_pago,
                        unidade_base, fator)
                VALUES (%(nome)s, %(unidade)s, %(estoque)s, %(qtd_comprada)s,
                        %(preco_pago)s, %(unidade_base)s, %(fator)s)
                ON CONFLICT (nome) DO NOTHING
                """,
                itens,
            )
        conn.commit()
        inseridos = contar(conn) - antes
        e.detalhe = (
            f"{inseridos} novo(s), {len(itens) - inseridos} preservado(s)"
        )

    decorrido = time.monotonic() - inicio
    taxa = len(itens) / decorrido if decorrido > 0 else 0
    print(
        f"  {D}{SETA}{R} {len(itens)} ingredientes {D}·{R} "
        f"{_tamanho(bytes_planilha)} de planilha {D}·{R} "
        f"{VERDE}{decorrido:.2f}s{R} {D}({taxa:.0f} itens/s){R}"
    )

    if pendentes := [i for i in itens if i["fator"] is None]:
        print()
        for i in pendentes:
            aviso(f"{i['nome']} — unidade \"{i['unidade']}\" sem conversao, vira pergunta")


def contar(conn: psycopg.Connection) -> int:
    return conn.execute("SELECT count(*) FROM ingredientes").fetchone()[0]


# --------------------------------------------------------------------------- #
# Estado
# --------------------------------------------------------------------------- #
def estado(conn: psycopg.Connection) -> None:
    titulo("Banco")
    for t in ("ingredientes", "perfil", "pratos", "pratos_ingredientes"):
        try:
            n = conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
            cor = VERDE if n else D
            print(f"  {D}{SETA}{R} {t:<22}{cor}{n:>5}{R} {D}linha(s){R}")
        except psycopg.errors.UndefinedTable:
            conn.rollback()
            print(f"  {D}{SETA} {t:<22}    - nao existe{R}")

    try:
        o = conn.execute("SELECT * FROM vw_orcamento").fetchone()
        if o:
            print(
                f"  {D}{SETA}{R} {'orcamento':<22}{D}total{R} R$ {o[0]}  "
                f"{D}gasto{R} R$ {o[1]}  {VERDE}restante R$ {o[2]}{R}"
            )
    except psycopg.Error:
        conn.rollback()




# --------------------------------------------------------------------------- #
def resetar() -> None:
    titulo("Reset")
    print(
        f"  {AMAR}{AVISO}{R} apaga o volume {B}{VOLUME}{R} e "
        f"{B}todos os dados{R}.\n"
        f"    a despensa volta da planilha; perfil, pratos e cardapio somem."
    )
    if input(f"  {SETA} confirma? digite 'sim': ").strip().lower() != "sim":
        raise SystemExit("  cancelado")
    with Etapa("removendo o volume"):
        compose("down", "-v", check=False)
        subprocess.run(["docker", "volume", "rm", "-f", VOLUME], capture_output=True)


def encerrar() -> None:
    titulo("Encerrando")
    with Etapa("derrubando o container") as e:
        compose("down", check=False)
        e.detalhe = "dados preservados no volume"


def main() -> None:
    ap = argparse.ArgumentParser(description="Sobe o Postgres, aplica o schema e carrega a despensa.")
    ap.add_argument("--reset", action="store_true", help="DESTROI o volume e recomeca do zero")
    ap.add_argument("--status", action="store_true", help="so mostra o estado atual")
    ap.add_argument("--down", action="store_true", help="derruba um container ja rodando")
    args = ap.parse_args()

    print(f"\n{B}Sabor da Maria{R} {D}· banco de dados{R}")

    if args.down:
        encerrar()
        return

    if args.reset:
        resetar()

    if args.status:
        esperar_banco()
        with psycopg.connect(DSN) as conn:
            estado(conn)
        return

    titulo("Infra")
    subir_container()
    esperar_banco()

    with psycopg.connect(DSN) as conn:
        titulo("Schema")
        aplicar_schema(conn)
        titulo("ETL")
        carregar(conn, ler_planilha())

    acompanhar(DSN, CONTAINER)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    colorama_init(autoreset=False)

    servindo = not any(a in sys.argv for a in ("--status", "--down", "-h", "--help"))
    try:
        main()
    except KeyboardInterrupt:
        print()
        if servindo:
            encerrar()
    else:
        if servindo:
            encerrar()
