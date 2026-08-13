# Dashboard PPI — ANP

Dashboard interativo dos **Preços de Paridade de Importação (PPI)** publicados pela ANP e
da **defasagem** entre o preço interno e a paridade de importação, com coleta, cálculo e
envio de e-mail automáticos.

**Dashboard:** https://jmagomez.github.io/anp-ppi-dashboard/

---

## O que o projeto faz

1. Baixa o arquivo oficial [`ppi.xlsx`](https://www.gov.br/anp/pt-br/assuntos/precos-e-defesa-da-concorrencia/precos/arq-ppi/ppi.xlsx) da ANP e normaliza as 4 abas semanais:

   | Aba | Produto | Unidade | Terminais |
   |---|---|---|---|
   | `Gasolina R$ semanal` | Gasolina A Comum | R$/litro | 16 |
   | `Diesel R$ semanal` | Diesel A S10 | R$/litro | 16 |
   | `QAV R$ semanal` | QAV | R$/litro | 16 |
   | `GLP R$ kg semanal` | GLP | R$/13kg | 2 (Suape, Santos) |

   Série semanal desde **novembro/2018**.

2. Baixa a planilha de [Preços Médios Ponderados Semanais de produtores e importadores](https://www.gov.br/anp/pt-br/assuntos/precos-e-defesa-da-concorrencia/precos/precos-de-produtores-e-importadores-de-derivados-de-petroleo-e-biodiesel) e calcula a **defasagem PPI × preço de realização**.

3. Roda as **checagens de sanidade** sobre o que a ANP publicou.

4. Publica os dados no GitHub Pages e envia o **e-mail semanal** toda segunda-feira.

## As duas séries não andam juntas

O PPI é publicado poucos dias depois do fim da semana; a planilha de produtores sai cerca de
dez dias depois. A defasagem só existe para semanas em que as duas pontas estão disponíveis,
então ela é normalmente **de uma semana anterior à do PPI**.

Isso não é um defeito, é o ritmo da fonte — mas apresentar os dois números como se fossem da
mesma semana é. Dashboard e e-mail carimbam a semana em cada bloco: o cabeçalho traz as duas
datas, os KPIs dizem a que semana pertencem e a tabela põe a data no cabeçalho de cada coluna.

## A defasagem, e por que ela dá trabalho

As duas séries da ANP **não estão na mesma base tributária**:

- o **PPI** é publicado sem tributos — “Todos os preços divulgados não incluem tributos”;
- a planilha de **produtores e importadores** inclui Cide, PIS/Pasep e Cofins (só exclui ICMS).

Comparar as duas diretamente infla a defasagem por um valor que é imposto federal, não margem.
O pipeline resolve isso deduzindo as alíquotas *ad rem* declaradas em
[`scripts/tributos.json`](scripts/tributos.json), um arquivo versionado e auditável, com
data de vigência e fonte em cada faixa.

**As duas séries também usam calendários diferentes**: o PPI fecha a semana na sexta e a
planilha de produtores usa outro fechamento. O pareamento é feito pela segunda-feira da
semana ISO de cada data final.

### Conferência automática

A cada execução o resultado é comparado com os valores que a própria ANP publica na
[Síntese Semanal de Preços](https://www.gov.br/anp/pt-br/assuntos/precos-e-defesa-da-concorrencia/precos/sintese-semanal-do-comportamento-dos-precos-dos-combustiveis)
(edição 13/2026, 12 pontos de controle). Última conferência:

| Produto | Erro no PPI | Erro na realização | Tributo implícito | Tributo aplicado |
|---|---|---|---|---|
| Gasolina A | +0,01% | −0,07% a +0,12% | 0,8907 a 0,8958 | 0,8925 |
| Diesel A S10 | −0,03% | +0,01% a +0,06% | 0,3552 → ~0 | 0,3550 → 0 |
| GLP | 0,00% | −0,01% a +0,01% | ~0 | 0 |

O diagnóstico completo fica em `docs/data/_debug_ppidp.json`, seção `validacao_sintese`.
Se o **tributo implícito** divergir do **aplicado**, a alíquota mudou — corrija
`scripts/tributos.json`.

### Limites conhecidos

- A série de defasagem começa em **04/09/2023**, quando passa a valer o regime de alíquotas
  conferido. Semanas anteriores ficam de fora em vez de receberem um número calculado sobre
  base tributária incerta.
- **QAV não tem conferência externa**: a Síntese Semanal não cobre esse produto, então não há
  preço de realização publicado pela ANP para comparar. A dedução vem direto do Decreto
  5.059/2004 (art. 2º, IV — PIS R$ 12,69/m³ e Cofins R$ 58,51/m³, ou R$ 0,0712/l), com a Cide
  zerada pelo Decreto 5.060/2004. Esteve em **zero de 13/04/2026 a 31/07/2026** (Decretos
  12.924 e 12.991/2026). A faixa a partir de **03/08/2026 é presumida**: não foi localizada
  prorrogação publicada, então o valor volta ao estatutário e fica marcado com
  `"presumido": true` até que a conferência feche.
- O preço de realização é a média ponderada de **todos os produtores e importadores** do país,
  não apenas da Petrobras.
- A defasagem **não é margem nem lucro**: ignora custos logísticos, tributos estaduais e a
  estrutura comercial de cada agente.

### Aviso de cobertura de alíquota

Uma faixa de `tributos.json` pode ter prazo. Passada a data, `deducao()` devolve `None`, a
semana é descartada e o produto **encolhe no gráfico sem erro nenhum**. Para que isso não passe
despercebido, a cada execução o pipeline emite avisos publicados em `defasagem.json`
(`avisos_cobertura`), no log, no e-mail semanal e no topo da metodologia do dashboard:

| nível | quando | texto |
|---|---|---|
| `erro` | há semanas com dado da ANP e sem alíquota declarada | quantas semanas e o intervalo |
| `erro` | a última faixa já venceu | data do vencimento |
| `aviso` | a última faixa vence em até 45 dias | data e dias restantes |

Faixa sem data final (`"ate": null`, caso de gasolina e GLP) nunca gera aviso.

## Checagens de sanidade do dado de entrada

`scripts/qualidade.py` roda depois do build e compara o resultado com a execução anterior e
com o que é plausível, em vez de confiar na fonte. Os achados vão para
`docs/data/qualidade.json`, para o painel **Qualidade do dado** no dashboard e para o e-mail.

| nível | checagem |
|---|---|
| `erro` | série encolheu ou retrocedeu entre execuções |
| `erro` | semana duplicada; valor zerado ou negativo |
| `erro` | variação semanal ≥ 60% — ordem de grandeza de erro de publicação |
| `erro` | degrau com assinatura de alíquota errada na virada de faixa |
| `aviso` | variação semanal ≥ 20%; terminal a mais de 15% da média |
| `aviso` | terminal sumiu da planilha; cobertura abaixo de 80% |
| `aviso` | buraco no calendário no último ano; alíquota presumida |
| `info` | a ANP não publicou semana nova desde a última execução |

### O teste de degrau

É a única conferência disponível para o QAV. Se aplicamos a alíquota nova quando a antiga ainda
valia, o preço de realização reconstruído erra pela diferença entre as duas e a defasagem dá um
salto de exatamente `-Δtaxa` na data do corte. O teste mede o quanto o salto observado se parece
com essa assinatura:

```
resíduo = |salto + Δtaxa|        razão = resíduo / |Δtaxa|
```

Perto de zero, a alíquota provavelmente está errada. Muito acima de 1, o mercado se moveu tanto
no período que o teste **não conclui nada** — e isso é dito, em vez de virar falso positivo. Foi
o que aconteceu na virada do QAV em abril/2026: o preço subiu 2,79 R$, 39 vezes a mudança de
alíquota de 0,07 R$.

## E-mail semanal

Toda segunda-feira, depois de atualizar os dados, o workflow envia um e-mail com:

- as **duas semanas** no cabeçalho — a do PPI e a da realização, com a distância entre elas;
- resumo do PPI por produto e variação sobre a semana anterior;
- defasagem em R$ e em %, sempre carimbada com a sua semana;
- **destaques**: defasagem acima de 15% em módulo, inversão de sinal e variação semanal
  acima de 5%;
- **precisa de revisão** e **avisos e lacunas**: erros e avisos das checagens de qualidade e
  da cobertura de alíquotas. Se algum dado faltar, o e-mail sai assim mesmo e sinaliza o que
  faltou;
- link para o dashboard e o CSV da defasagem em anexo.

### Configurar o envio

O envio usa SMTP e depende de secrets do repositório. **Nenhuma credencial fica no código.**

1. Gere uma **senha de app** do Gmail em <https://myaccount.google.com/apppasswords>
   (requer verificação em duas etapas ativa). A senha da conta não funciona.
2. Em **Settings → Secrets and variables → Actions → New repository secret**, crie
   `MAIL_USERNAME` (endereço Gmail) e `MAIL_PASSWORD_ANP` (senha de app de 16 caracteres).
   Host e porta ficam fixos no workflow, por não serem sigilosos.
3. Teste em **Actions → Atualizar PPI (ANP) → Run workflow**.

Enquanto os secrets não existirem, o passo de e-mail roda e apenas gera a prévia, sem falhar.

## Estrutura

```
scripts/build_data.py                 coleta e normalizacao do PPI
scripts/defasagem.py                  preco de realizacao, deducao de tributos e defasagem
scripts/qualidade.py                  checagens de sanidade do dado de entrada
scripts/tributos.json                 aliquotas ad rem, com vigencia e fonte
scripts/email_semanal.py              montagem e envio do e-mail
.github/workflows/atualizar-ppi.yml   coleta diaria e e-mail semanal
docs/index.html                       dashboard (pagina unica, Chart.js via CDN)
docs/data/ppi.json                    serie completa do PPI
docs/data/defasagem.json              serie pareada PPI x realizacao
docs/data/defasagem.csv               mesma serie em formato longo
docs/data/qualidade.json              achados das checagens de sanidade
docs/data/meta.json                   metadados da ultima execucao
docs/data/_debug_ppidp.json           diagnostico e validacao contra a ANP
```

## Automação

O workflow `Atualizar PPI (ANP)` roda:

- **todo dia às 08:00 (America/Sao_Paulo)** — cron `0 11 * * *` em UTC. Só coleta: o commit
  sai apenas se o dado mudar, e nenhum e-mail é enviado;
- **toda segunda-feira às 20:00 (America/Sao_Paulo)** — cron `0 23 * * 1` em UTC. É a única
  execução agendada que dispara o e-mail;
- sob demanda, pelo botão **Run workflow** (envia e-mail);
- a cada push em `scripts/` ou `requirements.txt` (sem enviar e-mail).

### Por que segunda às 20h

A janela foi escolhida a partir do histórico das próprias execuções, não por convenção:

| Execução | PPI até | Realização até |
|---|---|---|
| dom 26/07 16h47 | 17/07 | 17/07 |
| seg 27/07 21h04 | **24/07** | 17/07 |
| seg 03/08 21h10 | 31/07 | **24/07** |
| qua 05/08 22h38 | 31/07 | 24/07 |
| seg 10/08 20h36 | **07/08** | **31/07** |

A ANP publica o PPI na própria segunda-feira — no domingo ainda não está lá. E a planilha de
produtores entra cerca de dez dias depois do fim da semana, ou seja, também numa segunda.
Rodar mais tarde na semana não traz nada: a execução de quarta 05/08 devolveu exatamente o
mesmo que a de segunda 03/08. A coleta diária existe só para cobrir o caso de a ANP publicar
depois das 20h, em vez de o painel ficar uma semana parado.

> Os horários agendados do GitHub Actions rodam em regime de melhor esforço e podem atrasar
> alguns minutos em janelas de alta demanda.

## Rodar localmente

```bash
pip install -r requirements.txt
python scripts/build_data.py
python scripts/email_semanal.py          # sem secrets, gera docs/_email_previa.html
python -m http.server 8000 --directory docs
```

## Fontes

- ANP — [Preços de Paridade de Importação](https://www.gov.br/anp/pt-br/assuntos/precos-e-defesa-da-concorrencia/precos/precos-de-paridade-de-importacao)
- ANP — [Preços de produtores e importadores de derivados de petróleo e biodiesel](https://www.gov.br/anp/pt-br/assuntos/precos-e-defesa-da-concorrencia/precos/precos-de-produtores-e-importadores-de-derivados-de-petroleo-e-biodiesel)
- ANP — [Síntese Semanal de Preços dos Combustíveis](https://www.gov.br/anp/pt-br/assuntos/precos-e-defesa-da-concorrencia/precos/sintese-semanal-do-comportamento-dos-precos-dos-combustiveis)

Dados públicos; este repositório apenas coleta, organiza e visualiza.
