# src/

Duas fronteiras, sem meio-termo: **`backend/` é Python, `frontend/` é TypeScript.**

```
src/
├── backend/                    Python
│   ├── domain/                 calculo puro
│   ├── mcp_server/             FastMCP — as tools do agente
│   ├── api/                    read-api — alimenta o cockpit
│   └── hermes/                 customizacoes do Hermes Agent
│       └── skills/
└── frontend/                   Next.js
```

---

## backend/domain

O núcleo determinístico: CMV, preço, viabilidade, conversão de unidade.

**Sem I/O, sem LLM, sem rede.** Funções puras que recebem dado e devolvem número.
É a única camada que precisa estar matematicamente certa, e é testável com
`pytest` sozinha — sem subir Docker, sem chamar modelo.

Todo o resto depende daqui. Nada aqui depende do resto.

## backend/mcp_server

Servidor **FastMCP** que expõe o `domain/` como ferramentas para o agente.

É a fronteira de determinismo: o LLM não soma e não grava — ele chama uma tool
e recebe o resultado pronto. As regras que não podem ser burladas (um prato só
é aceito se os requisitos estiverem confirmados) moram aqui, não no prompt.

## backend/api

**read-api** (FastAPI). Só leitura, só `SELECT`.

Projeta o estado do banco para os painéis do cockpit: despensa, elicitação,
CMV, orçamento. Não escreve nada — quem escreve é o `mcp_server/`.

## backend/hermes

As customizações do Hermes Agent — o entregável obrigatório do desafio.

| Arquivo | Papel |
| --- | --- |
| `config.yaml` | provider, `mcp_servers`, limites de delegação |
| `SOUL.md` | tom e identidade da consultora |
| `skills/` | procedimentos que o agente carrega sob demanda |
| `install.py` | copia `SOUL.md` e `skills/` para o `HERMES_HOME` |

> O `install.py` existe por um motivo: o Hermes lê `SOUL.md` e `skills/`
> **apenas** de `~/.hermes/` (ou `$HERMES_HOME`), nunca do repositório.
> Versionar os arquivos aqui não basta — eles precisam ser instalados para
> a configuração reproduzir em outra máquina.

O `AGENTS.md` é a exceção: fica na **raiz do repositório**, porque o Hermes o
procura a partir da raiz do git.

## frontend

**Next.js.** Chat + cockpit.

Zero lógica de negócio: lê da `api/` e desenha. Se precisar calcular alguma
coisa aqui, o cálculo está no lugar errado.
