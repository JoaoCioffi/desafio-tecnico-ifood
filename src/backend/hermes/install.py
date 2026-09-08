"""Aplica as customizacoes do Hermes no HERMES_HOME.

    python src/backend/hermes/install.py           # aplica
    python src/backend/hermes/install.py --dry-run # so mostra o que mudaria

Por que este script existe:

O Hermes le `SOUL.md` e `skills/` APENAS de `~/.hermes/` (ou `$HERMES_HOME`),
nunca do repositorio. E o `config.yaml` dele tem centenas de chaves default —
sobrescrever o arquivo inteiro destruiria a instalacao.

Entao aqui a gente:
  * faz MERGE das nossas chaves no config.yaml, preservando todo o resto
  * copia SOUL.md e skills/ para o HERMES_HOME
  * guarda um backup antes de qualquer escrita

Assim ninguem precisa digitar nada no terminal, e a configuracao reproduz
igual na maquina de quem clonar o repositorio.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import urllib.request

from dotenv import dotenv_values
from datetime import datetime, timezone
from pathlib import Path

import yaml

AQUI = Path(__file__).resolve().parent
RAIZ = AQUI.parents[2]  # src/backend/hermes -> raiz do repo
ENV_LOCAL = RAIZ / ".env"
CONFIG_LOCAL = AQUI / "config.yaml"
SOUL_LOCAL = AQUI / "SOUL.md"
SKILLS_LOCAL = AQUI / "skills"


# --------------------------------------------------------------------------- #
def hermes_home() -> Path:
    """Onde o Hermes guarda config, skills e memoria."""
    if env := os.environ.get("HERMES_HOME"):
        return Path(env).expanduser()
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA")
        if base and (p := Path(base) / "hermes").is_dir():
            return p
    return Path.home() / ".hermes"


def fundir(base: dict, novo: dict) -> tuple[dict, list[str]]:
    """Merge recursivo. Devolve (resultado, caminhos alterados).

    Dicionarios sao fundidos chave a chave; qualquer outro tipo e substituido.
    Nunca remove chave que so existe em `base`.
    """
    mudancas: list[str] = []

    def _rec(a: dict, b: dict, prefixo: str) -> dict:
        saida = dict(a)
        for chave, valor in b.items():
            caminho = f"{prefixo}.{chave}" if prefixo else chave
            if isinstance(valor, dict) and isinstance(saida.get(chave), dict):
                saida[chave] = _rec(saida[chave], valor, caminho)
            elif saida.get(chave) != valor:
                mudancas.append(f"{caminho}: {saida.get(chave)!r} -> {valor!r}")
                saida[chave] = valor
        return saida

    return _rec(base, novo, ""), mudancas


# --------------------------------------------------------------------------- #
# O merge PRESERVA o que ja existe — e o que salva as centenas de chaves
# default do Hermes. Mas chave que deixou de valer precisa do contrario:
# preservada, ela continua mandando na configuracao.
#
# Chave que precisa SUMIR entra aqui, e o aplicar_config apaga depois de fundir.
_REMOVER: list[tuple[str, ...]] = []


def _apagar(dados: dict, caminho: tuple[str, ...]) -> bool:
    alvo = dados
    for parte in caminho[:-1]:
        alvo = alvo.get(parte) if isinstance(alvo, dict) else None
        if not isinstance(alvo, dict):
            return False
    return alvo.pop(caminho[-1], _APAGADO) is not _APAGADO


_APAGADO = object()


def ambiente() -> dict:
    """Le o .env da raiz. Ausente ou vazio nao e erro — e o caminho local."""
    return dotenv_values(ENV_LOCAL) if ENV_LOCAL.is_file() else {}


def escolher_modelo(nosso: dict, env: dict) -> str:
    """Aponta o Hermes para a OpenAI, com o que estiver no .env.

    Sem chave nao ha instalacao valida: falha aqui, em vez de gravar um
    config que so quebra na primeira conversa.
    """
    chave = (env.get("LLM_PROVIDER_API_KEY") or "").strip()
    if not chave:
        raise SystemExit(
            f"\n  LLM_PROVIDER_API_KEY vazia em {ENV_LOCAL}.\n"
            "  Copie o .env.example, preencha a chave da OpenAI e rode de novo.\n"
        )

    modelo = nosso.setdefault("model", {})
    modelo["provider"] = env.get("LLM_PROVIDER") or "openai-api"
    modelo["default"] = env.get("LLM_MODEL") or "gpt-5.6-terra"

    # Sobra de instalacao antiga: um `base_url` local faria a chamada da
    # OpenAI sair para a porta errada, e o merge nao apaga sozinho.
    _REMOVER.extend([("model", "base_url"), ("model", "context_length")])
    return f"{modelo['provider']} · {modelo['default']}"


def gravar_chave(home: Path, env: dict, dry_run: bool) -> None:
    """Poe a chave no .env do HERMES_HOME, que e de onde ele le.

    Nunca no config.yaml: aquele arquivo tem backup a cada instalacao, e chave
    espalhada em copia e chave vazada. O .env do Hermes ja e o lugar dele.
    """
    chave = (env.get("LLM_PROVIDER_API_KEY") or "").strip()
    if not chave:
        return
    alvo = home / ".env"
    linhas = alvo.read_text(encoding="utf-8").splitlines() if alvo.is_file() else []
    linhas = [l for l in linhas if not l.startswith("OPENAI_API_KEY=")]
    linhas.append(f"OPENAI_API_KEY={chave}")
    if dry_run:
        print(f"  = OPENAI_API_KEY iria para {alvo.name}")
        return
    alvo.write_text("\n".join(linhas) + "\n", encoding="utf-8")
    print(f"  + OPENAI_API_KEY gravada em {alvo.name}  ({chave[:12]}...{chave[-4:]})")


def materializar(nosso: dict) -> dict:
    """Troca marcadores do config versionado por valores DESTA maquina.

    Duas coisas nao dao para versionar: o caminho absoluto do projeto, que o
    Hermes usa para achar o AGENTS.md, e a escolha de provider, que depende
    de haver ou nao chave de API no .env.
    """
    terminal = nosso.get("terminal")
    if isinstance(terminal, dict) and str(terminal.get("cwd", "")).strip() in ("", "."):
        if not (RAIZ / "AGENTS.md").is_file():
            print(f"  ! AGENTS.md nao esta em {RAIZ} — terminal.cwd pode estar errado")
        terminal["cwd"] = str(RAIZ)
    print(f"  = modelo: {escolher_modelo(nosso, ambiente())}")
    return nosso


def aplicar_config(home: Path, dry_run: bool) -> None:
    destino = home / "config.yaml"
    if not CONFIG_LOCAL.is_file():
        print(f"  ! {CONFIG_LOCAL.name} nao encontrado — pulando config")
        return
    if not destino.is_file():
        print(f"  ! {destino} nao existe — o Hermes esta instalado?")
        return

    nosso = materializar(yaml.safe_load(CONFIG_LOCAL.read_text(encoding="utf-8")) or {})
    atual = yaml.safe_load(destino.read_text(encoding="utf-8")) or {}
    resultado, mudancas = fundir(atual, nosso)

    for caminho in _REMOVER:
        if _apagar(resultado, caminho):
            mudancas.append(f"- {'.'.join(caminho)} removido (nao vale para este provider)")

    if not mudancas:
        print("  = config.yaml ja esta como queremos")
        return

    for m in mudancas:
        print(f"  ~ {m}")

    if dry_run:
        return

    carimbo = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    backup = destino.with_suffix(f".yaml.bak.{carimbo}")
    shutil.copy2(destino, backup)
    destino.write_text(
        yaml.safe_dump(resultado, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    print(f"  + config.yaml atualizado  (backup: {backup.name})")


def aplicar_arquivo(origem: Path, destino: Path, dry_run: bool) -> None:
    if not origem.is_file():
        print(f"  ! {origem.name} nao encontrado — pulando")
        return
    if destino.is_file() and destino.read_bytes() == origem.read_bytes():
        print(f"  = {destino.name} ja esta igual")
        return
    print(f"  + {destino.name}")
    if dry_run:
        return
    if destino.is_file():
        carimbo = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        shutil.copy2(destino, destino.with_suffix(f"{destino.suffix}.bak.{carimbo}"))
    destino.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(origem, destino)


def aplicar_busca(home: Path, dry_run: bool) -> None:
    """Garante um backend de busca sem chave no venv do Hermes.

    O requisito 2.1 do desafio exige pesquisar receitas reais na internet.
    O Hermes suporta varios backends, mas TODOS exigem chave paga
    (Exa, Tavily, Firecrawl, Perplexity, Brave) ou servidor proprio
    (SearXNG) — menos um:

        "ddgs": lambda: _ddgs_package_importable()   # tools/web_tools.py

    O `ddgs` (DuckDuckGo) nao pede chave, mas TAMBEM NAO VEM instalado com o
    Hermes: nao e dependencia nem extra. Sem ele nenhum backend fica
    disponivel e o web_search falha — em silencio, o que e pior.
    """
    py = home / "hermes-agent" / "venv" / "Scripts" / "python.exe"
    if not py.is_file():
        py = home / "hermes-agent" / "venv" / "bin" / "python"
    if not py.is_file():
        print("  ! venv do Hermes nao encontrado — pulando backend de busca")
        return

    import subprocess

    ja_tem = subprocess.run(
        [str(py), "-c", "import ddgs"], capture_output=True
    ).returncode == 0
    if ja_tem:
        print("  = ddgs ja instalado (web_search sem chave)")
        return

    print("  + ddgs — backend de busca sem chave para o web_search")
    if dry_run:
        return

    uv = home / "bin" / ("uv.exe" if sys.platform == "win32" else "uv")
    cmd = (
        [str(uv), "pip", "install", "ddgs"]
        if uv.is_file()
        else [str(py), "-m", "pip", "install", "ddgs"]
    )
    r = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        env={**os.environ, "VIRTUAL_ENV": str(py.parent.parent)},
    )
    if r.returncode != 0:
        print(f"    ! falhou: {(r.stderr or r.stdout).strip().splitlines()[-1:]}")
        print(f"    instale manualmente: {' '.join(cmd)}")


def conferir_ferramentas(nosso: dict) -> None:
    """Avisa se o MCP serve ferramenta que o config nao declara.

    O Hermes filtra pelo `tools.include`: ferramenta fora dessa lista existe no
    servidor e e INVISIVEL para o agente. Aconteceu com a `ingrediente_preco` —
    escrita, testada e exposta pelo MCP, e o agente passou uma rodada inteira
    improvisando com `perfil_gravar` porque nunca a enxergou.

    Nao ha erro em lugar nenhum quando isso acontece: o agente simplesmente usa
    outra coisa. Dai a conferencia.
    """
    declaradas = set(
        (((nosso.get("mcp_servers") or {}).get("sabor-da-maria") or {}).get("tools") or {}).get(
            "include"
        )
        or []
    )
    if not declaradas:
        return
    try:
        with urllib.request.urlopen("http://127.0.0.1:9000/health", timeout=2) as r:
            servidas = set(json.load(r).get("ferramentas") or [])
    except Exception:
        print("  = MCP fora do ar — nao deu para conferir a lista de ferramentas")
        return

    fora = sorted(servidas - declaradas)
    fantasma = sorted(declaradas - servidas)
    if fora:
        print(f"  ! o MCP serve, mas o config NAO declara: {', '.join(fora)}")
        print("    o agente nao enxerga essas — inclua em mcp_servers.tools.include")
    if fantasma:
        print(f"  ! o config declara, mas o MCP nao serve: {', '.join(fantasma)}")
    if not fora and not fantasma:
        print(f"  = {len(declaradas)} ferramentas declaradas, todas servidas")


def aplicar_skills(home: Path, dry_run: bool) -> None:
    if not SKILLS_LOCAL.is_dir():
        print("  ! skills/ nao encontrado — pulando")
        return

    destino_raiz = home / "skills"
    nossas = [d for d in SKILLS_LOCAL.iterdir() if d.is_dir()]
    if not nossas:
        print("  = nenhuma skill para instalar")
        return

    for skill in nossas:
        destino = destino_raiz / skill.name
        # Substitui a skill inteira: ela e nossa, versionada no repo.
        # As skills do proprio Hermes ficam em outras pastas e nao sao tocadas.
        if destino.is_dir():
            print(f"  ~ skills/{skill.name}")
            if not dry_run:
                shutil.rmtree(destino)
        else:
            print(f"  + skills/{skill.name}")
        if not dry_run:
            shutil.copytree(skill, destino)


# --------------------------------------------------------------------------- #
def main() -> None:
    ap = argparse.ArgumentParser(description="Aplica as customizacoes do Hermes.")
    ap.add_argument("--dry-run", action="store_true", help="so mostra o que mudaria")
    args = ap.parse_args()

    home = hermes_home()
    print(f"\nHERMES_HOME: {home}")
    if not home.is_dir():
        raise SystemExit(
            "  ERRO: HERMES_HOME nao encontrado.\n"
            "  Instale o Hermes primeiro: https://hermes-agent.nousresearch.com"
        )
    if args.dry_run:
        print("(dry-run — nada sera escrito)")

    print("\nchave de API")
    gravar_chave(home, ambiente(), args.dry_run)

    print("\nconfig.yaml")
    aplicar_config(home, args.dry_run)

    print("\nSOUL.md")
    aplicar_arquivo(SOUL_LOCAL, home / "SOUL.md", args.dry_run)

    print("\nskills/")
    aplicar_skills(home, args.dry_run)

    print("\nbusca na web")
    aplicar_busca(home, args.dry_run)

    print("\nferramentas")
    conferir_ferramentas(
        materializar(yaml.safe_load(CONFIG_LOCAL.read_text(encoding="utf-8")) or {})
    )

    print("\nPronto.\n" if not args.dry_run else "\nNada foi escrito.\n")


if __name__ == "__main__":
    main()
