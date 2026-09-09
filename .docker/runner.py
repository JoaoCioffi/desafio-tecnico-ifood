"""
Sobe o Postgres e o Hermes juntos e SEGURA O TERMINAL.
Os containers vivem enquanto o script viver: Ctrl+C derruba os dois.

    python .docker/runner.py            # sobe tudo e segura o terminal no painel
    python .docker/runner.py --setup    # wizard do Hermes (rodar uma vez, antes)
    python .docker/runner.py --down     # derruba, preserva volume e imagens
    python .docker/runner.py --reset    # DESTROI o volume do banco
    python .docker/runner.py --delete   # DESTROI tudo, inclusive os dados do agente

A cada subida ele tambem aplica o perfil versionado no config do agente e
carrega a planilha da Dona Maria no Postgres. Os dois passos sao idempotentes.

Dependencias em requirements.txt.

O terminal fica preso no painel de status, nao nos logs. O Hermes despeja
dezenas de WARNING de tool indisponivel a cada boot, e isso enterrava a unica
coisa que importa olhar. Os logs saem sob demanda:

    docker compose -f .docker/docker-compose.yaml logs -f

Sobre o .env: ele fica na raiz, nao aqui. O compose so le `.env` automatico
quando o arquivo esta ao lado do docker-compose.yaml, entao todo comando
daqui passa `--env-file` apontando para a raiz. E o mesmo arquivo entra no
container do Hermes pelo `env_file:` do compose — um resolve os ${VAR} do
YAML, o outro entrega as variaveis ao processo.

Segredos NUNCA aparecem na tela. O painel lista o nome da variavel e diz se
ela esta definida, nunca o valor; e todo texto que sai daqui — erro do
compose, log do container — passa por `redigir()` antes de ser impresso.

Sobre o redesenho: cada linha do painel e cortada na largura do terminal
antes de sair. Uma linha que embrulha desalinha a conta de "subir N linhas"
e deixa lixo na tela; cortar antes e o que torna a conta sempre exata.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import threading
import time
from collections import deque
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

AQUI = Path(__file__).resolve().parent
RAIZ = AQUI.parent
COMPOSE = AQUI / "docker-compose.yaml"
ENV = RAIZ / ".env"
DADOS_HERMES = AQUI / "hermes-data"      # dados do agente — fora do git
PERFIL = AQUI / "hermes-profile" / "config.yaml"  # delta versionado
BASELINE = AQUI / "hermes-profile" / "config.base.yaml"  # config completa do wizard
ESQUEMA = AQUI / "db.sql"
PLANILHA = RAIZ / "shared" / "despensa_dona_maria.xlsx"

# Do enunciado: "Orcamento restante para complementos: R$ 80,00".
ORCAMENTO_INICIAL = Decimal("80.00")

PROJETO = "sabor-da-maria"

# Cabecalho do painel. Cru, sem cor e sem margem: quem desenha decide as duas.
#
# String RAW de proposito — a arte e feita de barras invertidas, e `\_` ou `\/`
# num literal normal e sequencia de escape invalida. Hoje o Python so avisa;
# a partir da 3.12 o aviso vira erro de sintaxe.
BANNER = r"""
  _   ____        _                      _         __  __            _         _
 | | / ___|  __ _| |__   ___  _ __    __| | __ _  |  \/  | __ _ _ __(_) __ _  | |
 | | \___ \ / _` | '_ \ / _ \| '__|  / _` |/ _` | | |\/| |/ _` | '__| |/ _` | | |
 | |  ___) | (_| | |_) | (_) | |    | (_| | (_| | | |  | | (_| | |  | | (_| | | |
 | | |____/ \__,_|_.__/ \___/|_|     \__,_|\__,_| |_|  |_|\__,_|_|  |_|\__,_| | |
 |_|                                                                          |_|
"""[1:-1].split("\n")

LEGENDA = "docker compose telemetry"

# Os dois comandos que valem estar a vista de quem esta olhando o painel.
# `--down` e redundante (o Ctrl+C ja faz, e o rodape diz) e `--reset` e um
# subconjunto de `--delete`; listar os quatro viraria menu, nao dica.
COMANDOS = (("--setup", "reconfigura pelo wizard"),
            ("--delete", "apaga tudo e recomeca do zero"))
VOLUME = "sabor-da-maria-pgdata"
REDE = "sabor-da-maria-net"
# O MCP entra como servico monitorado, mas NAO ganha bloco proprio: o painel
# tem tres blocos e essa regra vale mais que a simetria. Ele e infraestrutura
# do agente, entao aparece como linha dentro de INFRA.
CONTAINERS = {"postgres": "sabor-da-maria-db",
              "hermes": "sabor-da-maria-hermes",
              "mcp": "sabor-da-maria-mcp"}

# Sem estas o compose sobe com string vazia e o erro so aparece la na frente,
# disfarcado de "senha invalida". Melhor barrar aqui.
OBRIGATORIAS = ("DB_USER", "DB_PASSWORD", "DB_PORT", "DB_NAME", "LLM_PROVIDER_API_KEY")

# Qualquer variavel cujo NOME casa com isto tem o VALOR tratado como segredo.
# E por nome, nao por lista fixa, para que o TELEGRAM_BOT_TOKEN ja entre
# protegido no dia em que voce o adicionar ao .env.
PADRAO_SEGREDO = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL)", re.I)

# Preenchido por ler_env(); consumido por redigir().
SEGREDOS: list[str] = []

# Ligado por subir(); so ele autoriza o encerrar() do finally.
SUBIU = False

# Preenchido por carregar(); lido pelo bloco INFRA. Some se o ETL nao rodou.
ETL: dict = {}

# Preenchido por aplicar_perfil(); lido pelo bloco HERMES.
PERFIL_APLICADO: list[str] = []

# De onde veio o config.yaml desta subida. Lido pelo bloco HERMES.
CONFIG_ORIGEM: str = ""

ANSI = re.compile(r"\033\[[0-9;]*m")


# --------------------------------------------------------------------------- #
# Terminal
# --------------------------------------------------------------------------- #
# Antes de qualquer coisa: o console do Windows abre em cp1252 e engasga no
# primeiro glifo do painel. Precisa vir antes do probe logo abaixo, senao ele
# mede a codificacao velha e cai no fallback ascii sem motivo.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

try:
    from colorama import Fore, Style
    from colorama import init as colorama_init

    colorama_init()
    R, B, D = Style.RESET_ALL, Style.BRIGHT, Style.DIM
    VERDE, VERM, AMAR = Fore.GREEN, Fore.RED, Fore.YELLOW
except ImportError:  # colorama ausente: segue sem cor, nao quebra
    R = B = D = VERDE = VERM = AMAR = ""
    if os.name == "nt":
        try:
            import ctypes

            _k = ctypes.windll.kernel32
            _k.SetConsoleMode(_k.GetStdHandle(-11), 7)
        except Exception:
            pass


def _imprimivel(texto: str) -> bool:
    """O console do Windows nem sempre e UTF-8. Testar antes de usar evita
    trocar o painel inteiro por interrogacoes."""
    try:
        texto.encode(sys.stdout.encoding or "utf-8")
        return True
    except (UnicodeEncodeError, LookupError):
        return False


if _imprimivel("✓✗•⠋…▁█↗→↘"):
    OK, FALHA, PONTO, CORTE = "✓", "✗", "•", "…"
    GIRO = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
    # Oito alturas de bloco, todas assentadas na mesma linha de base: uma
    # amostra por coluna desenha a curva do historico.
    NIVEIS = "▁▂▃▄▅▆▇█"
    SOBE, IGUAL, DESCE = "↗", "→", "↘"
else:
    OK, FALHA, PONTO, CORTE = "+", "x", "*", "~"
    GIRO = "|/-\\"
    # Rampa de tinta crescente. Nao ha altura em ASCII, entao a leitura vira
    # densidade — pior, mas so aparece em console que nao aceita UTF-8.
    NIVEIS = "._-=+*#@"
    SOBE, IGUAL, DESCE = "^", "=", "v"

# Pulso do npm install: um ponto que anda enquanto algo sobe.
PULSO = (".:::", ":.::", "::.:", ":::.")


def largura() -> int:
    return shutil.get_terminal_size((100, 30)).columns - 1


def cortar(linha: str, limite: int) -> str:
    """Corta na largura visivel, ignorando os escapes de cor na contagem.
    Quando precisa cortar, devolve o texto plano: fatiar no meio de um
    escape imprime lixo, e um corte sem reset vaza cor para a linha seguinte."""
    if len(ANSI.sub("", linha)) <= limite:
        return linha
    return ANSI.sub("", linha)[: max(0, limite - 1)] + CORTE


def redigir(texto: str) -> str:
    """Troca todo valor secreto conhecido por uma mascara.

    Roda em TUDO que vai para a tela: o stderr do compose repete o comando
    com as variaveis resolvidas, e o log de um container pode ecoar a DSN
    inteira numa mensagem de conexao recusada.
    """
    for segredo in SEGREDOS:
        texto = texto.replace(segredo, PONTO * 6)
    return texto


class Serie:
    """Historico curto de uma metrica: vira curva e seta de tendencia."""

    # Amplitude minima da janela de desenho, em pontos percentuais.
    #
    # Sem ela, uma serie parada faria max == min e a divisao estouraria. Com
    # ela, uma serie parada ocupa um degrau so, e o ruido de centesimo que
    # existe em toda leitura de CPU nao vira montanha. O numero e o preco
    # dessa escolha: variacao menor que 1 ponto percentual e desenhada como
    # menor que a altura cheia, em vez de preencher o grafico.
    PISO = 1.0

    def __init__(self, tamanho: int = 60) -> None:
        # Guarda mais do que costuma caber na tela: a largura do desenho sai
        # do terminal, que pode ser largo, e sobra vira historico descartado
        # na hora de desenhar, em vez de amostra que nunca foi coletada.
        # A 3s por amostra, 60 sao tres minutos.
        self._valores: deque[float] = deque(maxlen=tamanho)

    def anotar(self, valor: float | None) -> None:
        if valor is not None:
            self._valores.append(valor)

    @property
    def atual(self) -> float | None:
        return self._valores[-1] if self._valores else None

    def _escala(self, valores: list[float]) -> tuple[float, float]:
        """Janela vertical do desenho: o proprio min/max, nunca menor que PISO.

        Aqui esta a diferenca em relacao ao medidor que havia antes. Numa
        escala fixa de 0 a 100 a maquina ociosa vive colada no chao: CPU a
        0,3% e CPU a 0,9% desenham identicas, e a linha vira enfeite. Contra
        o proprio historico, 0,3 e 0,9 ficam a degraus de distancia e da para
        ver a maquina respirar.

        O preco e que a altura passa a ser relativa: curva cheia significa
        "variou o maximo que variou nestes tres minutos", nao "esta cheio". A
        COR carrega o absoluto (`cor_faixa`, sobre o valor de agora) e o
        numero ao lado da a leitura exata. Sao tres perguntas diferentes, e
        cada uma tem seu canal.

        O chao da janela e sempre o MENOR valor do historico; o piso so
        levanta o teto. E o que faz serie parada desenhar rente ao chao em vez
        de virar uma parede na meia altura: sem isso, um disco cravado em
        0,55% ficaria identico a um disco em 50%, e a linha mentiria com todas
        as letras.

        >>> def serie(*v):
        ...     s = Serie()
        ...     for x in v:
        ...         s.anotar(x)
        ...     return s
        >>> serie()._escala([0.55, 0.55, 0.55])       # parada: o piso levanta o teto
        (0.55, 1.55)
        >>> serie()._escala([2.0, 40.0, 91.0])        # variou: manda o proprio min/max
        (2.0, 91.0)
        """
        lo, hi = min(valores), max(valores)
        if hi - lo < self.PISO:
            hi = lo + self.PISO
        return lo, hi

    def curva(self, largura: int) -> str:
        """As ultimas `largura` amostras, uma coluna cada, mais nova a direita.

        A guarda do zero nao e defensiva, e necessaria: `lista[-0:]` e
        `lista[0:]`, ou seja, a lista TODA. Sem ela, pedir zero coluna devolve
        o historico inteiro, e `f"{s:>0}"` nao trunca nada — o painel
        imprimia sessenta blocos justamente no terminal estreito onde a curva
        deveria ter sumido.

        Os exemplos comparam INDICES em NIVEIS, nao os glifos: o alfabeto do
        desenho muda conforme o console aceite UTF-8, e um doctest preso ao
        bloco Unicode quebraria no console que caiu no ASCII.

        >>> def serie(*v):
        ...     s = Serie()
        ...     for x in v:
        ...         s.anotar(x)
        ...     return s
        >>> serie(1, 2, 3).curva(0)                   # sem espaco, nada desenhado
        ''
        >>> serie().curva(10)                         # antes da primeira coleta
        ''
        >>> [NIVEIS.index(c) for c in serie(0, 20, 40, 60, 80, 100).curva(6)]
        [0, 1, 3, 4, 6, 7]
        >>> [NIVEIS.index(c) for c in serie(*[0.55] * 4).curva(4)]
        [0, 0, 0, 0]
        >>> len(serie(*range(50)).curva(12))          # so as ultimas que cabem
        12
        """
        if largura <= 0:
            return ""
        valores = list(self._valores)[-largura:]
        if not valores:
            return ""
        lo, hi = self._escala(valores)
        degrau = (hi - lo) / len(NIVEIS)
        return "".join(
            NIVEIS[max(0, min(len(NIVEIS) - 1, int((v - lo) / degrau)))]
            for v in valores
        )

    def tendencia(self) -> str:
        """Seta do valor de agora contra a MEDIA do historico, com zona morta.

        Comparar so com a leitura anterior parece o obvio e erra nos dois
        extremos. Numa rampa lenta cada passo e minusculo, entao a seta fica
        congelada em `→` enquanto a curva desenha uma escada evidente. E logo
        depois de um pico, as duas ultimas amostras ja empataram no chao e a
        seta perde a descida inteira.

        Contra a media, a pergunta vira "estou acima ou abaixo de onde tenho
        estado", que e o que a seta ao lado de um historico deveria responder.
        A rampa acusa desde o comeco, e a queda depois de um pico acusa `↘`
        na descida e volta a `→` quando a leitura assenta — a montanha
        continua desenhada na curva, mas ja nao e novidade.

        A zona morta acompanha a escala do desenho em vez de ser um numero
        fixo em pontos percentuais: a seta mexe quando a curva mexeria
        tambem. Fixa, ficaria travada exatamente nas metricas que vivem perto
        do chao — as que esta escala movel existe para tornar legiveis.
        """
        valores = list(self._valores)
        if len(valores) < 2:
            return IGUAL
        lo, hi = self._escala(valores)
        delta = valores[-1] - sum(valores) / len(valores)
        if abs(delta) < (hi - lo) / len(NIVEIS):
            return IGUAL
        return SOBE if delta > 0 else DESCE


def cor_faixa(percentual: float | None) -> str:
    """Verde ate 25%, amarelo ate 75%, vermelho acima.

    A faixa 50-75% acabou em amarelo junto com 25-50%: nao existe uma quarta
    cor que leia como "pior que amarelo, melhor que vermelho" sem virar
    adivinhacao para quem olha.
    """
    if percentual is None:
        return ""
    if percentual >= 75:
        return VERM
    if percentual >= 25:
        return AMAR
    return VERDE


class Telemetria:
    """Coleta em thread. O painel so le o ultimo retrato.

    `docker stats` leva perto de um segundo e a consulta ao banco abre rede.
    Chamados no laco do desenho, cada quadro esperaria por eles e a animacao
    engasgaria. Aqui a coleta tem cadencia propria e o desenho nunca bloqueia.

    Atribuir atributo e atomico sob o GIL e so esta thread escreve — um Lock
    seria cerimonia sem funcao.
    """

    def __init__(self, variaveis: dict[str, str], intervalo: float = 3.0) -> None:
        self.variaveis = variaveis
        self.intervalo = intervalo
        self.cpu, self.ram, self.disco, self.gpu = Serie(), Serie(), Serie(), Serie()
        self.ram_texto = self.disco_texto = self.gpu_texto = "—"

        # `docker stats` mede CPU com 100% = UM nucleo saturado, entao dois
        # containers ocupados passam de 100% e a barra estoura a escala. O
        # numero que cabe em 0-100 e a fracao da maquina inteira. E o NCPU do
        # Docker conta processador LOGICO — 24 num 5900X de 12 nucleos.
        self.threads = max(1, int(_numero(subprocess.run(
            ["docker", "info", "--format", "{{.NCPU}}"],
            capture_output=True, text=True).stdout) or 1))
        self.cpu_texto = _identificar_cpu(self.threads)
        self.gpu_nome, self.gpu_vram = _identificar_gpu()

        self.banco: dict = {}
        self.agente: dict = {}
        self.divergencia: list[str] = []
        # O modelo sai do config, nao do historico de uso: antes da primeira
        # conversa o `session_model_usage` esta vazio, e a linha mais basica
        # do bloco — qual modelo esta carregado — sumia justamente na hora em
        # que se quer conferir se a configuracao pegou.
        self.modelo, self.provedor = _modelo_configurado()
        self.parar = False
        threading.Thread(target=self._laco, daemon=True).start()

    def _laco(self) -> None:
        conexao, volta = None, 0
        while not self.parar:
            volta += 1
            for coleta in (self._hardware, self._agente):
                try:
                    coleta()
                except Exception:
                    pass  # telemetria e enfeite: nunca derruba o runner
            # A GPU sai por contador de performance do Windows, e uma leitura
            # leva perto de 3s — mais que o proprio ciclo. Numa cadencia de
            # quatro voltas ela sai a cada ~12s, e a thread passa a maior
            # parte do tempo dormindo em vez de perseguindo o proprio rabo.
            if volta % 4 == 1:
                try:
                    self._gpu()
                except Exception:
                    pass
            try:
                conexao = self._postgres(conexao)
            except Exception:
                conexao = None
            time.sleep(self.intervalo)

    # ---------------------------------------------------------------- #
    def _hardware(self) -> None:
        proc = subprocess.run(
            ["docker", "stats", "--no-stream", "--format",
             "{{.CPUPerc}}|{{.MemUsage}}|{{.MemPerc}}", *CONTAINERS.values()],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        cpu_total = usado = limite = 0.0
        for linha in proc.stdout.splitlines():
            partes = linha.strip().split("|")
            if len(partes) < 3:
                continue
            cpu_total += _numero(partes[0])
            usa, _, tem = partes[1].partition("/")
            usado += _bytes(usa)
            limite = max(limite, _bytes(tem))  # o teto e da VM, igual p/ todos
        if limite:
            self.ram.anotar(usado / limite * 100)
            self.ram_texto = f"{_humano(usado)} / {_humano(limite)}"
        self.cpu.anotar(cpu_total / self.threads)

        # Disco: `docker system df` soma imagens, containers, volumes e cache.
        # O teto do Docker Desktop nao sai pelo CLI, entao o percentual vai
        # contra o disco do host, que e onde esses bytes de fato moram.
        proc = subprocess.run(
            ["docker", "system", "df", "--format", "{{.Size}}"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        ocupado = sum(_bytes(l) for l in proc.stdout.splitlines() if l.strip())
        total = shutil.disk_usage(RAIZ.anchor or "/").total
        if total:
            self.disco.anotar(ocupado / total * 100)
            self.disco_texto = f"{_humano(ocupado)} / {_humano(total)}"

    def _gpu(self) -> None:
        """Uso e memoria da GPU pelos contadores de performance do Windows.

        Sem nvidia-smi de proposito: a maquina tem Radeon, e o contador do SO
        e agnostico de fabricante — funciona igual em AMD, Intel e NVIDIA.
        As duas leituras vao numa chamada so porque o caro aqui e subir o
        PowerShell, nao consultar o contador.
        """
        if os.name != "nt" or not self.gpu_nome:
            return
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", _PS_GPU],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=20,
        )
        partes = proc.stdout.strip().split("|")
        if len(partes) < 2:
            return
        self.gpu.anotar(min(100.0, _numero(partes[0])))
        usada = float(_numero(partes[1]))
        self.gpu_texto = (f"{self.gpu_nome} {PONTO} "
                          f"{_humano(usada)} / {_humano(self.gpu_vram)}"
                          if self.gpu_vram else self.gpu_nome)

    def _agente(self) -> None:
        """Telemetria do Hermes, lida do state.db do bind mount.

        E sqlite pela stdlib sobre um arquivo local: nao custa `docker exec`
        nem rede. E o mesmo banco que alimenta o dashboard, entao o painel
        mostra os mesmos numeros sem duplicar a fonte.
        """
        alvo = DADOS_HERMES / "state.db"
        if not alvo.is_file():
            return
        import sqlite3

        # somente-leitura e imutavel: o gateway esta escrevendo neste arquivo
        # agora, e abrir normal criaria um -wal ao lado, de dono trocado.
        conexao = sqlite3.connect(f"file:{alvo}?mode=ro&immutable=1", uri=True)
        try:
            sessoes, mensagens, ferramentas = conexao.execute(
                "SELECT count(*), coalesce(sum(message_count), 0), "
                "       coalesce(sum(tool_call_count), 0) FROM sessions"
            ).fetchone()
            uso = conexao.execute(
                "SELECT model, billing_provider, sum(input_tokens), "
                "       sum(output_tokens), sum(cache_read_tokens), "
                "       sum(estimated_cost_usd), sum(api_call_count) "
                "  FROM session_model_usage "
                " WHERE task = '' OR task IS NULL "
                " GROUP BY model, billing_provider "
                " ORDER BY sum(api_call_count) DESC LIMIT 1"
            ).fetchone()
        finally:
            conexao.close()

        self.agente = {"sessoes": sessoes, "mensagens": mensagens,
                       "ferramentas": ferramentas}
        self.divergencia = _config_divergente()
        if uso:
            modelo, provedor, entrada, saida, cache, custo, chamadas = uso
            self.agente.update(modelo=modelo, provedor=provedor,
                               entrada=entrada or 0, saida=saida or 0,
                               cache=cache or 0, custo=custo or 0.0,
                               chamadas=chamadas or 0)

    def _postgres(self, conexao):
        import psycopg

        if conexao is None or conexao.closed:
            conexao = psycopg.connect(dsn(self.variaveis), connect_timeout=3)
        with conexao.cursor() as cur:
            linha = cur.execute(
                """
                SELECT current_setting('server_version'),
                       pg_database_size(current_database()),
                       (SELECT count(*) FROM pg_stat_activity
                         WHERE datname = current_database()),
                       (SELECT count(*) FROM despensa),
                       (SELECT count(*) FROM precos),
                       (SELECT total    FROM vw_orcamento),
                       (SELECT restante FROM vw_orcamento)
                """
            ).fetchone()
        conexao.rollback()
        chaves = ("versao", "tamanho", "conexoes", "despensa", "precos",
                  "orcamento_inicial", "orcamento_saldo")
        self.banco = dict(zip(chaves, linha))
        return conexao


# Uma chamada so: subir o PowerShell custa mais que ler os contadores.
_PS_GPU = (
    "$u=(Get-Counter '\\GPU Engine(*)\\Utilization Percentage' -EA SilentlyContinue)"
    ".CounterSamples|Measure-Object CookedValue -Sum;"
    "$m=(Get-Counter '\\GPU Adapter Memory(*)\\Dedicated Usage' -EA SilentlyContinue)"
    ".CounterSamples|Measure-Object CookedValue -Sum;"
    "\"$($u.Sum)|$($m.Sum)\""
)

_PS_GPU_ESTATICA = (
    "Get-ChildItem 'HKLM:\\SYSTEM\\CurrentControlSet\\Control\\Class\\"
    "{4d36e968-e325-11ce-bfc1-08002be10318}' -EA SilentlyContinue|"
    "ForEach-Object{$p=Get-ItemProperty $_.PSPath -EA SilentlyContinue;"
    "if($p.'HardwareInformation.qwMemorySize'){"
    "\"$($p.DriverDesc)|$($p.'HardwareInformation.qwMemorySize')\"}}|Select-Object -First 1"
)


def _powershell(script: str, timeout: int = 20) -> str:
    if os.name != "nt":
        return ""
    proc = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout,
    )
    return proc.stdout.strip()


def _identificar_cpu(threads: int) -> str:
    """Modelo e contagem de nucleos. Lido uma vez: nao muda em execucao."""
    nucleos = threads
    modelo = ""
    try:
        if os.name == "nt":
            saida = _powershell(
                "$c=Get-CimInstance Win32_Processor|Select-Object -First 1;"
                "\"$($c.Name.Trim())|$($c.NumberOfCores)\"", timeout=15)
            modelo, _, cru = saida.partition("|")
            nucleos = int(_numero(cru)) or threads
        else:
            texto = Path("/proc/cpuinfo").read_text(encoding="utf-8", errors="replace")
            for linha in texto.splitlines():
                if linha.startswith("model name"):
                    modelo = linha.partition(":")[2].strip()
                    break
            fisicos = {l.partition(":")[2].strip() for l in texto.splitlines()
                       if l.startswith("core id")}
            nucleos = len(fisicos) or threads
    except Exception:
        pass
    # "AMD Ryzen 9 5900X 12-Core Processor" -> "Ryzen 9 5900X": o painel tem
    # largura de terminal, nao de datasheet.
    for lixo in ("(R)", "(TM)", "CPU", "Processor", "Intel", "AMD", "12-Core",
                 "16-Core", "8-Core", "6-Core"):
        modelo = modelo.replace(lixo, "")
    modelo = re.sub(r"\s+", " ", modelo).strip(" @-")
    sufixo = f"{nucleos}c/{threads}t"
    return f"{modelo} {PONTO} {sufixo}" if modelo else sufixo


def _identificar_gpu() -> tuple[str, float]:
    """(nome, VRAM em bytes). Vazio quando nao ha placa identificavel.

    A VRAM sai do registro, nao do Win32_VideoController: o AdapterRAM de la
    e um inteiro de 32 bits e satura em 4 GB — uma 7800 XT de 16 GB aparece
    com 4. O qwMemorySize do registro e de 64 bits e diz a verdade.
    """
    try:
        saida = _powershell(_PS_GPU_ESTATICA, timeout=15)
        nome, _, bytes_ = saida.partition("|")
        nome = re.sub(r"\s+", " ", nome.replace("AMD ", "").replace("NVIDIA ", "")).strip()
        return nome, float(_numero(bytes_))
    except Exception:
        return "", 0.0


def _config_divergente() -> list[str]:
    """Chaves em que o config VIVO nao reflete o delta versionado.

    Existe por causa de uma falha silenciosa que aconteceu de verdade: subir
    com `docker compose up -d` em vez do runner cria os containers mas nao
    funde o delta. O painel ficava todo verde, o servidor MCP saudavel, e o
    agente sem nenhuma ferramenta — porque `mcp_servers` nunca chegou no
    config. Nada na tela denunciava.

    Roda na thread de coleta, nao no desenho: sao dois parses de YAML.
    """
    if not PERFIL.is_file():
        return []
    try:
        import yaml

        vivo = yaml.safe_load((DADOS_HERMES / "config.yaml").read_text(encoding="utf-8")) or {}
        delta = yaml.safe_load(PERFIL.read_text(encoding="utf-8")) or {}
        return fundir(vivo, delta)[1]
    except Exception:
        return []


def _modelo_configurado() -> tuple[str, str]:
    """(modelo, provedor) do config.yaml. Lido uma vez: o delta ja foi aplicado."""
    alvo = DADOS_HERMES / "config.yaml"
    try:
        import yaml

        modelo = (yaml.safe_load(alvo.read_text(encoding="utf-8")) or {}).get("model", {})
        return modelo.get("default", ""), modelo.get("provider", "")
    except Exception:
        return "", ""


def _numero(texto: str) -> float:
    achado = re.search(r"[\d.]+", texto or "")
    return float(achado.group()) if achado else 0.0


_ESCALA = {"B": 1, "KIB": 1024, "MIB": 1024**2, "GIB": 1024**3, "TIB": 1024**4,
           "KB": 1000, "MB": 1000**2, "GB": 1000**3, "TB": 1000**4}


def _bytes(texto: str) -> float:
    """'45.2MiB' -> 47394816.0. Aceita as duas escalas que o Docker mistura."""
    achado = re.search(r"([\d.]+)\s*([A-Za-z]+)", (texto or "").strip())
    return float(achado.group(1)) * _ESCALA.get(achado.group(2).upper(), 1) if achado else 0.0


def _humano(n: float) -> str:
    for sufixo, corte in (("TB", 1024**4), ("GB", 1024**3), ("MB", 1024**2)):
        if n >= corte:
            return f"{n / corte:.1f} {sufixo}"
    return f"{n / 1024:.0f} KB"


class Pulsando:
    """Linha unica que pulsa enquanto algo bloqueante roda.

    Existe por causa do silencio: entre o `compose up` e o primeiro quadro do
    painel passavam alguns segundos sem nada na tela, e tela parada nao
    distingue "trabalhando" de "travado". O painel so pode desenhar depois
    que ha o que desenhar; ate la, quem fala e isto.

    Some sem deixar rastro — `\\r` volta ao inicio da linha e `\\033[K` apaga
    ate o fim dela, entao a proxima saida escreve por cima.
    """

    def __init__(self, texto: str) -> None:
        # `texto` e reatribuivel de fora: o laco le a cada quadro, entao quem
        # esta trabalhando pode dizer o que esta fazendo sem parar o pulso.
        self.texto = texto
        self.parar = False

    def __enter__(self) -> Pulsando:
        # Fora de um terminal o retorno de carro nao volta o cursor: cada
        # quadro viraria uma linha nova, e redirecionar a saida para arquivo
        # geraria megabytes de pulso. Sem tty, o passo nao se anuncia.
        # pulso. Sem tty, o passo simplesmente nao se anuncia.
        if sys.stdout.isatty():
            threading.Thread(target=self._laco, daemon=True).start()
        else:
            self.parar = True
        return self

    def _laco(self) -> None:
        inicio, quadro = time.monotonic(), 0
        while not self.parar:
            pulso = PULSO[quadro % len(PULSO)]
            decorrido = time.monotonic() - inicio
            sys.stdout.write(f"\r  {D}{pulso} {self.texto}… {decorrido:4.1f}s{R}\033[K")
            sys.stdout.flush()
            quadro += 1
            time.sleep(0.12)

    def __exit__(self, *_) -> None:
        if not sys.stdout.isatty():
            return
        self.parar = True
        time.sleep(0.15)  # deixa o laco morrer antes de limpar, senao ele reescreve
        sys.stdout.write("\r\033[K")
        sys.stdout.flush()


class Painel:
    """Bloco de linhas que se reescreve no lugar, estilo npm install."""

    def __init__(self) -> None:
        self._altura = 0

    def desenhar(self, linhas: list[str]) -> None:
        limite = largura()
        saida = []
        if self._altura:
            saida.append(f"\033[{self._altura}A")  # volta ao topo do bloco
        for linha in linhas:
            saida.append(cortar(linha, limite) + "\033[K\n")  # \033[K limpa a sobra

        # O bloco muda de altura conforme a telemetria chega e some. Se o novo
        # for menor, as linhas antigas ficariam na tela para sempre: limpa a
        # diferenca e recua o cursor para nao contar as linhas mortas.
        if (sobra := self._altura - len(linhas)) > 0:
            saida.append("\033[K\n" * sobra)
            saida.append(f"\033[{sobra}A")

        sys.stdout.write("".join(saida))
        sys.stdout.flush()
        self._altura = len(linhas)

    def encerrar(self) -> None:
        """Solta o bloco: o proximo print vira texto normal, nao redesenho."""
        self._altura = 0


# --------------------------------------------------------------------------- #
# Ambiente
# --------------------------------------------------------------------------- #
def ler_env() -> dict[str, str]:
    """Le o .env para VALIDAR e para saber o que mascarar. Quem consome de
    verdade e o compose, via --env-file.

    Duplicar o parser aqui evita uma dependencia (python-dotenv) para uma
    checagem de cinco linhas — e o formato que o compose aceita e simples:
    KEY=valor, com aspas opcionais.
    """
    if not ENV.is_file():
        raise RuntimeError(f"{ENV} nao encontrado — copie o .env.example e preencha")

    variaveis: dict[str, str] = {}
    for linha in ENV.read_text(encoding="utf-8").splitlines():
        linha = linha.strip()
        if not linha or linha.startswith("#") or "=" not in linha:
            continue
        chave, _, valor = linha.partition("=")
        variaveis[chave.strip()] = valor.strip().strip('"').strip("'")

    if faltando := [c for c in OBRIGATORIAS if not variaveis.get(c)]:
        raise RuntimeError(f"variaveis ausentes ou vazias no .env: {', '.join(faltando)}")

    # Valores curtos demais (uma senha "123") nao entram: um replace deles
    # picotaria texto legitimo — um "123" no meio de uma porta, por exemplo.
    SEGREDOS.clear()
    SEGREDOS.extend(
        sorted(
            (v for k, v in variaveis.items() if PADRAO_SEGREDO.search(k) and len(v) >= 6),
            key=len,
            reverse=True,  # do maior para o menor: mascara o todo antes da parte
        )
    )
    return variaveis


def exigir_docker() -> str:
    proc = subprocess.run(
        ["docker", "version", "--format", "{{.Server.Version}}"],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError("Docker nao respondeu — o Docker Desktop esta aberto?")
    return proc.stdout.strip() or "?"


def semear_baseline() -> bool:
    """Planta a config completa do wizard quando nao ha nenhuma. Devolve se plantou.

    E o que dispensa o wizard num clone limpo. O delta sozinho nao basta:
    ele funde chaves numa config que precisa existir antes. Sem baseline, um
    `rm -rf hermes-data` obriga a passar por sete telas de novo — e pior, o
    delta acabaria aplicado sobre a config que a IMAGEM traz, que o proprio
    Hermes reporta como velha demais para migrar automaticamente.

    So planta se faltar. Nunca sobrescreve: depois da primeira subida, quem
    manda no config.yaml e o par (agente, delta), nao este arquivo.
    """
    global CONFIG_ORIGEM
    if hermes_configurado():
        CONFIG_ORIGEM = "reaproveitada de hermes-data"
        return False
    if not BASELINE.is_file():
        return False
    DADOS_HERMES.mkdir(parents=True, exist_ok=True)
    shutil.copy2(BASELINE, DADOS_HERMES / "config.yaml")
    CONFIG_ORIGEM = f"semeada de {BASELINE.name} {D}·{R} wizard dispensado"
    return True


def hermes_configurado() -> bool:
    """O `gateway run` exige um config.yaml. Sem ele o container entra em
    crash loop e o `restart: unless-stopped` esconde o motivo repetindo."""
    return (DADOS_HERMES / "config.yaml").is_file()


# --------------------------------------------------------------------------- #
# Docker
# --------------------------------------------------------------------------- #
def compose(*args: str, check: bool = True, interativo: bool = False):
    cmd = ["docker", "compose", "--env-file", str(ENV), "-f", str(COMPOSE), *args]
    if interativo:
        # Herda stdin/stdout do terminal: preciso para o wizard, que le teclado.
        # Herdar significa que a saida ja passou direto pela tela — nao da para
        # recapturar o erro aqui, entao so o codigo de saida sobra como sinal.
        # Ignora-lo era o que fazia um pull recusado terminar com "feito".
        proc = subprocess.run(cmd)
        if check and proc.returncode != 0:
            raise RuntimeError(
                f"`docker compose {' '.join(args)}` falhou (codigo {proc.returncode}) "
                f"— o motivo esta na saida acima"
            )
        return proc
    proc = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    if check and proc.returncode != 0:
        bruto = (proc.stderr or proc.stdout or "docker compose falhou").strip()
        raise RuntimeError(redigir(bruto)[:500])
    return proc


# "1.2GB/3.4GB", "539.9kB / 53.65MB". Deliberadamente solto: casa o par de
# tamanhos onde quer que esteja na linha, em vez de exigir o formato inteiro.
# O texto ao redor ja mudou entre versoes do Docker; o par nao. Quem converte
# cada metade e o `_bytes`, que ja sabe que o Docker mistura kB com KiB.
_TAMANHOS = re.compile(r"([\d.]+\s*[A-Za-z]*B)\s*/\s*([\d.]+\s*[A-Za-z]*B)")

# As duas fases que reportam tamanho, na ordem em que acontecem.
_FASES = (("Downloading", "baixando imagens"), ("Extracting", "extraindo camadas"))


def _par_de_bytes(texto: str) -> tuple[float, float] | None:
    """'... 539.9kB/53.65MB' -> (539900.0, 53650000.0). None se nao houver par."""
    if not (achado := _TAMANHOS.search(texto)):
        return None
    return _bytes(achado.group(1)), _bytes(achado.group(2))


# O que o registry devolve quando o problema e a rede, nao a configuracao.
# So estes reexecutam: "pull access denied", "manifest unknown" ou "no space
# left" sao definitivos, e repeti-los seria so demorar mais para dar o mesmo
# erro — pior, escondendo a causa atras de tres tentativas.
_TRANSITORIO = re.compile(
    r"TLS handshake timeout|i/o timeout|connection reset|unexpected EOF"
    r"|temporary failure|dial tcp|timeout awaiting|context deadline exceeded"
    r"|500 Internal Server Error|502 Bad Gateway|503 Service Unavailable",
    re.IGNORECASE,
)


def _rodar_narrado(pulso: Pulsando, args: tuple[str, ...]) -> tuple[int, list[str]]:
    """Uma passada do compose, narrando no pulso. Devolve (codigo, saida)."""
    cmd = ["docker", "compose", "--env-file", str(ENV), "-f", str(COMPOSE),
           "--progress", "plain", *args]
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", bufsize=1,
    )

    fases: dict[str, dict[str, tuple[float, float]]] = {f: {} for f, _ in _FASES}
    historico: list[str] = []

    for linha in proc.stdout:
        linha = redigir(linha.strip())
        if not linha:
            continue
        historico.append(linha)

        fase = next((f for f, _ in _FASES if f in linha), None)
        par = _par_de_bytes(linha) if fase else None

        if fase and par:
            fases[fase][linha.split()[0]] = par
            # A fase mais adiantada manda: assim que a primeira camada comeca a
            # extrair, o rotulo troca, mesmo que outras ainda estejam baixando.
            atual, rotulo = next((f, r) for f, r in reversed(_FASES) if fases[f])
            feito = sum(f for f, _ in fases[atual].values())
            total = sum(t for _, t in fases[atual].values())
            pulso.texto = f"{rotulo}  {_humano(feito)} / {_humano(total)}"
        elif any(fases.values()):
            # Ja houve progresso com numero: nao volta para linha solta, senao
            # o valor pisca e some a cada camada que termina.
            continue
        else:
            pulso.texto = cortar(linha, max(20, largura() - 20))

    return proc.wait(), historico


def compose_narrado(pulso: Pulsando, *args: str, tentativas: int = 3) -> None:
    """`docker compose` com a saida lida ao vivo, narrando no pulso.

    O `compose()` normal captura tudo e so devolve no fim. Serve para comando
    rapido; nao serve para o `up` depois de um `--delete`, quando ha alguns GB
    de imagem para baixar e a do MCP para construir. A tela ficava minutos
    parada com um relogio subindo — que e exatamente o que um processo travado
    tambem faz.

    Soma os bytes por CAMADA, nao por linha: o Docker reimprime a mesma camada
    a cada atualizacao, e somar as linhas contaria o mesmo download dezenas de
    vezes. Guardar o ultimo par por id faz o total so andar para frente.

    E separa download de extracao. As duas fases reportam `X/Y` no mesmo
    formato e para a MESMA camada, entao um dicionario so faria o total VOLTAR
    quando a camada recem-baixada comecasse a extrair.

    Repete quando a falha e de rede. Um `TLS handshake timeout` no fim de tres
    minutos de download derrubava a subida inteira, e a acao obvia era
    justamente aquela que o runner nao fazia: rodar de novo. O `up` reconcilia
    o que ja existe, entao repetir nao duplica nada, e as camadas ja baixadas
    ficam no cache do Docker — a segunda tentativa comeca de onde a primeira
    parou, nao do zero.

    Falha que nao e de rede sobe na primeira: insistir em "pull access denied"
    so demora mais para dar o mesmo erro, escondendo a causa.
    """
    for tentativa in range(1, tentativas + 1):
        codigo, historico = _rodar_narrado(pulso, args)
        if codigo == 0:
            return

        cauda = "\n".join(historico[-8:]) or "docker compose falhou"
        ultima = tentativa == tentativas
        if ultima or not _TRANSITORIO.search(cauda):
            quantas = f" (apos {tentativa} tentativas)" if tentativa > 1 else ""
            raise RuntimeError(f"{cauda[:500]}{quantas}")

        espera = 3 * tentativa
        pulso.texto = (f"a rede falhou, repetindo em {espera}s  "
                       f"tentativa {tentativa + 1} de {tentativas}")
        time.sleep(espera)


# .Name vem com barra na frente; e o primeiro campo para dar match por nome,
# ja que um container ainda inexistente some da saida e desalinharia a ordem.
FORMATO = (
    "{{.Name}}"
    "|{{.State.Status}}"
    "|{{if .State.Health}}{{.State.Health.Status}}{{else}}-{{end}}"
    "|{{.Config.Image}}"
    "|{{range $porta, $publica := .NetworkSettings.Ports}}"
    "{{if $publica}}{{(index $publica 0).HostPort}}->{{$porta}} {{end}}{{end}}"
    "|{{.State.StartedAt}}"
    "|{{.RestartCount}}"
)


def desde(carimbo: str) -> str:
    """Ha quanto tempo o container esta de pe.

    O Docker carimba com nanossegundos e o fromisoformat so aceita ate
    microssegundos. A fracao de segundo nao diz nada sobre uptime, entao
    sai fora inteira em vez de ser truncada.
    """
    try:
        limpo = re.sub(r"\.\d+", "", carimbo.replace("Z", "+00:00"))
        delta = datetime.now(timezone.utc) - datetime.fromisoformat(limpo)
    except (ValueError, TypeError):
        return "-"
    total = int(delta.total_seconds())
    if total < 60:
        return f"{total}s"
    if total < 3600:
        return f"{total // 60}m{total % 60:02d}s"
    return f"{total // 3600}h{(total % 3600) // 60:02d}m"


def inspecionar() -> dict[str, dict[str, str]]:
    """Estado dos dois containers numa chamada so. Ausente vira 'criando'."""
    proc = subprocess.run(
        ["docker", "inspect", "--format", FORMATO, *CONTAINERS.values()],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    achados: dict[str, dict[str, str]] = {}
    for linha in proc.stdout.splitlines():
        campos = linha.strip().split("|")
        if len(campos) < 7:
            continue
        nome, status, saude, imagem, portas, inicio, reinicios = campos[:7]
        achados[nome.lstrip("/")] = {
            "status": status,
            "saude": saude,
            "imagem": imagem,
            "portas": " ".join(portas.split()) or "—",
            "desde": desde(inicio) if status == "running" else "—",
            "reinicios": reinicios,
        }
    return {
        servico: achados.get(
            container,
            {"status": "criando", "saude": "-", "imagem": "—", "portas": "—",
             "desde": "—", "reinicios": "0"},
        )
        for servico, container in CONTAINERS.items()
    }


def pronto(info: dict[str, str]) -> bool:
    """Com healthcheck exige healthy; sem healthcheck basta running."""
    return info["status"] == "running" and info["saude"] in ("healthy", "-")


def rotulo(info: dict[str, str]) -> str:
    """O que o usuario ve na coluna de status: 'starting' e mais util que
    'running' quando o healthcheck ainda nao passou."""
    if info["status"] != "running":
        return info["status"]
    return info["saude"] if info["saude"] != "-" else "running"


# --------------------------------------------------------------------------- #
# Painel de subida
# --------------------------------------------------------------------------- #
def marca(info: dict[str, str], quadro: int) -> tuple[str, str, str]:
    """(simbolo, cor, etiqueta) do servico. A etiqueta e o que o olho pega
    primeiro: (starting) e (healthy) dizem mais que uma cor sozinha."""
    if info["status"] == "exited":
        return FALHA, VERM, "(morto)"
    if pronto(info):
        etiqueta = "(healthy)" if info["saude"] == "healthy" else "(running)"
        return OK, VERDE, etiqueta
    return GIRO[quadro % len(GIRO)], AMAR, f"({rotulo(info)})"


def titulo(nome: str, info: dict[str, str], quadro: int) -> str:
    simbolo, cor, etiqueta = marca(info, quadro)
    return (f"  {cor}{simbolo}{R} {B}{nome:<8}{R} {D}{info['imagem']}{R}  "
            f"{cor}{etiqueta}{R}")


def campo(rotulo_: str, valor: str) -> str:
    return f"      {D}{rotulo_:<10}{R}{valor}"


# Porta publicada -> (esquema, rotulo, condicao para o servico atender ali).
# A condicao existe porque publicar a porta nao garante que alguem escuta do
# outro lado: o dashboard se recusa a subir sem autenticacao, e o api_server
# fica no loopback DE DENTRO do container ate ser liberado. Nos dois casos a
# porta aceita a conexao no host e fecha em seguida.
PORTAS = {
    "9119": ("http://", "dashboard",
             lambda v: bool(v.get("HERMES_DASHBOARD_BASIC_AUTH_PASSWORD", "").strip())),
    "5432": ("", "", lambda v: True),
    "8642": ("http://", "api", lambda v: v.get("API_SERVER_HOST", "") == "0.0.0.0"),
}


def acesso(info: dict[str, str], variaveis: dict[str, str]) -> str:
    """Mapeamento de portas -> endereco utilizavel.

    `5432->5432/tcp` informa a mecanica e esconde a pergunta real, que e onde
    conectar. So aparece o que de fato atende: anunciar endereco que fecha a
    conexao e pior que nao anunciar.
    """
    enderecos = []
    for par in info["portas"].split():
        hospedeira = par.partition("->")[0]
        if hospedeira not in PORTAS:
            continue
        esquema, nome, atende = PORTAS[hospedeira]
        if not atende(variaveis):
            continue
        enderecos.append((list(PORTAS).index(hospedeira),
                          f"{esquema}localhost:{hospedeira}" + (f" {nome}" if nome else "")))
    return "  ".join(e for _, e in sorted(enderecos)) or "—"


# --------------------------------------------------------------------------- #
# INFRA
# --------------------------------------------------------------------------- #
def bloco_infra(estados: dict[str, dict], tel: Telemetria,
                variaveis: dict[str, str], quadro: int) -> list[str]:
    info = estados["postgres"]
    linhas = [titulo("INFRA", info, quadro)]
    if info["status"] != "running":
        return linhas

    banco = tel.banco
    versao = f" {D}·{R} server {banco['versao']}" if banco.get("versao") else ""
    linhas.append(campo("uptime", f"no ar ha {info['desde']}{versao}"))
    linhas.append(campo("acesso", acesso(info, variaveis)))

    mcp = estados["mcp"]
    simbolo, cor, etiqueta = marca(mcp, quadro)
    linhas.append(campo("mcp", f"{cor}{simbolo} {etiqueta}{R} {D}·{R} "
                               f"{mcp['imagem']} {D}·{R} rede interna, sem porta publicada"))

    if ETL:
        taxa = f" ({ETL['itens'] / ETL['segundos']:.0f} it/s)" if ETL["segundos"] else ""
        linhas.append(campo("etl", f"{ETL['itens']} itens da planilha {D}·{R} "
                                   f"{ETL['compostas']} unidades normalizadas {D}·{R} "
                                   f"{ETL['segundos']:.2f}s{taxa}"))
    if banco:
        linhas.append(campo("tabelas", f"despensa {banco['despensa']}   "
                                       f"precos {banco['precos']}   orcamento 1"))
        gasto = banco["orcamento_inicial"] - banco["orcamento_saldo"]
        linhas.append(campo("orcamento", f"gasto R$ {gasto:.2f}   "
                                         f"restante R$ {banco['orcamento_saldo']:.2f}"))
        conexoes = banco["conexoes"]
        linhas.append(campo("banco", f"{_humano(float(banco['tamanho']))} {D}·{R} "
                                     f"{conexoes} {'conexao' if conexoes == 1 else 'conexoes'}"))
    return linhas


# --------------------------------------------------------------------------- #
# HERMES
# --------------------------------------------------------------------------- #
def estado_gateway() -> dict:
    """Le o gateway_state.json do bind mount.

    Sai de graca: o arquivo esta no disco do host, entao nao custa um
    `docker exec` por quadro. E e a unica fonte que sabe se o Telegram esta
    conectado — o container estar `running` nao diz nada sobre isso.
    """
    alvo = DADOS_HERMES / "gateway_state.json"
    try:
        return json.loads(alvo.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def bloco_hermes(info: dict[str, str], tel: Telemetria,
                 variaveis: dict[str, str], quadro: int) -> list[str]:
    linhas = [titulo("HERMES", info, quadro)]
    if info["status"] != "running":
        return linhas

    estado = estado_gateway()
    versao = f" {D}·{R} hermes {estado['code_version']}" if estado.get("code_version") else ""
    linhas.append(campo("uptime", f"no ar ha {info['desde']}{versao}"))
    linhas.append(campo("acesso", acesso(info, variaveis)))

    if plataformas := estado.get("platforms", {}):
        partes = []
        for nome, dados in sorted(plataformas.items()):
            ligado = dados.get("state") == "connected"
            cor = VERDE if ligado else AMAR
            partes.append(f"{cor}{nome} {dados.get('state', '?')}{R}")
        linhas.append(campo("gateway", f"{estado.get('gateway_state', '?')} {D}·{R} "
                                       + f" {D}·{R} ".join(partes)))
    ag = tel.agente
    if tel.modelo:
        # O modelo em uso pode divergir do configurado: um /model no meio da
        # sessao troca so o de uso. Quando divergem, os dois aparecem.
        usado = ag.get("modelo")
        extra = f" {D}·{R} em uso {usado}" if usado and usado != tel.modelo else ""
        linhas.append(campo("modelo", f"{tel.modelo} {D}via {tel.provedor or '?'}{R}{extra}"))

    linhas.append(campo("sessoes", f"{ag.get('sessoes', 0)} {D}·{R} "
                                   f"{ag.get('mensagens', 0)} mensagens {D}·{R} "
                                   f"{ag.get('ferramentas', 0)} tool calls {D}·{R} "
                                   f"{estado.get('active_agents', 0)} ativo(s)"))

    # Zerado tambem informa: mostra que a leitura do state.db funciona e que
    # nada foi gasto ainda. Esconder a linha ate a primeira conversa faria o
    # bloco mudar de altura no primeiro "oi" e parecer que algo quebrou.
    #
    # O cache vai separado porque muda a conta: token relido de cache custa
    # uma fracao do preco de entrada, e num loop agentico ele domina o
    # volume — 359k contra 42k na primeira sessao que rodamos.
    linhas.append(campo("tokens", f"{_mil(ag.get('entrada', 0))} in {D}·{R} "
                                  f"{_mil(ag.get('saida', 0))} out {D}·{R} "
                                  f"{_mil(ag.get('cache', 0))} cache {D}·{R} "
                                  f"{ag.get('chamadas', 0)} chamadas"))
    linhas.append(campo("custo", f"US$ {ag.get('custo', 0.0):.4f} {D}estimado{R}"))
    linhas.append(campo("skills", str(len(list(DADOS_HERMES.glob("skills/**/SKILL.md"))))))
    if CONFIG_ORIGEM:
        linhas.append(campo("config", CONFIG_ORIGEM))
    if tel.divergencia:
        linhas.append(campo("divergencia",
                            f"{AMAR}o config vivo nao reflete o delta em "
                            f"{', '.join(tel.divergencia)}{R}"))
        linhas.append(campo("", f"{D}subiu por fora do runner? as ferramentas do "
                                f"agente podem estar faltando{R}"))
    if PERFIL_APLICADO:
        linhas.append(campo("perfil", f"{D}delta em {', '.join(PERFIL_APLICADO)}{R}"))
    if not variaveis.get("HERMES_DASHBOARD_BASIC_AUTH_PASSWORD", "").strip():
        linhas.append(campo("dashboard", f"{AMAR}sem senha — o Hermes recusa subir painel "
                                         f"sem autenticacao{R}"))
    if variaveis.get("TELEGRAM_BOT_TOKEN") and not variaveis.get("TELEGRAM_ALLOWED_USERS", "").strip():
        linhas.append(campo("telegram", f"{AMAR}sem allowlist — o bot vai ignorar "
                                        f"suas mensagens{R}"))
    return linhas


# --------------------------------------------------------------------------- #
# HARDWARE
# --------------------------------------------------------------------------- #
def _mil(n: float) -> str:
    """Contagem legivel de token: 359277 -> 359k."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    return f"{n / 1000:.0f}k" if n >= 1000 else str(int(n))


# Tudo que a linha gasta fora da curva: margem, seta, nome, os dois espacos
# antes do texto, o texto e o percentual. Sai daqui a largura que sobra para
# o desenho — cravar um numero faria a curva vazar ou sobrar buraco conforme
# o terminal.
_FIXO_METRICA = 2 + 1 + 1 + 5 + 2 + 1 + 8


def linha_metrica(nome: str, serie: Serie, texto: str, coluna: int, curva: int) -> str:
    valor = serie.atual
    cor = cor_faixa(valor)
    # Duas casas porque a maquina nunca esta exatamente parada: com uma casa,
    # tudo abaixo de 0,05% vira "0.0 %" e a coluna toda mente junto.
    numero = f"{valor:.2f} %" if valor is not None else "—"
    # A curva inteira na cor do valor de AGORA. Pintar cada coluna pela
    # propria leitura seria mais fiel e ilegivel: dezenas de trocas de cor por
    # linha, e o painel deixaria de ser painel.
    desenho = serie.curva(curva)
    # Alinhada a DIREITA: a amostra de agora fica cravada sempre na mesma
    # coluna e o historico cresce para tras. A esquerda, a borda do presente
    # andaria para o lado durante os tres primeiros minutos, e o olho leria
    # movimento onde so ha buffer enchendo.
    return (f"  {serie.tendencia()} {D}{nome:<5}{R}{cor}{desenho:>{curva}}{R}  "
            f"{texto:>{coluna}} {cor}{numero:>8}{R}")


def bloco_hardware(tel: Telemetria) -> list[str]:
    if tel.cpu.atual is None and tel.ram.atual is None:
        return [f"  {B}HARDWARE{R} {D}coletando…{R}"]

    metricas = [("cpu", tel.cpu, tel.cpu_texto),
                ("ram", tel.ram, tel.ram_texto),
                ("disk", tel.disco, tel.disco_texto)]
    # A GPU so entra quando ha placa identificada: uma linha "gpu —" num
    # servidor sem video seria ruido permanente.
    if tel.gpu_nome:
        metricas.append(("gpu", tel.gpu, tel.gpu_texto))

    # Coluna medida, nao fixa: "Radeon RX 7800 XT · 2.0 GB / 16.0 GB" tem o
    # dobro da largura de "4.4 GB / 930.6 GB", e um numero cravado no codigo
    # empurraria o percentual para fora do lugar em uma das duas.
    coluna = max(len(t) for _, _, t in metricas)

    # A curva come o que sobrar da linha, ate um teto de 60 — mais que isso
    # vira uma faixa larga demais para o olho seguir em tela cheia.
    #
    # E some inteira quando sobra pouco. Um piso aqui empurraria a linha para
    # fora da tela, e quem corta e o `cortar`, que corta pela direita: morreria
    # o percentual para salvar tres blocos sem forma. A curva e a parte
    # descartavel desta linha; o numero nao e.
    curva = min(60, largura() - _FIXO_METRICA - coluna)
    if curva < 6:
        curva = 0

    # As linhas voltam a ficar coladas: as colunas agora tem alturas diferentes
    # e cada serie ja se le como uma forma propria. Era a barra chapada de
    # antes, repetida identica em toda linha, que precisava do respiro.
    linhas = [f"  {B}HARDWARE{R}", ""]
    for nome, serie, texto in metricas:
        linhas.append(linha_metrica(nome, serie, texto, coluna, curva))
    return linhas


def cabecalho(versao: str) -> list[str]:
    """O banner, ou uma linha so quando ele nao cabe.

    O `cortar` do painel corta pela direita, e arte ASCII cortada pela direita
    nao degrada: vira lixo. Entao a decisao e aqui, antes de desenhar — cabe
    inteiro ou nao aparece.

    A legenda e centrada sobre a largura do banner em vez de vir com os
    espacos ja contados. A versao da engine entra no meio dela e muda de
    tamanho conforme a maquina; indentacao cravada descentraria sozinha na
    primeira maquina com outra versao do Docker.
    """
    legenda = f"{LEGENDA} · engine {versao}"
    largura_banner = max(len(l) for l in BANNER)

    if largura() < largura_banner:
        return [f"  {B}{PROJETO}{R} {D}· {legenda}{R}",
                "  " + "  ".join(f"{f} {D}{t}{R}" for f, t in COMANDOS)]

    # Arredonda para CIMA quando a sobra e impar: com 81 de banner e 40 de
    # legenda sobram 41 colunas, e meio caractere nao existe. Para cima, a
    # legenda encosta na perna direita do banner em vez de flutuar solta.
    recuo = " " * ((largura_banner - len(legenda) + 1) // 2)

    # O recuo sai do texto SEM cor. Centrar sobre a string ja pintada contaria
    # os escapes ANSI como caractere visivel e jogaria a linha para a esquerda
    # — quanto mais cor, mais torta.
    ajuda = "   ·   ".join(f"{f} {t}" for f, t in COMANDOS)
    pintada = "   ·   ".join(f"{f} {D}{t}{R}" for f, t in COMANDOS)
    recuo_ajuda = " " * max(0, (largura_banner - len(ajuda) + 1) // 2)

    return [*(f"{B}{l}{R}" for l in BANNER), "",
            f"{D}{recuo}{legenda}{R}", f"{recuo_ajuda}{pintada}"]


# --------------------------------------------------------------------------- #
def monitorar(variaveis: dict[str, str], versao: str, timeout: int = 120) -> None:
    """O painel vivo, do `up` ate o Ctrl+C. E ele que segura o terminal.

    Antes esta funcao era `docker compose logs -f`, e o resultado era ilegivel:
    o Hermes despeja dezenas de WARNING de tool indisponivel em cada boot, e
    isso enterrava a unica coisa que importa olhar. Os logs continuam a um
    comando de distancia; a dica fica no rodape.
    """
    painel = Painel()
    tel = Telemetria(variaveis)
    inicio = time.monotonic()
    quadro = 0
    estavel = False

    try:
        while True:
            estados = inspecionar()
            quadro += 1

            corpo = [
                "",
                *cabecalho(versao),
                "",
                *bloco_infra(estados, tel, variaveis, quadro),
                "",
                *bloco_hermes(estados["hermes"], tel, variaveis, quadro),
                "",
                *bloco_hardware(tel),
            ]

            if mortos := [s for s, i in estados.items() if i["status"] == "exited"]:
                painel.encerrar()
                raise RuntimeError(
                    f"{mortos[0]} morreu.\n"
                    f"    veja o motivo com:  docker logs {CONTAINERS[mortos[0]]}"
                )

            if not estavel and all(pronto(i) for i in estados.values()):
                estavel = True

            if estavel:
                rodape = ["", f"  {D}Ctrl+C derruba os dois · logs: "
                              f"docker compose -f {COMPOSE.relative_to(RAIZ)} logs -f{R}"]
            else:
                decorrido = time.monotonic() - inicio
                if decorrido > timeout:
                    painel.encerrar()
                    parados = {s: rotulo(i) for s, i in estados.items() if not pronto(i)}
                    raise RuntimeError(f"nao ficou pronto em {timeout}s: {parados}")
                pulso = PULSO[quadro % len(PULSO)]
                rodape = ["", f"  {D}{pulso} subindo… {decorrido:5.1f}s{R}"]

            painel.desenhar(corpo + rodape)

            # Enquanto sobe, o pulso precisa de quadro rapido. Depois que
            # estabiliza nao ha o que animar, e um `docker inspect` a cada
            # 120ms seria puro desperdicio.
            time.sleep(1.0 if estavel else 0.12)
    finally:
        tel.parar = True


# --------------------------------------------------------------------------- #
# Comandos
# --------------------------------------------------------------------------- #
def setup() -> None:
    """Wizard interativo do Hermes, partindo da baseline versionada.

    Semear ANTES de abrir o wizard e o que muda o significado do comando: de
    "comecar do zero" para "ajustar o que ja temos". Sem isso, num hermes-data
    vazio o container copia a config da propria imagem — que ele mesmo reporta
    como velha demais para migrar — e o wizard abre com os defaults de fabrica.
    Trocar um modelo custaria refazer as sete telas, e o resultado nem sequer
    partiria das nossas escolhas.

    Escreve em ./hermes-data, que e bind mount, entao o resultado sobrevive a
    qualquer recriacao de container.
    """
    DADOS_HERMES.mkdir(parents=True, exist_ok=True)
    print(f"\n  {B}setup do Hermes{R} {D}· o wizard roda dentro do container{R}")
    if semear_baseline():
        print(f"  {D}partindo de {BASELINE.relative_to(RAIZ)} — as telas ja vem "
              f"com as escolhas atuais{R}")
    else:
        print(f"  {D}partindo da config existente em {DADOS_HERMES.name}{R}")
    print(f"  {D}o que voce configurar fica em {DADOS_HERMES}{R}\n")

    # --no-deps: o wizard nao usa o banco. Sem isso o `depends_on` sobe o
    # postgres junto e transforma o pull dele num ponto de falha a toa.
    compose("run", "--no-deps", "--rm", "hermes", "setup", interativo=True)

    print(f"\n  {VERDE}{OK}{R} feito. Rode {B}python .docker/runner.py{R} sem flags.")
    print(f"  {D}se mudou algo que deva valer num clone limpo, a baseline "
          f"precisa ser recapturada:{R}")
    print(f"  {D}    cp {DADOS_HERMES.relative_to(RAIZ)}/config.yaml "
          f"{BASELINE.relative_to(RAIZ)}{R}\n")


def fundir(base: dict, novo: dict, prefixo: str = "") -> tuple[dict, list[str]]:
    """Merge recursivo. Devolve (resultado, caminhos alterados).

    Dicionario e fundido chave a chave; qualquer outro tipo e substituido —
    entao uma lista troca inteira, que e o que se quer para os toolsets.
    Nunca remove chave que so existe em `base`: e isso que preserva as
    centenas de defaults que o container gera.
    """
    saida, mudancas = dict(base), []
    for chave, valor in novo.items():
        caminho = f"{prefixo}.{chave}" if prefixo else chave
        if isinstance(valor, dict) and isinstance(saida.get(chave), dict):
            saida[chave], filhas = fundir(saida[chave], valor, caminho)
            mudancas += filhas
        elif saida.get(chave) != valor:
            mudancas.append(caminho)
            saida[chave] = valor
    return saida, mudancas


def instalar_arquivos_do_perfil() -> None:
    """Copia tudo que nao seja o config.yaml de hermes-profile/ para hermes-data/.

    O Hermes le SOUL.md, skills/ e agent-hooks/ do HERMES_HOME, nunca de um
    repositorio. Sem este passo, versionar esses arquivos nao teria efeito
    nenhum: eles ficariam no git e o agente continuaria com os defaults.

    Tambem planta o marcador `.no-bundled-skills`. A imagem semeia 58 skills
    a cada boot — apple-notes, spotify, manim-video, kanban — e nenhuma delas
    serve a Dona Maria. Cada uma custa superficie no system prompt e uma vaga
    no menu de comandos do Telegram, que o Hermes limita a 60: com 58 skills
    de fabrica, as NOSSAS podem simplesmente nao caber.
    """
    origem = PERFIL.parent
    if not origem.is_dir():
        return

    (DADOS_HERMES / ".no-bundled-skills").touch()

    for item in origem.iterdir():
        if item.name in (PERFIL.name, BASELINE.name):
            # Os dois arquivos de config tem caminho proprio: a baseline e
            # semeada por semear_baseline(), o delta e fundido por
            # aplicar_perfil(). Copiar qualquer um aqui so deixaria uma copia
            # morta em hermes-data, que ninguem le e todo mundo confunde.
            continue
        destino = DADOS_HERMES / item.name
        if item.is_dir():
            shutil.copytree(item, destino, dirs_exist_ok=True)
        else:
            shutil.copy2(item, destino)


def aplicar_perfil() -> None:
    """Funde o delta versionado no config.yaml do agente.

    E o que torna a instalacao reproduzivel: quem clonar o repositorio e
    rodar o runner recebe o mesmo modelo, o mesmo backend e o mesmo conjunto
    de ferramentas, sem digitar nada no wizard. O `hermes-data/` inteiro fica
    fora do git — ele guarda .env, auth.json e sessoes — entao o delta e o
    unico caminho para versionar configuracao sem versionar segredo.
    """
    if not PERFIL.is_file() or not hermes_configurado():
        return

    instalar_arquivos_do_perfil()

    try:
        import yaml
    except ImportError:
        print(f"  {AMAR}{FALHA}{R} PyYAML ausente, perfil nao aplicado "
              f"{D}(pip install -r requirements.txt){R}")
        return

    alvo = DADOS_HERMES / "config.yaml"
    atual = yaml.safe_load(alvo.read_text(encoding="utf-8")) or {}
    delta = yaml.safe_load(PERFIL.read_text(encoding="utf-8")) or {}
    fundido, mudancas = fundir(atual, delta)
    if not mudancas:
        return

    # Backup antes de escrever: um merge errado nao pode ser irreversivel.
    carimbo = datetime.now().strftime("%Y%m%d_%H%M%S")
    shutil.copy2(alvo, alvo.with_suffix(f".yaml.bak.{carimbo}"))
    alvo.write_text(
        yaml.safe_dump(fundido, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    PERFIL_APLICADO.extend(mudancas)


# --------------------------------------------------------------------------- #
# ETL da planilha
# --------------------------------------------------------------------------- #
def dsn(variaveis: dict[str, str]) -> str:
    """Conexao a partir do host. O DB_HOST do .env vale aqui — quem precisa de
    `postgres` em vez de `localhost` e o container do Hermes, nao este script."""
    return (
        f"host={variaveis['DB_HOST']} port={variaveis['DB_PORT']} "
        f"dbname={variaveis['DB_NAME']} user={variaveis['DB_USER']} "
        f"password={variaveis['DB_PASSWORD']}"
    )


def esperar_banco(variaveis: dict[str, str], timeout: int = 60):
    """O healthcheck do compose usa pg_isready, que responde antes de o banco
    aceitar conexao de verdade na primeira subida. Tentar conectar e o unico
    teste que vale."""
    import psycopg

    limite = time.monotonic() + timeout
    ultimo = ""
    while time.monotonic() < limite:
        try:
            return psycopg.connect(dsn(variaveis), connect_timeout=3)
        except psycopg.OperationalError as erro:
            ultimo = (str(erro).strip().splitlines() or [""])[0]
            time.sleep(0.5)
    raise RuntimeError(f"banco nao aceitou conexao em {timeout}s — {redigir(ultimo)}")


def ler_planilha() -> tuple[list[dict], list[dict]]:
    """As duas abas, com a unidade ja resolvida.

    A conversao vem de src/backend/domain/unidades.py e NAO e reimplementada
    aqui: o servidor MCP vai precisar da mesma regra quando casar a unidade de
    uma receita com a da despensa. Duas implementacoes seriam duas verdades
    sobre quanto custa uma alcaparra — o bug exato que estamos evitando.
    """
    import openpyxl

    sys.path.insert(0, str(RAIZ / "src" / "backend"))
    from domain.unidades import normalizar

    if not PLANILHA.is_file():
        raise RuntimeError(f"planilha nao encontrada em {PLANILHA}")

    wb = openpyxl.load_workbook(PLANILHA, data_only=True)
    if faltando := {"Despensa", "Precos"} - set(wb.sheetnames):
        raise RuntimeError(f"abas ausentes na planilha: {sorted(faltando)}")

    def linhas(aba: str, colunas: int):
        for linha in wb[aba].iter_rows(min_row=2, values_only=True):
            if linha and linha[0] and str(linha[0]).strip():
                yield (str(linha[0]).strip(), *linha[1:colunas])

    despensa = []
    for nome, qtd, unidade in linhas("Despensa", 3):
        texto = str(unidade).strip()
        base, fator = normalizar(texto)
        despensa.append({"ingrediente": nome, "quantidade": Decimal(str(qtd)),
                         "unidade": texto, "unidade_base": base, "fator": fator})

    precos = []
    for nome, qtd, unidade, preco in linhas("Precos", 4):
        texto = str(unidade).strip()
        base, fator = normalizar(texto)
        precos.append({"ingrediente": nome, "qtd_comprada": Decimal(str(qtd)),
                       "unidade": texto, "preco_pago": Decimal(str(preco)),
                       "unidade_base": base, "fator": fator})

    return despensa, precos


def carregar(variaveis: dict[str, str]) -> None:
    """Aplica o schema e carrega a planilha. Idempotente dos dois lados.

    Sobre o conflito, a escolha e diferente por tabela e de proposito:

      despensa e precos  DO UPDATE. Sao projecao da planilha, e o ETL e o
                         unico escritor. Se a regra de conversao mudar, a
                         proxima subida corrige — com DO NOTHING o balde de
                         alcaparras ficaria errado para sempre.

      orcamento          DO NOTHING. Depois da primeira carga quem manda e o
                         agente. Sobrescrever devolveria os R$ 80 e apagaria
                         tudo que a Dona Maria ja aprovou.
    """
    if not ESQUEMA.is_file():
        raise RuntimeError(f"{ESQUEMA} nao encontrado")

    relogio = time.monotonic()
    despensa, precos = ler_planilha()

    with esperar_banco(variaveis) as conexao:
        with conexao.cursor() as cur:
            cur.execute(ESQUEMA.read_text(encoding="utf-8"))

            cur.executemany(
                """
                INSERT INTO despensa (ingrediente, quantidade, unidade, unidade_base, fator)
                VALUES (%(ingrediente)s, %(quantidade)s, %(unidade)s, %(unidade_base)s, %(fator)s)
                ON CONFLICT (ingrediente) DO UPDATE SET
                    quantidade   = EXCLUDED.quantidade,
                    unidade      = EXCLUDED.unidade,
                    unidade_base = EXCLUDED.unidade_base,
                    fator        = EXCLUDED.fator,
                    carregado_em = now()
                """,
                despensa,
            )
            cur.executemany(
                """
                INSERT INTO precos (ingrediente, qtd_comprada, unidade, preco_pago,
                                    unidade_base, fator)
                VALUES (%(ingrediente)s, %(qtd_comprada)s, %(unidade)s, %(preco_pago)s,
                        %(unidade_base)s, %(fator)s)
                ON CONFLICT (ingrediente) DO UPDATE SET
                    qtd_comprada = EXCLUDED.qtd_comprada,
                    unidade      = EXCLUDED.unidade,
                    preco_pago   = EXCLUDED.preco_pago,
                    unidade_base = EXCLUDED.unidade_base,
                    fator        = EXCLUDED.fator,
                    carregado_em = now()
                """,
                precos,
            )
            cur.execute(
                """
                INSERT INTO orcamento (id, valor_inicial)
                VALUES (1, %s)
                ON CONFLICT (id) DO NOTHING
                """,
                (ORCAMENTO_INICIAL,),
            )
            resumo = cur.execute(
                """
                SELECT (SELECT count(*) FROM despensa),
                       (SELECT count(*) FROM precos),
                       (SELECT count(*) FROM precos WHERE unidade <> unidade_base),
                       (SELECT restante FROM vw_orcamento)
                """
            ).fetchone()
        conexao.commit()

    # Sem print: o resultado aparece dentro do bloco INFRA. Uma linha solta
    # acima do painel sobreviveria a todos os redesenhos e quebraria a regra
    # de que a tela tem tres blocos e mais nada.
    n_desp, n_prec, compostas, _saldo = resumo
    ETL.update(itens=n_desp + n_prec, compostas=compostas,
               segundos=time.monotonic() - relogio)


def subir(variaveis: dict[str, str], versao: str) -> None:
    global SUBIU
    SUBIU = True  # a partir daqui pode haver container de pe para derrubar
    # Depois de um `--delete` nao ha imagem nenhuma: este `up` baixa alguns GB
    # e constroi a do MCP. Por isso e o unico comando narrado do arquivo.
    with Pulsando("subindo servicos") as pulso:
        compose_narrado(pulso, "up", "-d")

    # Container sobrando de uma execucao anterior nao e recriado pelo `up -d`:
    # se estiver doente, fica doente, e a espera estoura sem dizer porque.
    if inspecionar()["postgres"]["saude"] == "unhealthy":
        print(f"  {AMAR}{FALHA}{R} postgres estava unhealthy, recriando...")
        compose("up", "-d", "--force-recreate", "postgres")

    # Antes do painel, de proposito: a carga tem saida propria e precisa poder
    # falhar com erro legivel. Dentro do painel ela viraria uma linha que
    # aparece e some, e um schema quebrado passaria despercebido.
    with Pulsando("carregando a planilha"):
        carregar(variaveis)

    monitorar(variaveis, versao)


def encerrar() -> None:
    print(f"\n  {D}derrubando os containers...{R}", flush=True)
    compose("down", check=False)
    print(f"  {VERDE}{OK}{R} derrubados {D}· volume, rede e imagens preservados{R}\n")


def confirmar(aviso: str, palavra: str) -> None:
    print(f"\n  {AMAR}{FALHA}{R} {aviso}")
    if input(f"  > para confirmar, digite '{palavra}': ").strip().lower() != palavra:
        raise SystemExit("cancelado")


def resetar() -> None:
    confirmar(f"apaga o volume {B}{VOLUME}{R} e {B}todos os dados do banco{R}.", "sim")
    compose("down", "-v", check=False)
    subprocess.run(["docker", "volume", "rm", "-f", VOLUME], capture_output=True)
    print(f"  {VERDE}{OK}{R} volume removido\n")


def remover_arvore(caminho: Path) -> list[str]:
    """Apaga a arvore e devolve o que nao saiu, em vez de levantar.

    O `onexc` nao e zelo: no Windows o unlink respeita o atributo somente-
    leitura do PROPRIO arquivo, nao a permissao do diretorio como no POSIX, e
    o Hermes deixa varios assim em `bin/` e `lazy-packages/`. Sem o retry
    depois do chmod, o rmtree morre no meio e deixa a arvore pela metade — o
    pior dos dois mundos, porque o proximo boot acha config incompleta.

    O que resta costuma ser handle preso pelo Docker Desktop, que solta
    sozinho em segundos. Por isso vira aviso, nao excecao: o resto do
    `--delete` ja foi feito e travar aqui nao desfaz nada.
    """
    restos: list[str] = []

    def insistir(func, alvo, exc) -> None:
        try:
            os.chmod(alvo, stat.S_IWRITE)
            func(alvo)
        except OSError as erro:
            restos.append(f"{alvo}: {erro.strerror or erro}")

    shutil.rmtree(caminho, onexc=insistir)
    return restos


def apagar_tudo() -> None:
    confirmar(
        f"apaga {B}containers, volume, rede, imagens{R} e {B}os dados do agente{R}\n"
        f"    {D}o banco inteiro, e {DADOS_HERMES.name}/ com config, memorias, "
        f"sessoes e o historico de conversa.{R}\n"
        f"    {D}o que volta sozinho no proximo `runner.py`: config (da baseline "
        f"versionada), SOUL, skills e hooks.{R}",
        "apagar",
    )
    # Um comando so ja cobre os quatro; os `docker ... rm` abaixo sao rede de
    # seguranca para sobras de execucoes antigas que o compose nao reconhece
    # mais como suas (renomeou servico, trocou o `name:` do projeto, etc).
    compose("down", "--volumes", "--rmi", "all", "--remove-orphans", check=False)
    subprocess.run(["docker", "volume", "rm", "-f", VOLUME], capture_output=True)
    subprocess.run(["docker", "network", "rm", REDE], capture_output=True)
    print(f"  {VERDE}{OK}{R} containers, volume, rede e imagens removidos")

    if DADOS_HERMES.exists():
        if restos := remover_arvore(DADOS_HERMES):
            print(f"  {AMAR}{FALHA}{R} {DADOS_HERMES.name}/ saiu pela metade, "
                  f"{len(restos)} item(ns) presos:")
            for resto in restos[:3]:
                print(f"      {D}{resto}{R}")
            print(f"  {D}costuma ser handle do Docker Desktop; repita em alguns "
                  f"segundos{R}")
        else:
            print(f"  {VERDE}{OK}{R} dados do agente removidos "
                  f"{D}· {DADOS_HERMES.name}/{R}")

    print(f"\n  {D}o proximo {R}{B}python .docker/runner.py{R}{D} reconstroi tudo "
          f"do zero, sem wizard:{R}")
    # as_posix() nas duas: o Path do Windows imprime com barra invertida, e
    # a linha sairia com as duas barras misturadas. Barra normal e o que se
    # digita para chamar o proprio runner.
    print(f"  {D}a config vem de {BASELINE.relative_to(RAIZ).as_posix()} e o resto de "
          f"{PERFIL.parent.relative_to(RAIZ).as_posix()}/{R}\n")


# --------------------------------------------------------------------------- #
def main() -> None:
    ap = argparse.ArgumentParser(description="Sobe o Postgres e o Hermes e segura o terminal.")
    grupo = ap.add_mutually_exclusive_group()
    grupo.add_argument("--setup", action="store_true", help="wizard do Hermes (rodar uma vez, antes)")
    grupo.add_argument("--down", action="store_true", help="derruba os containers, preserva o resto")
    grupo.add_argument("--reset", action="store_true", help="DESTROI o volume do banco e recomeca")
    grupo.add_argument("--delete", action="store_true", help="DESTROI tudo, inclusive os dados do agente")
    args = ap.parse_args()

    # ler_env() antes de tudo: e ele que carrega SEGREDOS, e sem isso um erro
    # de docker logo abaixo sairia sem mascara.
    variaveis = ler_env()
    versao = exigir_docker()

    if args.delete:
        apagar_tudo()
        return
    if args.down:
        encerrar()
        return
    if args.setup:
        setup()
        return
    if args.reset:
        resetar()

    # As duas fases sob um pulso so: semear copia um arquivo, aplicar funde
    # YAML e instala as skills. Num clone limpo isso demora o bastante para a
    # tela parecer travada — e tela parada nao distingue trabalhando de morto.
    # O resultado nao vira print: aparece no bloco HERMES do painel.
    with Pulsando("carregando config do Hermes"):
        semear_baseline()
        if hermes_configurado():
            aplicar_perfil()

    if not hermes_configurado():
        raise RuntimeError(
            "sem config e sem baseline versionada.\n"
            "    rode:  python .docker/runner.py --setup"
        )
    subir(variaveis, versao)  # bloqueia no painel ate o Ctrl+C


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print()
    except RuntimeError as e:
        print(f"\n  {VERM}{FALHA}{R} {e}\n")
        sys.exit(1)
    except SystemExit as e:
        if e.code not in (0, None):
            print(f"  {D}{e}{R}\n")
        raise
    finally:
        if SUBIU:
            encerrar()
