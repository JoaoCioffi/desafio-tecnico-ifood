"""Painel ao vivo do host, do container e do banco.

Separado do run_services porque e apresentacao, nao ETL.

Coleta cara roda em thread: `docker stats` leva ~1s e os contadores de GPU
do Windows ~2,8s. Se fossem chamados no loop de desenho o painel travaria.
Aqui eles rodam a parte e o desenho le sempre o ultimo valor conhecido —
por isso a tela atualiza a cada 0,4s mesmo com coletores lentos.
"""

from __future__ import annotations

import json
import math
import re
import subprocess
import sys
import threading
import time

import psutil
import psycopg
from colorama import Fore, Style

D, B, R = Style.DIM, Style.BRIGHT, Style.RESET_ALL
VERDE, VERM, AMAR, AZUL = Fore.GREEN, Fore.RED, Fore.YELLOW, Fore.BLUE

BANCO = "🐘"
GLIFOS = "▁▃▅▇"

# Apaga do cursor ate o fim da linha. Sem isso, uma linha curta deixa
# resto da linha longa anterior na tela ("...1 conexao(oes)" virando
# "...nexao(oes)" atras de um valor curto).
FIM = "\033[K"

ESPEC = f"    {D}{{:<7}}{R}{{:<38}}{D}{{}}{R}{FIM}"
USO = f"  {D}{{}}{R} {D}{{:<7}}{R}{{}}  {{:>18}}"


# --------------------------------------------------------------------------- #
def sinal(pct: float) -> str:
    """Barras de sinal, estilo wifi: 4 niveis de 25%.

    Sempre 4 caracteres visiveis — as apagadas ficam em cinza, entao a
    coluna seguinte nunca desalinha.
    """
    pct = max(0.0, min(100.0, pct or 0.0))
    n = 0 if pct <= 0 else min(4, math.ceil(pct / 25))
    cor = VERDE if n <= 2 else (AMAR if n == 3 else VERM)
    return f"{cor}{GLIFOS[:n]}{R}{D}{GLIFOS[n:]}{R}"


_ULTIMA: dict[str, float] = {}


def tendencia(chave: str, valor: float, limiar: float = 0.8) -> str:
    """Seta comparando com a leitura anterior desta mesma metrica.

    O limiar evita a seta piscar entre 11.0% e 11.2% — variacao menor que
    ele conta como estavel. A cor fica de fora de proposito: a severidade
    ja e das barras, a seta so diz a direcao.
    """
    anterior = _ULTIMA.get(chave)
    _ULTIMA[chave] = valor
    if anterior is None or abs(valor - anterior) < limiar:
        return "→"
    return "↗" if valor > anterior else "↘"


def marca(pct: float, rotulo: str = "overclock") -> str:
    """Etiqueta para leitura acima de 100% — nao e erro, e agregacao.

    O rotulo muda porque a causa muda:

        cpu   `overclock`      soma dos nucleos passando do nominal
        gpu   `multi-engine`   o Windows soma 3D + copy + decode + video,
                               entao 130% e mais de um engine ocupado ao
                               mesmo tempo, nao clock acima do nominal
    """
    return f"  {VERDE}{rotulo}{R}" if pct > 100 else ""


def _pct(txt) -> float:
    try:
        return float(str(txt).replace("%", "").strip())
    except (ValueError, AttributeError):
        return 0.0


def _mem_curta(txt: str) -> str:
    """'41.23MiB / 63.93GiB' -> '41.2 MiB'."""
    bruto = (txt or "").split("/")[0].strip()
    m = re.match(r"([\d.]+)\s*([KMGT]?i?B)", bruto)
    return f"{float(m.group(1)):.1f} {m.group(2)}" if m else (bruto or "—")


def _powershell(script: str, timeout: int = 20) -> str:
    r = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    return r.stdout.strip()


# --------------------------------------------------------------------------- #
# Coleta
# --------------------------------------------------------------------------- #
class Coletor(threading.Thread):
    """Roda uma coleta lenta em loop e guarda o ultimo resultado."""

    def __init__(self, alvo, intervalo: float) -> None:
        super().__init__(daemon=True)
        self._alvo, self._intervalo = alvo, intervalo
        self._parar = threading.Event()
        self.valor: dict = {}

    def run(self) -> None:
        while not self._parar.is_set():
            try:
                self.valor = self._alvo() or {}
            except Exception:
                self.valor = {}
            self._parar.wait(self._intervalo)

    def parar(self) -> None:
        self._parar.set()


def hardware() -> dict:
    """Ficha do host. Lida uma vez — nao muda."""
    ficha = {
        "cpu": "CPU desconhecida",
        "cores": psutil.cpu_count(logical=False) or 0,
        "threads": psutil.cpu_count(logical=True) or 0,
        "ram_gb": psutil.virtual_memory().total / 2**30,
        "gpu": "",
        "vram_gb": 0.0,
    }
    if sys.platform != "win32":
        return ficha

    classe = r"HKLM:\SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
    script = (
        r"(Get-ItemProperty 'HKLM:\HARDWARE\DESCRIPTION\System\CentralProcessor\0')"
        ".ProcessorNameString; "
        "(Get-CimInstance Win32_VideoController | Select-Object -First 1).Name; "
        f"(Get-ChildItem '{classe}' | ForEach-Object {{ (Get-ItemProperty $_.PSPath)."
        "'HardwareInformation.qwMemorySize' } | Where-Object { $_ } | "
        "Sort-Object -Descending | Select-Object -First 1)"
    )
    try:
        linhas = _powershell(script).splitlines()
        if len(linhas) > 0 and linhas[0].strip():
            ficha["cpu"] = " ".join(linhas[0].split())
        if len(linhas) > 1 and linhas[1].strip():
            ficha["gpu"] = linhas[1].strip()
        if len(linhas) > 2 and linhas[2].strip().isdigit():
            ficha["vram_gb"] = int(linhas[2].strip()) / 2**30
    except Exception:
        pass
    return ficha


def coletar_gpu() -> dict:
    """Uso e VRAM via contadores de performance do Windows.

    Nao depende de nvidia-smi — funciona em GPU AMD tambem.
    """
    if sys.platform != "win32":
        return {}
    script = (
        r"$u=(Get-Counter '\GPU Engine(*)\Utilization Percentage' -EA SilentlyContinue)"
        ".CounterSamples | Measure-Object CookedValue -Sum; "
        r"$m=(Get-Counter '\GPU Adapter Memory(*)\Dedicated Usage' -EA SilentlyContinue)"
        ".CounterSamples | Measure-Object CookedValue -Sum; "
        '"$($u.Sum)"; "$($m.Sum)"'
    )
    linhas = _powershell(script).replace(",", ".").splitlines()
    try:
        return {"uso": float(linhas[0]), "vram_gb": float(linhas[1]) / 2**30}
    except (IndexError, ValueError):
        return {}


def coletar_docker(container: str) -> dict:
    r = subprocess.run(
        ["docker", "stats", "--no-stream", "--format", "{{json .}}", container],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0 or not r.stdout.strip():
        return {}
    try:
        return json.loads(r.stdout.strip().splitlines()[0])
    except json.JSONDecodeError:
        return {}


def saude(container: str) -> str:
    r = subprocess.run(
        ["docker", "inspect", "--format", "{{.State.Health.Status}}", container],
        capture_output=True,
        text=True,
    )
    return r.stdout.strip() if r.returncode == 0 else "ausente"


TABELAS = ("ingredientes", "perfil", "pratos", "pratos_ingredientes", "evento")

_SQL_METRICAS = (
    "SELECT "
    + ", ".join(f"(SELECT count(*) FROM {t}) AS {t}" for t in TABELAS)
    + ", (SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()) AS conexoes"
    ", pg_size_pretty(pg_database_size(current_database())) AS tamanho"
    ", (SELECT gasto FROM vw_orcamento) AS gasto"
    ", (SELECT restante FROM vw_orcamento) AS restante"
)


def coletar_banco(dsn: str) -> dict:
    """Estado do banco em uma unica ida.

    Uma query so, com subselects. As tabelas tem dezenas de linhas — o custo
    e desprezivel — mas roda em thread propria a 1,5s porque contagem de linha
    nao muda 2,5x por segundo, e o desenho nao pode esperar o banco.
    """
    with psycopg.connect(dsn, connect_timeout=3) as conn:
        linha = conn.execute(_SQL_METRICAS).fetchone()
    chaves = list(TABELAS) + ["conexoes", "tamanho", "gasto", "restante"]
    return dict(zip(chaves, linha))


# --------------------------------------------------------------------------- #
def _uso_host() -> dict:
    """CPU e RAM do host. Dict vazio se o psutil falhar."""
    try:
        mem = psutil.virtual_memory()
        return {"cpu": psutil.cpu_percent(interval=None), "mem": mem}
    except Exception:
        return {}

def acompanhar(dsn: str, container: str) -> None:
    """Segura o terminal com o painel. Sai quando o container cai ou no Ctrl+C.

    O painel e montado linha a linha conforme o que a maquina deixa ler.
    Se nada de hardware estiver disponivel, sobra so o bloco do banco —
    o script continua util em qualquer host.
    """
    hw = hardware()

    print(
        f"\n  {AZUL}{BANCO}{R}  {B}postgres no ar{R}  {D}localhost:5432{R}"
        f"   {D}admin / admin / sabor_da_maria{R}"
    )
    print(f"  {D}Ctrl+C para parar e derrubar o container{R}\n")

    # Ficha do host: estatica, impressa uma vez so.
    especs = []
    if hw["cpu"] != "CPU desconhecida":
        especs.append(("cpu", hw["cpu"], f"{hw['cores']}C/{hw['threads']}T"))
    if hw["gpu"]:
        especs.append(("gpu", hw["gpu"], f"{hw['vram_gb']:.0f} GB" if hw["vram_gb"] else ""))
    if hw["ram_gb"]:
        especs.append(("ram", "", f"{hw['ram_gb']:.1f} GB"))
    if especs:
        print(f"  {B}HOST{R}{FIM}")
        for rotulo, nome, detalhe in especs:
            print(ESPEC.format(rotulo, nome, detalhe))
        print(FIM)

    gpu = Coletor(coletar_gpu, 3.0)
    docker = Coletor(lambda: coletar_docker(container), 2.0)
    banco = Coletor(lambda: coletar_banco(dsn), 1.5)
    for c in (gpu, docker, banco):
        c.start()

    _uso_host()  # descarta a leitura de calibracao do psutil
    anterior = 0

    try:
        while True:
            estado = saude(container)
            if estado in ("unhealthy", "ausente"):
                print(f"\n  {VERM}✖ container ficou '{estado}'{R}")
                return

            host, g, dk, bd = _uso_host(), gpu.valor, docker.valor, banco.valor
            painel: list[str] = []

            # --- consumo global da maquina -----------------------------
            uso: list[str] = []
            if host:
                mem = host["mem"]
                uso.append(
                    USO.format(tendencia("cpu", host["cpu"]), "cpu", sinal(host["cpu"]), f"{host['cpu']:.1f} %")
                    + marca(host["cpu"])
                    + FIM
                )
                uso.append(
                    USO.format(
                        tendencia("ram", mem.percent),
                        "ram",
                        sinal(mem.percent),
                        f"{mem.used / 2**30:.1f} / {mem.total / 2**30:.1f} GB",
                    )
                    + FIM
                )
            if (uso_gpu := g.get("uso")) is not None:
                uso.append(
                    USO.format(tendencia("gpu", uso_gpu), "gpu", sinal(uso_gpu), f"{uso_gpu:.1f} %")
                    + marca(uso_gpu, "multi-engine")
                    + FIM
                )
                if (vram := g.get("vram_gb")) is not None and hw["vram_gb"]:
                    uso.append(
                        USO.format(
                            tendencia("vram", vram / hw["vram_gb"] * 100, 0.3),
                            "vram",
                            sinal(vram / hw["vram_gb"] * 100),
                            f"{vram:.1f} / {hw['vram_gb']:.1f} GB",
                        )
                        + FIM
                    )
            if uso:
                painel.append(f"  {B}USO{R}  {D}global da maquina{R}{FIM}")
                painel.extend(uso)
                painel.append(FIM)

            # --- banco: contagem ao vivo -------------------------------
            cor = VERDE if estado == "healthy" else AMAR
            painel.append(f"  {B}BANCO{R}  {D}{container}{R}  {cor}{estado}{R}{FIM}")

            if bd:
                tabelas = "   ".join(
                    f"{D}{t}{R} {VERDE if bd[t] else D}{bd[t]}{R}" for t in TABELAS
                )
                painel.append(f"  {D}{'linhas':<10}{R}{tabelas}{FIM}")
                painel.append(
                    f"  {D}{'orcamento':<10}{R}{D}gasto{R} R$ {bd['gasto']}"
                    f"   {VERDE}restante R$ {bd['restante']}{R}{FIM}"
                )
                rodape = [str(bd["tamanho"]), f"{bd['conexoes']} conexao(oes)"]
            else:
                rodape = ["banco indisponivel"]
            if dk:
                rodape.append(_mem_curta(dk.get("MemUsage")))
            painel.append(f"  {D}{'':<10}{'  ·  '.join(rodape)}{R}{FIM}")

            if anterior:
                print(f"\033[{anterior}A", end="")
            print("\n".join(painel))
            anterior = len(painel)
            time.sleep(0.4)
    finally:
        for c in (gpu, docker, banco):
            c.parar()
