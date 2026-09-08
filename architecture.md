# Arquitetura — Sabor da Maria

Agente consultora de cardápio e precificação, construído sobre o **Hermes Agent**.
Modelo: **gpt-5.6-terra** pela API da OpenAI, configurado no `.env`.

---

### DIAGRAMA 1 — Visão E2E

```
╔══════════════════════════════════════════════════════════════════════╗
║ [1] FRONT — Next.js                                                  ║
║                                                                      ║
║  ┌────────────────┐  ┌──────────────────────────────────────────┐    ║
║  │  CHAT          │  │  PAINEL                                  │    ║
║  │                │  │   utensilios / tecnicas / restricoes     │    ║
║  │  conversa com  │  │   despensa: tem / falta                  │    ║
║  │  a Dona Maria  │  │   CMV e preco sugerido do prato          │    ║
║  │                │  │   orcamento: R$ 80 -> quanto sobrou      │    ║
║  └────────┬───────┘  └───────────────────┬──────────────────────┘    ║
╚═══════════╪══════════════════════════════╪═══════════════════════════╝
            │ chat (stream)                │ estado (leitura)
            v                              v
╔═══════════════════════════╗    ╔══════════════════════════════════╗
║ [2] hermes-api-server     ║    ║ [3] read-api                     ║
║     adapter do Hermes     ║    ║     so le o banco p/ o painel    ║
╚═══════════╤═══════════════╝    ╚═══════════════╤══════════════════╝
            v                                    │
╔══════════════════════════════════════════════╗ │
║ [4] HERMES AGENT                             ║ │
║     ver DIAGRAMA 2                           ║ │
╚═════╤═══════════════════════╤════════════════╝ │
      │ tools (MCP)           │ web search       │
      v                       v                  │
╔═══════════════════════╗  ┌──────────┐          │
║ [5] MCP SERVER        ║  │ INTERNET │          │
║                       ║  │ receitas │          │
║  todo calculo e toda  ║  │  reais   │          │
║  gravacao ficam aqui  ║  └──────────┘          │
║                       ║                        │
║  o LLM nao soma       ║                        │
║  o LLM nao grava      ║                        │
╚═══════════╤═══════════╝                        │
            │                                    │
            v                                    v
╔══════════════════════════════════════════════════════════╗
║ [6] POSTGRES — 4 tabelas · ver DIAGRAMA 4                ║
╚═══════════════════════▲══════════════════════════════════╝
                        │ 1x no boot
        ┌───────────────┴──────────────────────┐
        │ [7] CARGA                            │
        │  despensa_dona_maria.xlsx -> banco   │
        └──────────────────────────────────────┘
```

---

### DIAGRAMA 2 — Hermes por dentro

```
╔═══════════════════════════════════════════════════════════════════╗
║  ORQUESTRADORA — sessao principal                                 ║
║  "Consultora da Dona Maria"                                       ║
║                                                                   ║
║  conduz a conversa                                                ║
║  pergunta o que ainda nao sabe                                    ║
║  decide quando delegar                                            ║
║  explica os numeros — mas quem decide o preco e ela               ║
╚═══════════════════════════╤═══════════════════════════════════════╝
                            │
   ┌────────────────────────┴────────────────────────────────┐
   │  CONTEXT FILES        SKILLS              MEMORY        │
   │  (todo turno)         (sob demanda)       (entre        │
   │                                            sessoes)     │
   │  SOUL.md              elicitar-restricoes               │
   │   tom, jeito          pesquisar-receitas   o que a Dona │
   │                       avaliar-viabilidade  Maria ja     │
   │  AGENTS.md            precificar-prato     contou       │
   │   taxa 10%            explicar-cmv                      │
   │   orcamento R$ 80                                       │
   │   LLM nao calcula                                       │
   └─────────────────────────────────────────────────────────┘
                            │
                            │  delegate_task — nasce, faz, morre
                            v
    +- - - - - - -+  +- - - - - - -+  +- - - - - - -+  +- - - - - - -+
    ' PESQUISA    '  ' VIABILIDADE '  ' PRECO       '  ' (tracejado =
    '             '  '             '  '             '  '  sob demanda)
    ' acha        '  ' checa o que '  ' calcula CMV '  '
    ' receitas    '  ' falta p/ o  '  ' e monta os  '  '
    ' na internet '  ' prato sair  '  ' 3 cenarios  '  '
    +- - - - - - -+  +- - - - - - -+  +- - - - - - -+  +- - - - - - -+

    subagente do Hermes nasce sem contexto e herda as tools do pai.
    o que os diferencia e a SKILL e o objetivo que recebem.
```

---

### DIAGRAMA 3 — Tools (MCP)

```
╔═══════════════════════════════════════════════════════════════════╗
║  MCP SERVER — sabor-da-maria                                      ║
║                                                                   ║
║  DESPENSA                                                         ║
║    despensa()            o que tem, quanto, e o custo por kg/L    ║
║                                                                   ║
║  ELICITACAO                                                       ║
║    perfil_ler()          o que ja se sabe dela                    ║
║    perfil_gravar()       registra uma resposta                    ║
║    perfil_pendente()     o que ainda falta perguntar              ║
║                                                                   ║
║  CARDAPIO                                                         ║
║    prato_salvar()        guarda uma receita candidata             ║
║    prato_checar()        o que falta p/ esse prato sair           ║
║    prato_aceitar()       fecha o prato no cardapio                ║
║                                                                   ║
║  DINHEIRO                                                         ║
║    cmv(prato)            custo dos ingredientes do prato          ║
║    cenarios(cmv)         3 opcoes de preco com a taxa de 10%      ║
╚═══════════════════════════════════════════════════════════════════╝

  prato_aceitar() so grava se prato_checar() estiver limpo.
  a regra fica no servidor — o modelo nao consegue pular.
```

---

### DIAGRAMA 4 — Banco (4 tabelas)

```
  A PLANILHA VIRA UMA TABELA
  as duas abas tem a mesma chave (Ingrediente) e casam 1:1
  ┌──────────────────────────────────────────────────────────────┐
  │  ingredientes                                                │
  │                                                              │
  │   nome             PK    <- coluna Ingrediente               │
  │   unidade                <- coluna Unidade                   │
  │   estoque                <- aba Despensa                     │
  │   qtd_comprada           <- aba Precos                       │
  │   preco_pago             <- aba Precos                       │
  │   custo_unitario         <- preco_pago / qtd_comprada        │
  └──────────────────────────────────────────────────────────────┘

  O QUE A PLANILHA NAO TEM
  ┌──────────────────────────────┐  ┌─────────────────────────────┐
  │  perfil                      │  │  pratos                     │
  │   o que ela tem e sabe fazer │  │   o cardapio                │
  │                              │  │                             │
  │   categoria  utensilio |     │  │   id            PK          │
  │              tecnica    |    │  │   nome                      │
  │              restricao       │  │   fonte      url da receita │
  │   item       "forno"         │  │   status     sugerido |     │
  │   resposta   "nao tem"       │  │              aceito   |     │
  │   status     pendente |      │  │              recusado       │
  │              confirmado      │  │   cmv                       │
  │                              │  │   preco                     │
  └──────────────────────────────┘  └──────────────┬──────────────┘
                                                   │
                                    ┌──────────────┴──────────────┐
                                    │  pratos_ingredientes        │
                                    │   o que cada prato consome  │
                                    │                             │
                                    │   prato_id       FK         │
                                    │   ingrediente    FK         │
                                    │   quantidade                │
                                    │   comprar        true =     │
                                    │                  nao tem,   │
                                    │                  sai do     │
                                    │                  orcamento  │
                                    └─────────────────────────────┘
```
