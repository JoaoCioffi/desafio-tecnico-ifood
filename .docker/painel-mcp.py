"""
Dashboard do servidor MCP. Aba separada, nao concorre com o runner.

    python .docker/painel-mcp.py

So LE. Nao sobe nada, nao derruba nada, e um Ctrl+C aqui nao encosta nos
containers — de proposito: a aba do runner e quem manda no ciclo de vida, e
duas coisas com poder de derrubar a stack e uma a mais.

O QUE ELE MOSTRA

O trabalho do MCP: qual ferramenta foi chamada, por qual dos dois agentes, com
que FORMA de entrada e saida, quanto demorou, e o que o gate decidiu.

Forma, nao conteudo. Um payload vira `{5}`, uma lista vira `[7]`. As saidas
variam demais para caber num molde narrativo — um painel que tenta contar a
historia ("propondo receita de frango com...") vira leitura diagonal, e o que
se quer aqui e a silhueta do sistema trabalhando. Como consequencia, nada do
que a Dona Maria escreve aparece na tela: da para deixar aberto durante a
gravacao sem pensar duas vezes.

DE ONDE VEM O DADO

Do `/eventos` do proprio MCP, publicado em 127.0.0.1. O servidor guarda as
ultimas chamadas num anel em memoria; este painel manda o ultimo `seq` que ja
tem e recebe so o delta. Painel aberto ha uma hora custa o mesmo que um
recem-aberto.

O desenho reaproveita o runner: `Painel`, `cortar` e `Serie` sao de la. Aquele
redesenho tem detalhe suficiente — cortar na largura antes de imprimir, contar
linha para subir o cursor, limpar sobra quando o bloco encolhe — para uma copia
divergir na primeira mudanca.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

AQUI = Path(__file__).resolve().parent

# Importado pelo caminho, e nao por `import runner`: o arquivo tem hifen no
# vizinho e o diretorio nao e pacote. Nada acontece no import — o runner so
# define constantes ate o `main()`.
_spec = importlib.util.spec_from_file_location("runner", AQUI / "runner.py")
rn = importlib.util.module_from_spec(_spec)
sys.modules["runner"] = rn
_spec.loader.exec_module(rn)

Painel, Serie, cortar, largura = rn.Painel, rn.Serie, rn.cortar, rn.largura
B, D, R, VERDE, VERM, AMAR = rn.B, rn.D, rn.R, rn.VERDE, rn.VERM, rn.AMAR
OK, FALHA, PONTO, CORTE = rn.OK, rn.FALHA, rn.PONTO, rn.CORTE
PULSO, ANSI = rn.PULSO, rn.ANSI

URL = "http://127.0.0.1:8765/eventos"

BANNER = r"""
  __  __  ____ ____    ____            _          __
 |  \/  |/ ___|  _ \  |  _ \  __ _ ___| |__    _  \ \
 | |\/| | |   | |_) | | | | |/ _` / __| '_ \  (_)  | |
 | |  | | |___|  __/  | |_| | (_| \__ \ | | |  _   | |
 |_|  |_|\____|_|     |____/ \__,_|___/_| |_| (_)  | |
                                                  /_/
"""[1:-1].split("\n")

LARGURA_BANNER = max(len(l) for l in BANNER)

# Cor por agente. Sao tres papeis e nao dois: o gate nao e um agente, e a
# distincao importa mais que a simetria — e a unica linha do painel que mostra
# uma decisao que o modelo nao pode tomar.
COR_DE_QUEM = {"maria": VERDE, "cliente": AMAR, "gate": VERM}

# Reservado para o rodape e a margem. Todo o RESTO e medido: os blocos sao
# montados primeiro e o FLUXO fica com o que sobrar. Um numero cravado aqui
# erra nos dois sentidos — some com o fluxo quando a ULTIMA tem poucos campos,
# e vaza a tela quando tem muitos. Vazar e o pior dos dois: uma linha que
# embrulha desalinha a conta de "subir N linhas" do redesenho.
_RESERVA = 4


def secao(nome: str, direita: str = "") -> str:
    esq = f"  {B}{nome}{R}"
    visivel = len(ANSI.sub("", esq)) + len(direita)
    return esq + " " * max(1, largura() - visivel - 2) + f"{D}{direita}{R}"


def campo(rotulo: str, valor: str) -> str:
    return f"      {D}{rotulo:<9}{R}{valor}"


def buscar(desde: int) -> dict | None:
    """Uma leitura do /eventos. None quando o MCP nao respondeu.

    Timeout curto de proposito: o painel redesenha a cada segundo, e uma leitura
    que demora mais que isso congela a animacao — melhor perder o quadro e
    dizer que perdeu.
    """
    try:
        with urllib.request.urlopen(f"{URL}?desde={desde}", timeout=2) as resposta:
            return json.loads(resposta.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return None


def _duracao(segundos: float | None) -> str:
    if segundos is None:
        return "—"
    if segundos < 60:
        return f"{segundos:.0f}s"
    if segundos < 3600:
        return f"{int(segundos // 60)}m{int(segundos % 60):02d}s"
    return f"{int(segundos // 3600)}h{int(segundos % 3600 // 60):02d}m"


def _ms(valor: float) -> str:
    return f"{valor:.0f}ms" if valor < 1000 else f"{valor / 1000:.1f}s"


def bloco_servidor(servidor: dict, atrasado: bool) -> list[str]:
    if atrasado:
        estado = f"{AMAR}sem resposta{R}"
    else:
        estado = f"{VERDE}ok{R}"
    linhas = [secao("SERVIDOR")]
    linhas.append(campo("saude", f"{estado} {D}·{R} no ar ha {_duracao(servidor.get('uptime'))}"))
    pool = servidor.get("pool_em_uso")
    linhas.append(campo("pool", f"{pool}/{servidor.get('pool_max')} conexoes ao postgres"))

    total = servidor.get("chamadas", 0)
    erros = servidor.get("erros", 0)
    cor_erro = VERM if erros else D
    linhas.append(campo("chamadas", f"{total} total {D}·{R} {cor_erro}{erros} erro{R} "
                                    f"{D}·{R} ultima ha {_duracao(servidor.get('ultima_ha'))}"))

    # O gate ganha linha propria mesmo zerado. Ele e a garantia central da
    # entrega, e uma linha que aparece so quando dispara nao prova que existe.
    bloqueios = servidor.get("gate_bloqueios", 0)
    cor = VERM if bloqueios else D
    linhas.append(campo("gate", f"{servidor.get('gate_total', 0)} avaliacoes "
                                f"{D}·{R} {cor}{bloqueios} bloqueio(s){R}"))
    return linhas


def linha_evento(e: dict) -> list[str]:
    quem = e["quem"]
    cor = COR_DE_QUEM.get(quem, "")
    hora = datetime.fromtimestamp(e["ts"]).strftime("%H:%M:%S.%f")[:-3]

    if e["estado"] == "ok":
        marca, sufixo = f"{D}{PONTO}{R}", ""
    elif e["estado"] == "erro":
        marca, sufixo = f"{VERM}{FALHA}{R}", f"  {VERM}ERRO{R}"
    else:
        marca, sufixo = f"{AMAR}{FALHA}{R}", f"  {AMAR}{e['estado']}{R}"

    cabeca = (f"   {D}{hora}{R}  {cor}{quem:<8}{R}{marca} "
              f"{e['tool']:<28}{_ms(e['ms']):>6}{sufixo}")

    if e.get("detalhe"):
        corpo = f"{D}{e['detalhe']}{R}"
    else:
        corpo = (f"{D}in{R} {e['entrada']:<9} {D}out{R} {e['saida']:<7} "
                 f"{D}·{R} {rn._humano(e['bytes']) if e['bytes'] >= 1024 else str(e['bytes']) + ' B'}")
    return [cabeca, f"                          {corpo}"]


def bloco_fluxo(eventos: list[dict], quantos: int) -> list[str]:
    linhas = [secao("FLUXO", "mais recente embaixo  ")]
    if not eventos:
        return linhas + [f"      {D}nenhuma chamada ainda — fale com um dos bots{R}"]
    for e in eventos[-quantos:]:
        linhas += linha_evento(e)
    return linhas


def bloco_ultima(eventos: list[dict]) -> list[str]:
    if not eventos:
        return []
    e = eventos[-1]
    cabecalho = f"{e['tool']} {PONTO} {e['quem']} {PONTO} {_ms(e['ms'])}  "
    linhas = [secao("ULTIMA", cabecalho)]

    # Uma largura para entrada E saida. Calculada por grupo, as duas colunas de
    # valor caiam em posicoes diferentes e o bloco ficava torto — e o ponto
    # deste bloco e justamente comparar o que entrou com o que saiu.
    todos = (e.get("campos_entrada") or []) + (e.get("campos_saida") or [])
    largura_chave = max((len(str(k)) for k, _ in todos), default=0)

    def despejar(rotulo: str, pares: list) -> None:
        if not pares:
            linhas.append(campo(rotulo, f"{D}—{R}"))
            return
        for i, (chave, valor) in enumerate(pares[:6]):
            nome = rotulo if i == 0 else ""
            linhas.append(f"      {D}{nome:<9}{R}{chave:<{largura_chave + 2}}{valor}")
        if len(pares) > 6:
            linhas.append(f"      {'':<9}{D}… mais {len(pares) - 6}{R}")

    despejar("entrada", e.get("campos_entrada") or [])
    despejar("saida", e.get("campos_saida") or [])
    return linhas


def bloco_agregado(agregado: list[dict], series: dict[str, Serie]) -> list[str]:
    linhas = [secao("AGREGADO", "desde a subida  ")]
    if not agregado:
        return linhas
    linhas.append(f"      {D}{'ferramenta':<26}{'n':>4}  {'p50':>7}  {'max':>7}"
                  f"  {'erro':>4}   latencia{R}")
    for item in agregado[:8]:
        serie = series.setdefault(item["tool"], Serie())
        # A curva vem do agregado, nao de amostragem local: o painel pode ter
        # aberto depois: reencher a serie a cada quadro deixa a forma correta
        # mesmo para quem chegou tarde.
        serie._valores.clear()
        for v in item["latencias"][-40:]:
            serie.anotar(v)
        erros = item["erros"]
        cor = VERM if erros else D
        linhas.append(
            f"      {item['tool']:<26}{item['n']:>4}  {_ms(item['p50']):>7}  "
            f"{_ms(item['max']):>7}  {cor}{erros:>4}{R}   {serie.curva(24)}"
        )
    return linhas


def cabecalho(servidor: dict, altura: int) -> list[str]:
    """O banner, ou uma linha so quando ele nao cabe.

    Mesma regra do runner, pelo mesmo motivo: arte ASCII cortada nao degrada,
    vira lixo. Aqui a altura pesa tanto quanto a largura — o banner sao seis
    linhas, e num terminal de 20 elas custam tres chamadas do FLUXO, que e o
    que o painel existe para mostrar.
    """
    legenda = (f"sabor-da-maria {PONTO} mcp {PONTO} "
               f"{servidor.get('ferramentas', '?')} ferramentas")
    if altura < 30 or largura() < LARGURA_BANNER:
        return [f"  {B}MCP{R} {D}{PONTO} {legenda}{R}"]
    recuo = " " * max(0, (LARGURA_BANNER - len(legenda) + 1) // 2)
    return [*(f"{B}{l}{R}" for l in BANNER), "", f"{D}{recuo}{legenda}{R}"]


def encolher(cabeca: list[str], fluxo: list[str], fim: list[str],
             teto: int) -> list[str]:
    """Garante que o quadro CABE, cortando na ordem do que menos custa perder.

    Nao e estetica. O redesenho sobe N linhas para reescrever no lugar, e esse N
    e a contagem do quadro anterior; se o quadro nao coube, o terminal rolou e a
    conta passa a apontar para o lugar errado — o painel comeca a se desenhar
    por cima de si mesmo. Um `max(1, ...)` na estimativa nao evita isso, so
    limita o tamanho do estrago.

    A ordem de sacrificio: primeiro o AGREGADO, que continua util truncado —
    ele ja vem ordenado pela ferramenta mais chamada, entao o que cai e a cauda.
    Depois o FLUXO, perdendo as chamadas mais antigas. A ULTIMA e o que menos
    encolhe: e o unico bloco com profundidade, e sem ele o painel vira uma lista
    de nomes.
    """
    def total() -> int:
        return len(cabeca) + len(fluxo) + len(fim)

    # O AGREGADO e a cauda de `fim`; o piso de 4 preserva a ULTIMA e o titulo
    # da secao, senao o corte comeria o cabecalho e sobraria numero sem rotulo.
    while total() > teto and len(fim) > 4:
        fim = fim[:-1]
    # Cada chamada sao duas linhas, e a primeira do bloco e o titulo da secao.
    while total() > teto and len(fluxo) > 4:
        fluxo = fluxo[:2] + fluxo[4:]

    # Corte incondicional. Abaixo de ~20 linhas nada mais cabe — SERVIDOR
    # sozinho sao cinco — e o painel fica feio. Feio e recuperavel: basta o
    # usuario aumentar a janela. Rolar a tela nao e: o redesenho passa a subir
    # o numero errado de linhas e o painel se escreve por cima de si mesmo ate
    # alguem matar o processo.
    return (fluxo + fim)[: max(0, teto - len(cabeca))]


def main() -> int:
    # A declaracao vem antes de qualquer LEITURA de URL nesta funcao: usar e
    # depois declarar global e erro de sintaxe, nao de execucao — o arquivo nem
    # carrega.
    global URL
    ap = argparse.ArgumentParser(description="Dashboard do servidor MCP (somente leitura).")
    ap.add_argument("--url", default=URL, help=f"endpoint de eventos (padrao: {URL})")
    URL = ap.parse_args().url

    painel = Painel()
    eventos: list[dict] = []
    series: dict[str, Serie] = {}
    servidor: dict = {}
    agregado: list[dict] = []
    ultimo_seq = 0
    falhas = 0
    quadro = 0

    try:
        while True:
            quadro += 1
            dados = buscar(ultimo_seq)
            if dados is None:
                falhas += 1
            else:
                falhas = 0
                servidor = dados["servidor"]
                agregado = dados["agregado"]
                novos = dados["eventos"]
                if novos:
                    eventos.extend(novos)
                    ultimo_seq = novos[-1]["seq"]
                    # Teto igual ao do anel do servidor: guardar mais aqui nao
                    # traz nada, porque o que ficou para tras nao volta.
                    eventos = eventos[-300:]
                elif servidor.get("seq", 0) < ultimo_seq:
                    # O `seq` do servidor andou para TRAS: o MCP reiniciou e o
                    # anel comecou do zero. Sem isto o painel congelaria pedindo
                    # eventos acima de um contador que nao existe mais.
                    eventos, ultimo_seq, series = [], 0, {}

            altura = shutil.get_terminal_size((100, 40)).lines
            corpo = ["", *cabecalho(servidor, altura), "",
                     *bloco_servidor(servidor, falhas > 0)]

            # Estimativa para o primeiro corte, garantia logo abaixo. Duas
            # linhas por chamada.
            fim = (["", *bloco_ultima(eventos)]
                   + ["", *bloco_agregado(agregado, series)])
            sobra = altura - len(corpo) - len(fim) - _RESERVA - 2
            fluxo = ["", *bloco_fluxo(eventos, max(1, sobra // 2))]
            corpo += encolher(corpo, fluxo, fim, altura - _RESERVA)

            if falhas:
                pulso = PULSO[quadro % len(PULSO)]
                rodape = ["", f"  {AMAR}{pulso} sem resposta do MCP ha {falhas}s "
                              f"— a stack esta de pe?{R}"]
            else:
                rodape = ["", f"  {D}Ctrl+C sai {PONTO} este painel so LE "
                              f"{PONTO} quem manda na stack e o runner{R}"]

            painel.desenhar(corpo + rodape)
            time.sleep(1.0)
    except KeyboardInterrupt:
        painel.encerrar()
        print(f"\n  {D}painel fechado {PONTO} nada foi derrubado{R}\n")
        return 0


if __name__ == "__main__":
    sys.exit(main())
