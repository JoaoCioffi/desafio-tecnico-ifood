"""Painel ao vivo do Sabor da Maria.

Quatro blocos, do mais estatico ao mais volatil:

    HOST              a maquina — lida uma vez, nao muda
    INFRA             o Postgres: imagem, versao, saude, ETL, tabelas, orcamento
    MCP               nossas ferramentas: quais existem, qual esta rodando agora
    HARDWARE STATS    consumo global em tempo real

Tudo vem de HTTP ou do Postgres. NENHUM bloco le arquivo — a versao anterior
lia o config do Hermes do disco e devolvia FileNotFoundError para um arquivo
que existia e que o mesmo interpretador lia sem problema fora do painel. Nunca
descobrimos por que; descobrimos que nao precisava. O MCP responde pelo
proprio /health, e o que o agente esta fazendo agora sai da tabela `evento`,
que ele escreve a cada chamada.

O modelo roda na OpenAI, entao nao ha o que monitorar de servidor de
inferencia aqui — o consumo de GPU segue no HARDWARE STATS porque a maquina
continua sendo a maquina.

O painel e a UNICA saida do programa. Nao ha log de boot rolando antes dele:
o progresso da subida aparece dentro do INFRA e some quando termina. Essa
decisao existe por um motivo — versoes anteriores imprimiam o log e depois
tentavam apaga-lo com escapes, e a aritmetica de "subir N linhas" errava toda
vez que uma linha embrulhava ou a janela rolava. Sem log, nao ha o que apagar.

Do terminal so se usa `\033[nA` (sobe) e `\033[K` (apaga a linha). `\033[J`,
`\033[H` e `\033[s`/`\033[u` foram descartados: a camada de traducao do
Windows nem sempre os aplica, e um escape ignorado deixa lixo na tela.

Coleta cara roda em thread — `docker inspect` leva ~1s e os contadores de GPU
do Windows ~2,8s. O desenho le sempre o ultimo valor conhecido, entao a tela
atualiza a cada 0,2s mesmo com coletores lentos.
"""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from datetime import datetime, timezone

import psutil
import psycopg
from colorama import Fore, Style

D, B, R = Style.DIM, Style.BRIGHT, Style.RESET_ALL
VERDE, VERM, AMAR = Fore.GREEN, Fore.RED, Fore.YELLOW

GLIFOS = "▁▃▅▇"
GIRO = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
PONTO = f" {D}·{R} "
FIM = "\033[K"  # apaga do cursor ate o fim da linha

QUADRO = 0.2  # segundos entre desenhos

# Endereco fixo com escape por ambiente. Ler do arquivo de config do Hermes
# foi exatamente o que quebrou antes, com FileNotFoundError num arquivo que
# existia — o painel nao le arquivo nenhum.
MCP_HTTP = os.environ.get("SABOR_MCP_URL", "http://127.0.0.1:9000").rstrip("/")

# O nome da ferramenta e para quem programou. Quem esta olhando a tela quer
# saber o que esta acontecendo com a Dona Maria.
FRASES = {
    "despensa": "consultando a despensa",
    "perfil_ler": "lendo o que ela ja contou",
    "perfil_gravar": "registrando o que ela contou",
    "perfil_pendente": "vendo o que falta perguntar",
    "ingrediente_preco": "anotando o preco que ela paga",
    "prato_salvar": "gravando a receita",
    "prato_checar": "checando se da para fazer",
    "prato_aceitar": "fechando o prato no cardapio",
    "cmv": "calculando o custo do prato",
    "cenarios": "montando os cenarios de preco",
    "cardapio": "lendo o cardapio",
}

# Todo valor comeca na coluna 15, nos cinco blocos — inclusive nas medidas,
# onde a seta mais o rotulo ocupam o mesmo espaco do rotulo sozinho nos
# outros. E o que faz os blocos lerem como uma tabela so.
def sec(nome: str, sub: str = "") -> str:
    """Cabecalho de bloco. Sem subtitulo nao sobra espaco pendurado."""
    return f"  {B}{nome}{R}" + (f"  {D}{sub}{R}" if sub else "") + FIM

LIN = f"    {D}{{:<11}}{R}{{}}{FIM}"
FICHA = f"    {D}{{:<11}}{R}{{:<38}}{D}{{}}{R}{FIM}"
MED = f"    {D}{{}}{R} {D}{{:<9}}{R}{{}}  {{:>18}}{FIM}"

_ANSI = re.compile(r"\033\[[0-9;]*[A-Za-z]")


# --------------------------------------------------------------------------- #
# Formatacao
# --------------------------------------------------------------------------- #
def cortar(linha: str, largura: int) -> str:
    """Corta na largura do terminal sem contar os codigos de cor.

    Linha que passa da largura EMBRULHA: o terminal passa a ocupar duas
    linhas fisicas onde o painel conta uma, e o `\033[nA` do proximo quadro
    sobe de menos. O quadro escorrega uma linha por ciclo.
    """
    visiveis, saida, i = 0, [], 0
    while i < len(linha):
        if achou := _ANSI.match(linha, i):
            saida.append(achou.group())
            i = achou.end()
            continue
        if visiveis >= largura:
            return "".join(saida) + R + FIM
        saida.append(linha[i])
        visiveis += 1
        i += 1
    return "".join(saida)


def sinal(pct: float) -> str:
    """Barras estilo wifi: 4 niveis de 25%.

    Sempre 4 caracteres visiveis — as apagadas ficam em cinza, entao a coluna
    seguinte nunca desalinha.
    """
    pct = max(0.0, min(100.0, pct or 0.0))
    n = 0 if pct <= 0 else min(4, math.ceil(pct / 25))
    cor = VERDE if n <= 2 else (AMAR if n == 3 else VERM)
    return f"{cor}{GLIFOS[:n]}{R}{D}{GLIFOS[n:]}{R}"


_ULTIMA: dict[str, float] = {}


def tendencia(chave: str, valor: float, limiar: float = 0.8) -> str:
    """Seta contra a leitura anterior desta mesma metrica.

    O limiar evita a seta piscar entre 11.0% e 11.2%. A cor fica de fora de
    proposito: a severidade ja e das barras, a seta so diz a direcao.
    """
    anterior = _ULTIMA.get(chave)
    _ULTIMA[chave] = valor
    if anterior is None or abs(valor - anterior) < limiar:
        return "→"
    return "↗" if valor > anterior else "↘"


def marca(pct: float, rotulo: str) -> str:
    """Etiqueta para leitura acima de 100% — nao e erro, e agregacao.

    cpu  `overclock`     soma dos nucleos passando do nominal
    gpu  `multi-engine`  o Windows soma 3D + copy + decode + video, entao
                         130% e mais de um engine ocupado ao mesmo tempo
    """
    return f"  {VERDE}{rotulo}{R}" if pct > 100 else ""



def duracao(seg: float | None) -> str:
    if seg is None:
        return "—"
    seg = max(0, int(seg))
    if seg < 60:
        return f"{seg} s"
    return f"{seg // 60} min" if seg < 3600 else f"{seg // 3600} h {seg % 3600 // 60:02d} min"


def plural(n: int, um: str, varios: str) -> str:
    return f"{n} {um if n == 1 else varios}"


def _mem_curta(txt: str) -> str:
    """'41.23MiB / 63.93GiB' -> '41.2 MiB'."""
    bruto = (txt or "").split("/")[0].strip()
    m = re.match(r"([\d.]+)\s*([KMGT]?i?B)", bruto)
    return f"{float(m.group(1)):.1f} {m.group(2)}" if m else (bruto or "—")


def _idade(iso: str) -> float | None:
    """Segundos desde um carimbo do docker inspect.

    O docker devolve nanossegundos e o fromisoformat aceita no maximo 6
    casas — dai o corte.
    """
    if not iso or iso.startswith("0001-"):
        return None
    iso = re.sub(r"\.(\d{6})\d+", r".\1", iso.strip()).replace("Z", "+00:00")
    try:
        return (datetime.now(timezone.utc) - datetime.fromisoformat(iso)).total_seconds()
    except ValueError:
        return None


def _powershell(script: str, timeout: int = 20) -> str:
    r = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
    )
    return r.stdout.strip()


def _http_json(url: str, timeout: float = 1.5) -> dict | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# Coleta
# --------------------------------------------------------------------------- #
class Coletor(threading.Thread):
    """Roda uma coleta lenta em loop e guarda o ultimo resultado.

    Excecao vira `erro` em vez de sumir: um bloco que desaparece esconde a
    falha, e o painel existe para nao esconder nada.
    """

    def __init__(self, alvo, intervalo: float) -> None:
        super().__init__(daemon=True)
        self._alvo, self._intervalo = alvo, intervalo
        self._parar = threading.Event()
        self.valor: dict = {}
        self.erro: str = ""

    def run(self) -> None:
        while not self._parar.is_set():
            try:
                self.valor, self.erro = self._alvo() or {}, ""
            except Exception as e:
                self.valor, self.erro = {}, f"{type(e).__name__}: {e}"
            self._parar.wait(self._intervalo)

    def parar(self) -> None:
        self._parar.set()


def hardware() -> dict:
    """Ficha da maquina. Lida uma vez — nao muda."""
    ficha = {
        "cpu": "", "cores": psutil.cpu_count(logical=False) or 0,
        "threads": psutil.cpu_count(logical=True) or 0,
        "ram_gb": psutil.virtual_memory().total / 2**30, "gpu": "", "vram_gb": 0.0,
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
    """Uso e VRAM pelos contadores do Windows — funciona em AMD, sem nvidia-smi."""
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


def uso_host() -> dict:
    try:
        return {"cpu": psutil.cpu_percent(interval=None), "mem": psutil.virtual_memory()}
    except Exception:
        return {}


_INSPECT = "{{.State.Health.Status}}\t{{.Config.Image}}\t{{.State.StartedAt}}"


def coletar_container(nome: str) -> dict:
    """Identidade, saude e consumo do container numa passada so."""
    ins = subprocess.run(
        ["docker", "inspect", "--format", _INSPECT, nome],
        capture_output=True,
        text=True,
        timeout=15,
    )
    if ins.returncode != 0:
        # "comando falhou" nao e "container morreu". O docker devolve nao-zero
        # tambem quando o daemon esta ocupado ou fora do ar — e ai o container
        # segue vivo. Ja derrubou uma execucao inteira por essa confusao: a
        # maquina engasgou, o inspect falhou, o painel leu "ausente" e chamou
        # o compose down.
        erro = (ins.stderr or "").lower()
        sumiu = "no such" in erro or "not found" in erro
        return {"estado": "ausente" if sumiu else "docker indisponivel"}
    p = (ins.stdout.strip().split("\t") + ["", "", ""])[:3]
    dados = {"estado": p[0].strip() or "sem healthcheck", "imagem": p[1].strip(), "idade": _idade(p[2])}
    st = subprocess.run(
        ["docker", "stats", "--no-stream", "--format", "{{json .}}", nome],
        capture_output=True, text=True,
    )
    if st.returncode == 0 and st.stdout.strip():
        try:
            dados["stats"] = json.loads(st.stdout.strip().splitlines()[0])
        except json.JSONDecodeError:
            pass
    return dados


TABELAS = ("ingredientes", "perfil", "pratos", "pratos_ingredientes", "evento")

_SQL = (
    "SELECT " + ", ".join(f"(SELECT count(*) FROM {t}) AS {t}" for t in TABELAS)
    + ", (SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()) AS conexoes"
    ", pg_size_pretty(pg_database_size(current_database())) AS tamanho"
    ", split_part(current_setting('server_version'), ' ', 1) AS versao"
    ", (SELECT gasto FROM vw_orcamento) AS gasto"
    ", (SELECT restante FROM vw_orcamento) AS restante"
)


# Ultimo estado de cada ferramenta e o que esta rodando agora. Sai do mesmo
# lugar que o verificador e2e le: a tabela que o MCP escreve a cada chamada.
_SQL_ATIVIDADE = """
SELECT DISTINCT ON (ferramenta) ferramenta, fase, momento
  FROM evento ORDER BY ferramenta, id DESC
"""


def coletar_atividade(dsn: str) -> dict:
    """O que as ferramentas do MCP andaram fazendo, direto do Postgres."""
    with psycopg.connect(dsn, connect_timeout=3) as conn:
        estados = {
            r[0]: {"fase": r[1], "momento": r[2]}
            for r in conn.execute(_SQL_ATIVIDADE).fetchall()
        }
        ultimo = conn.execute(
            "SELECT ferramenta, fase, momento FROM evento ORDER BY id DESC LIMIT 1"
        ).fetchone()
        total = conn.execute("SELECT count(*) FROM evento").fetchone()[0]
    return {
        "estados": estados,
        "ultimo": {"ferramenta": ultimo[0], "fase": ultimo[1], "momento": ultimo[2]} if ultimo else None,
        "total": total,
    }


# --------------------------------------------------------------------------- #
# MCP
# --------------------------------------------------------------------------- #
def coletar_mcp() -> dict:
    """Quem o MCP diz que e — pelo /health dele, nao por arquivo de config."""
    saude = _http_json(f"{MCP_HTTP}/health")
    return saude or {"status": "fora do ar"}


def coletar_banco(dsn: str) -> dict:
    """Estado do banco numa unica ida — uma query com subselects.

    Roda em thread propria a 1,5s: contagem de linha nao muda 5x por segundo
    e o desenho nao pode esperar o banco.
    """
    with psycopg.connect(dsn, connect_timeout=3) as conn:
        linha = conn.execute(_SQL).fetchone()
    return dict(zip(list(TABELAS) + ["conexoes", "tamanho", "versao", "gasto", "restante"], linha))


# --------------------------------------------------------------------------- #
# Blocos
# --------------------------------------------------------------------------- #
def bloco_host(hw: dict) -> list[str]:
    especs = []
    if hw["cpu"]:
        especs.append(("cpu", hw["cpu"], f"{hw['cores']}C/{hw['threads']}T"))
    if hw["gpu"]:
        especs.append(("gpu", hw["gpu"], f"{hw['vram_gb']:.0f} GB" if hw["vram_gb"] else ""))
    if hw["ram_gb"]:
        especs.append(("ram", "", f"{hw['ram_gb']:.1f} GB"))
    if not especs:
        return []
    return [sec("HOST"), *(FICHA.format(r, n, d) for r, n, d in especs)]


def bloco_infra(nome: str, passo: str, dk: dict, bd: dict, etl: dict | None) -> list[str]:
    """O Postgres como entidade. Enquanto sobe, o passo atual mora aqui —
    e por isso que nao existe log de boot rolando acima do painel."""
    estado = dk.get("estado", "—")
    saudavel = estado == "healthy"
    provisorio = estado in ("starting", "—", "docker indisponivel")
    cor = VERDE if saudavel else (AMAR if provisorio else VERM)
    linhas = [sec("INFRA", f"{nome}  {cor}{estado}{R}")]

    if passo:
        giro = GIRO[int(time.monotonic() / 0.1) % len(GIRO)]
        linhas.append(LIN.format("passo", f"{AMAR}{giro}{R} {passo}"))

    ficha = [dk["imagem"]] if dk.get("imagem") else []
    if bd.get("versao"):
        ficha.append(f"server {bd['versao']}")
    if dk.get("idade") is not None:
        ficha.append(f"no ar ha {duracao(dk['idade'])}")
    if ficha:
        linhas.append(LIN.format("imagem", PONTO.join(ficha)))

    if etl:
        taxa = etl["itens"] / etl["segundos"] if etl.get("segundos") else 0
        linhas.append(LIN.format("etl", PONTO.join([
            f"{etl['itens']} itens da planilha",
            plural(etl["novos"], "novo", "novos"),
            f"{VERDE}{etl['segundos']:.2f}s{R} {D}({taxa:.0f} it/s){R}",
        ])))

    linhas.append(LIN.format("acesso", f"localhost:5432{PONTO}{D}admin / admin / sabor_da_maria{R}"))

    if bd:
        linhas.append(LIN.format("tabelas", "   ".join(
            f"{D}{t}{R} {VERDE if bd[t] else D}{bd[t]}{R}" for t in TABELAS)))
        linhas.append(LIN.format("orcamento",
            f"{D}gasto{R} R$ {bd['gasto']}   {VERDE}restante R$ {bd['restante']}{R}"))
        rodape = [str(bd["tamanho"]), plural(bd["conexoes"], "conexao", "conexoes")]
    else:
        rodape = [f"{AMAR}sem leitura do banco{R}"]
    if stats := dk.get("stats"):
        rodape.append(f"{_mem_curta(stats.get('MemUsage'))} residente")
    linhas.append(LIN.format("banco", PONTO.join(rodape)))
    return linhas


# Estado de cada ferramenta na linha dela: cor, marca e rotulo juntos, para
# nao repetir a decisao em tres lugares.
_ESTADO_TOOL = {
    "inicio": (AMAR, "▶", "rodando"),
    "fim": (VERDE, "✓", "ok"),
    "recusa": (AMAR, "✕", "recusou"),
    "erro": (VERM, "✕", "erro"),
    "cancelado": (AMAR, "‖", "cancelado"),
}


def bloco_mcp(mc: dict, at: dict, detalhado: bool = True) -> list[str]:
    endereco = re.sub(r"^https?://", "", MCP_HTTP)
    status = mc.get("status", "fora do ar")
    if status == "fora do ar":
        return [sec("MCP", endereco), LIN.format("estado", f"{VERM}fora do ar{R}")]

    cor = VERDE if status == "ok" else AMAR
    linhas = [sec("MCP", f"{mc.get('servidor', 'sabor-da-maria')}  {cor}{status}{R}")]

    ficha = [endereco]
    if (idade := mc.get("no_ar_ha")) is not None:
        ficha.append(f"no ar ha {duracao(idade)}")
    if banco := mc.get("banco"):
        ficha.append(f"banco {banco}")
    linhas.append(LIN.format("servidor", PONTO.join(ficha)))

    # O que esta acontecendo AGORA: fase `inicio` sem fim e ferramenta rodando.
    ultimo, estados = at.get("ultimo"), at.get("estados") or {}
    if ultimo:
        frase = FRASES.get(ultimo["ferramenta"], ultimo["ferramenta"])
        ha = (datetime.now(timezone.utc) - ultimo["momento"]).total_seconds()
        if ultimo["fase"] == "inicio":
            giro = GIRO[int(time.monotonic() / 0.1) % len(GIRO)]
            atividade = f"{AMAR}{giro}{R} {frase}{PONTO}{D}ha {duracao(ha)}{R}"
        else:
            marca = {"fim": VERDE + "✓", "recusa": AMAR + "recusou", "erro": VERM + "✕"}
            atividade = (f"{D}ultima:{R} {frase} {marca.get(ultimo['fase'], '')}{R}"
                         f"{PONTO}{D}ha {duracao(ha)}{R}")
    else:
        atividade = f"{D}em espera — nenhuma chamada ainda{R}"
    linhas.append(LIN.format("agora", atividade))

    # Uma ferramenta por linha, cada uma com o proprio estado. Numa linha so
    # elas viravam um borrao de nomes colados, e o estado de cada uma — que e
    # a informacao que importa — se perdia no meio.
    ferramentas = mc.get("ferramentas") or []
    linhas.append(
        LIN.format(
            "tools",
            f"{len(ferramentas)}{PONTO}{at.get('total', 0)} chamada(s) registrada(s)",
        )
    )
    if not detalhado:
        # Janela curta: as ferramentas viram uma linha de marcas. Some o
        # detalhe, nao o bloco de baixo — cortar o HARDWARE STATS em silencio
        # seria esconder informacao sem avisar.
        marcas = " ".join(
            _ESTADO_TOOL.get((estados.get(n) or {}).get("fase"), (D, "·", ""))[0]
            + _ESTADO_TOOL.get((estados.get(n) or {}).get("fase"), (D, "·", ""))[1]
            + n[:3]
            + R
            for n in ferramentas
        )
        linhas.append(LIN.format("", f"{marcas}  {D}(janela curta){R}"))
        return linhas

    for nome in ferramentas:
        fase = (estados.get(nome) or {}).get("fase")
        cor, marca, rotulo = _ESTADO_TOOL.get(fase, (D, "·", "em espera"))
        linhas.append(
            f"      {cor}{marca}{R} {nome:<18}{D}{FRASES.get(nome, ''):<30}{R}"
            f"{cor}{rotulo}{R}{FIM}"
        )
    return linhas


def bloco_hardware(hw: dict, host: dict, g: dict) -> list[str]:
    medidas = []
    if host:
        mem = host["mem"]
        medidas.append(MED.format(tendencia("cpu", host["cpu"]), "cpu",
                                  sinal(host["cpu"]), f"{host['cpu']:.1f} %")
                       + marca(host["cpu"], "overclock"))
        medidas.append(MED.format(tendencia("ram", mem.percent), "ram", sinal(mem.percent),
                                  f"{mem.used / 2**30:.1f} / {mem.total / 2**30:.1f} GB"))
    if (uso := g.get("uso")) is not None:
        medidas.append(MED.format(tendencia("gpu", uso), "gpu", sinal(uso), f"{uso:.1f} %")
                       + marca(uso, "multi-engine"))
        if (vram := g.get("vram_gb")) is not None and hw["vram_gb"]:
            pct = vram / hw["vram_gb"] * 100
            medidas.append(MED.format(tendencia("vram", pct, 0.3), "vram", sinal(pct),
                                      f"{vram:.1f} / {hw['vram_gb']:.1f} GB"))
    return [sec("HARDWARE STATS", "consumo global"), *medidas] if medidas else []


# --------------------------------------------------------------------------- #
# Desenho
# --------------------------------------------------------------------------- #
def acompanhar(estado, dsn: str, container: str) -> None:
    """Segura o terminal com o painel ate o Ctrl+C ou o container cair.

    `estado` e o objeto compartilhado que a thread do boot atualiza: o painel
    le `passo`, `etl` e `erro` dela sem nunca esperar por nada.
    """
    hw = hardware()
    gpu = Coletor(coletar_gpu, 3.0)
    docker = Coletor(lambda: coletar_container(container), 2.0)
    banco = Coletor(lambda: coletar_banco(dsn), 1.5)
    atividade = Coletor(lambda: coletar_atividade(dsn), 0.6)
    mcp = Coletor(coletar_mcp, 2.0)
    coletores = (gpu, docker, banco, atividade, mcp)
    for c in coletores:
        c.start()

    uso_host()  # descarta a leitura de calibracao do psutil
    anterior = 0
    # Uma leitura ruim nao encerra nada. Sao precisas TRES seguidas — cerca de
    # seis segundos — para o painel aceitar que o container morreu de verdade.
    morrendo = 0

    try:
        while True:
            if estado.erro:
                print(f"\n  {VERM}✕ {estado.erro}{R}")
                return
            dk = docker.valor
            if estado.pronto and dk.get("estado") in ("unhealthy", "ausente"):
                morrendo += 1
                if morrendo >= 3:
                    print(f"\n  {VERM}✕ container ficou '{dk['estado']}' "
                          f"em {morrendo} leituras seguidas{R}")
                    return
            else:
                morrendo = 0

            # Separador e FIM, nao "": linha em branco sem [K nao apaga
            # nada, e o conteudo do quadro anterior sobrevive exatamente
            # nessas posicoes quando um bloco muda de altura. TODA linha
            # emitida precisa limpar a propria linha.
            colunas, linhas = shutil.get_terminal_size((120, 40))
            teto = max(4, linhas - 2)

            def montar(detalhado: bool) -> list[str]:
                return [
                    FIM,
                    *bloco_host(hw), FIM,
                    *bloco_infra(container, estado.passo, dk, banco.valor, estado.etl), FIM,
                    *bloco_mcp(mcp.valor, atividade.valor, detalhado), FIM,
                    *bloco_hardware(hw, uso_host(), gpu.valor), FIM,
                    f"  {D}Ctrl+C para parar e derrubar o container{R}{FIM}",
                ]

            quadro = montar(True)
            if len(quadro) > teto:
                quadro = montar(False)

            # Subir N linhas so acerta se N for EXATAMENTE quantas linhas
            # fisicas o quadro anterior ocupou. Tres coisas garantem isso:
            #
            #   corte na largura   linha que embrulha ocupa duas fisicas
            #   corte na altura    quadro maior que a janela rola o terminal
            #   altura constante   quadro que encolhe deixaria a cauda do
            #                      anterior; completar com linhas de \033[K
            #                      apaga a cauda e mantem a conta exata
            quadro = quadro[:teto]
            quadro = [cortar(l, max(40, colunas - 1)) for l in quadro]
            quadro += [FIM] * max(0, anterior - len(quadro))

            if anterior:
                print(f"\033[{anterior}A", end="")
            print("\n".join(quadro), flush=True)
            anterior = len(quadro)
            time.sleep(QUADRO)
    finally:
        for c in coletores:
            c.parar()
