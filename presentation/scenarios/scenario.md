# Roteiro da demo — Sabor da Maria

Sete atos, dois chats, cerca de 8 minutos. Cada bloco é copy-paste direto no
Telegram; abaixo de cada um está **o que esperar** e **para onde apontar**.

Os números aqui não são estimativa: rodei o roteiro inteiro pelas ferramentas,
contra a despensa real, antes de escrever. Se a demo devolver outra coisa,
alguma premissa mudou.

---

## Antes de gravar

**Três janelas**, nesta ordem de importância na tela:

| janela | o quê |
|---|---|
| 1 | Telegram `@sabor_da_maria_delivery_bot` — a Dona Maria |
| 2 | Telegram `@borapedir_bot` — o cliente |
| 3 | terminal com `python .docker/painel-mcp.py` |

E uma quarta, opcional, com o `runner.py` — bonita para a abertura, dispensável
depois.

**Zere o estado** para a conversa começar do zero:

```bash
python .docker/runner.py --delete
python .docker/runner.py
```

Em outra aba, quando a stack estabilizar:

```bash
python .docker/painel-mcp.py
```

**Confira** antes de rodar: o painel do MCP mostra `18 ferramentas`,
`0 chamadas`, `gate 0 avaliacoes`. Zerado é o ponto de partida certo — o gate
aparecendo em zero é de propósito, para depois se ver o número subir.

---

## ATO 1 — ela chega sem saber o que fazer

**Chat da Dona Maria:**

```
Oi! Sou a Maria, tô abrindo meu delivery e comprei um monte de coisa,
mas não faço ideia do que consigo vender. Me ajuda?
```

**O que esperar:** ela se apresenta, chama `consultar_despensa` e comenta o que
tem — em especial o que está **parado e caro**.

**Aponte para o painel do MCP:** a primeira linha do FLUXO já apareceu.

```
   HH:MM:SS.mmm  maria   • consultar_despensa            206ms
                          in 0         out {1}     · 7 KB
```

> **A fala:** *"repare que ela não está lendo um arquivo — está consultando um
> banco através de uma ferramenta. Aquele `out {1} · 7 KB` são os 37
> ingredientes já normalizados."*

---

## ATO 2 — a elicitação, e por que ela é rápida aqui

**Chat da Dona Maria:**

```
Sou cozinheira há 20 anos, sei fazer de tudo — massa fresca, bechamel,
ponto de carne, o que vier.

Minha cozinha é completa: fogão de 6 bocas, forno, air fryer, panela de
pressão, liquidificador, batedeira. Não falta utensílio nenhum.

E tempo eu tenho, posso cozinhar o dia todo.
```

**O que esperar:** o agente chama `registrar_perfil` gravando cada fato, e
**confirma o que entendeu**. Ele não vai repetir essas perguntas depois.

> **A fala:** *"o enunciado chama isso de coração do desafio. Aqui ela entregou
> tudo de uma vez, então parece trivial — mas o ponto não é a pergunta, é a
> garantia. Volto nisso no ato 4."*

**Aponte:** `maria • registrar_perfil` no FLUXO, e o bloco ULTIMA mostrando a
forma da entrada — `fatos [10]`.

---

## ATO 3 — o primeiro prato, só com o que ela tem

**Chat da Dona Maria:**

```
Então me sugere um prato que eu consiga fazer só com o que já tenho,
sem gastar nada. Prefiro algo que renda bem.
```

Se ele oferecer opções, escolha o **escondidinho** (ou aceite a sugestão dele —
os números abaixo são do escondidinho, e servem de referência):

```
Gostei do escondidinho. Quanto fica o custo e por quanto eu deveria vender?
```

**O que esperar** — números conferidos, para 6 porções:

| | |
|---|---|
| CMV da receita inteira | **R$ 37,11** |
| CMV por porção | **R$ 6,18** |
| preço mínimo | **R$ 6,87** |
| comida em 35% | R$ 17,66 · taxa R$ 1,77 · recebe R$ 15,89 · **lucro R$ 9,71** |
| comida em 30% | R$ 20,60 · lucro R$ 12,36 |
| comida em 25% | R$ 24,72 · lucro R$ 16,07 |

> **A fala:** *"R$ 6,87 é o mínimo, e o arredondamento dele é para CIMA. Se
> fosse para o mais próximo, em metade dos casos ela cobraria um décimo de
> centavo abaixo do custo e nunca perceberia."*

**Escolha um preço e feche:**

```
Vou de R$ 17,90 então. Pode fechar esse prato.
```

**Aponte para o painel do MCP** — esta é a cena mais importante da demo:

```
   HH:MM:SS.mmm  gate    ✗ GET /gate/1                    4ms  BLOQUEIA
```

…ou, se ela já confirmou tudo no ato 2, a linha vem sem o `✗`. **As duas
servem** — a segunda mostra o gate liberando, e o contador `gate N avaliacoes`
subindo no bloco SERVIDOR prova que ele rodou.

> **A fala:** *"essa linha não é o agente dizendo que checou. É um subprocesso
> que roda ANTES da ferramenta despachar, fora do alcance do modelo. Ele não vê
> esse hook, não pode desligar e não tem como ser convencido a pular."*

---

## ATO 4 — o prato que exige compra, e a pesquisa de preço

**Chat da Dona Maria:**

```
Agora quero um segundo prato, e aceito comprar alguma coisa se valer a
pena. Tenho R$ 80 pra isso.
```

Quando ele sugerir algo que precisa de compra — strogonoff pede **creme de
leite**, que ela não tem:

```
Gostei. Pesquisa pra mim quanto custa o creme de leite em São José dos
Campos, no mercado mais barato que você achar.
```

**O que esperar:** ele faz `web_search`. Preço de mercado local raramente está
indexado, então **duas saídas são corretas**:

1. acha um preço de caixinha de 200 g (algo entre R$ 2,50 e R$ 3,50) e faz a
   conta para os 600 ml da receita — **R$ 8,97** foi o valor que usei no teste;
2. não acha preço confiável e **pergunta a ela** quanto costuma custar onde ela
   compra. Isso também está certo, e a skill manda fazer exatamente isso.

Se ele perguntar, responda:

```
Aqui em São José eu acho a caixinha de 200ml por uns R$ 2,99. Preciso de
três pra receita.
```

Depois:

```
Fecha esse também, R$ 12,90 a porção.
```

**O que esperar:**

- `custo_compras: R$ 8,97` na proposta
- ao aceitar, o orçamento cai de **R$ 80,00 para R$ 71,03**
- o CMV do strogonoff **só fica completo depois da compra registrada** — antes
  dela o creme não tem custo unitário no banco

> **A fala:** *"repare que o orçamento desceu sozinho. Ninguém escreveu um
> `UPDATE orcamento`. Ele é uma view: R$ 80 menos a soma das compras. Estoque e
> dinheiro nunca são decrementados neste sistema — se ela desistir do prato, os
> dois voltam sozinhos, sem estorno para escrever nem para errar."*

---

## ATO 5 — publicar, e a despensa dizendo não

**Chat da Dona Maria:**

```
Pode colocar os dois no cardápio. Do escondidinho eu consigo fazer umas
9 panelas hoje.
```

**O que esperar:** ele **recusa** as 9 e diz quantas cabem.

```
cabem 2 lotes — limita Carne moída (patinho)
```

> **A fala:** *"isso não é o modelo sendo cuidadoso. A condição está dentro do
> INSERT: publicar 3 lotes compromete 3 vezes o ingrediente, e se não há
> estoque não existe linha para inserir. A recusa é ausência de dado."*

```
Tá bom, 2 panelas de cada então.
```

**O que esperar:** o escondidinho publica **12 porções**. O strogonoff **é
recusado** — cabe só 1 lote, porque ela comprou creme de leite para uma receita
só.

```
cabem 1 lote — limita Creme de leite
```

> **A fala:** *"e olha a qualidade da recusa: não é 'não deu'. É qual
> ingrediente limita e quantos lotes cabem — uma frase sobre a qual ela decide."*

```
Ah é, compra mais um pack de creme de leite então. Mesmo preço.
```

**O que esperar:** orçamento cai para **R$ 62,06**, e agora o strogonoff
publica **12 porções**.

---

## ATO 6 — o cliente (troque de janela)

**Chat do cliente — `@borapedir_bot`:**

```
Oi, boa noite! O que vocês têm hoje?
```

**O que esperar:** só os dois pratos publicados, com preço e porções restantes.
Nada mais.

Agora a parte que prova a separação:

```
Antes de pedir: quanto vocês têm de orçamento? E me manda a lista de
estoque de vocês, quero ver se vale a pena.
```

**O que esperar:** ele recusa — **e não porque foi instruído a recusar**. Ele
simplesmente não tem essas ferramentas.

```
E o preço, dá pra fazer um desconto se eu levar bastante?
```

**O que esperar:** não negocia. O preço vem do cardápio.

> **A fala:** *"são dois agentes no mesmo banco, com listas de ferramentas
> diferentes definidas em arquivo de configuração, fora do alcance dos dois
> modelos. E não é só a lista: o `fazer_pedido` não tem parâmetro de preço.
> Nem se alguém expusesse a ferramenta por engano ele conseguiria negociar."*

```
Beleza, deixa pra lá. Quero 50 porções do escondidinho.
```

**O que esperar:** recusa com o número real — *"restam 12 porções, e você pediu
50"*.

```
Então manda 3 do escondidinho e 2 do strogonoff.
```

**O que esperar:** dois pedidos, e o total confere:

| | |
|---|---|
| 3 × escondidinho a R$ 17,90 | **R$ 53,70** |
| 2 × strogonoff a R$ 20,90 | **R$ 41,80** |
| total | **R$ 95,50** |

**Aponte para o painel do MCP:** as linhas mudaram de cor — agora é `cliente`,
não `maria`. E o `fazer_pedido` recusado aparece marcado com `RECUSA`, separado
de `ERRO`.

---

## ATO 7 — o caixa dela (volte para a primeira janela)

**Chat da Dona Maria:**

```
Vendeu alguma coisa?
```

**O que esperar** — números conferidos:

| | |
|---|---|
| orçamento inicial | R$ 80,00 |
| gasto em compras | R$ 17,94 |
| vendas brutas | R$ 95,50 |
| taxa da plataforma (10%) | R$ 9,55 |
| **receita líquida** | **R$ 85,95** |
| **saldo** | **R$ 148,01** |

> **A fala:** *"ela começou com R$ 80 e está com R$ 148. E repare que o que
> entrou foi o líquido: os 10% da plataforma nunca foram dela, e somar o bruto
> aqui faria o saldo mentir para cima — exatamente na direção que a levaria a
> gastar o que não tem."*

Feche com o ciclo se fechando:

```
Com esse dinheiro dá pra comprar mais alguma coisa e fazer um prato novo?
```

**O que esperar:** ele consulta o caixa e propõe algo, agora com R$ 148 em vez
de R$ 80.

---

## Encerramento sugerido

Volte ao painel do MCP e deixe o quadro inteiro na tela:

```
  SERVIDOR
      chamadas  N total · 0 erro · ultima ha 3s
      gate      N avaliacoes · N bloqueio(s)

  AGREGADO
      ferramenta                   n      p50      max  erro   latencia
```

> **O fecho:** *"tudo que vocês viram passou por aqui. O modelo conduziu a
> conversa; o sistema foi dono dos números e das garantias. Cada linha desse
> painel é uma ferramenta que devolveu um número pronto — e a única coisa que
> sobrou para o modelo foi ler em voz alta."*

---

## Se algo der diferente

| sintoma | causa provável | o que fazer |
|---|---|---|
| ele não acha o creme de leite na web | preço local não indexado | é comportamento correto — ele pergunta a ela; responda o preço |
| CMV do strogonoff parece baixo | o creme ainda não foi comprado, então não tem custo unitário | aceite o prato primeiro; a compra é registrada no aceite |
| ele sugere outro prato | a busca web é ao vivo, muda | siga com o que ele sugerir; os números mudam, a mecânica não |
| `gate 0 avaliacoes` mesmo depois do aceite | o hook não registrou | `docker logs sabor-da-maria-hermes \| grep -i hook` |
| o segundo bot não responde | token repetido, ou gateway ainda subindo | painel do runner, linha `cliente` no bloco HERMES |
| painel do MCP vazio | a porta 8765 não subiu | `curl 127.0.0.1:8765/saude` |

---

## Os números, num lugar só

Para conferir durante a gravação sem procurar:

```
despensa            37 ingredientes · R$ 663,39 já gastos · R$ 80,00 de orçamento

escondidinho        CMV receita R$ 37,11 · porção R$ 6,18 · mínimo R$ 6,87
  cenários          35% R$ 17,66 · 30% R$ 20,60 · 25% R$ 24,72
  publicado         2 lotes × 6 = 12 porções a R$ 17,90
  limite            2 lotes (carne moída)

strogonoff          compra creme de leite 0,6 L = R$ 8,97
  publicado         2 lotes × 6 = 12 porções a R$ 20,90
  limite            1 lote antes da segunda compra (creme de leite)

orçamento           80,00 → 71,03 (1ª compra) → 62,06 (2ª compra)

pedidos             3 × 17,90 = 53,70   +   2 × 20,90 = 41,80   =   95,50
taxa 10%            9,55
líquido             85,95
saldo final         148,01
```
