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
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

AQUI = Path(__file__).resolve().parent
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
def aplicar_config(home: Path, dry_run: bool) -> None:
    destino = home / "config.yaml"
    if not CONFIG_LOCAL.is_file():
        print(f"  ! {CONFIG_LOCAL.name} nao encontrado — pulando config")
        return
    if not destino.is_file():
        print(f"  ! {destino} nao existe — o Hermes esta instalado?")
        return

    nosso = yaml.safe_load(CONFIG_LOCAL.read_text(encoding="utf-8")) or {}
    atual = yaml.safe_load(destino.read_text(encoding="utf-8")) or {}
    resultado, mudancas = fundir(atual, nosso)

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

    print("\nconfig.yaml")
    aplicar_config(home, args.dry_run)

    print("\nSOUL.md")
    aplicar_arquivo(SOUL_LOCAL, home / "SOUL.md", args.dry_run)

    print("\nskills/")
    aplicar_skills(home, args.dry_run)

    print("\nbusca na web")
    aplicar_busca(home, args.dry_run)

    print("\nPronto.\n" if not args.dry_run else "\nNada foi escrito.\n")


if __name__ == "__main__":
    main()
