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

3. Publica os dados no GitHub Pages e envia o **e-mail semanal** toda segunda-feira.

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
[Síntese Semanal de Preços](https://www.gov.br/anp/pt-br/assuntos/precos-e-defesa-da-concorrencia/precos/sintese-semanal-de-precos)
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
  zerada pelo Decreto 5.060/2004. A alíquota está em **zero de 13/04/2026 a 31/07/2026**
  (Decretos 12.924 e 12.991/2026); depois disso, sem prorrogação registrada em
  `tributos.json`, as semanas ficam sem defasagem em vez de receberem um tributo presumido.
- O preço de realização é a média ponderada de **todos os produtores e importadores** do país,
  não apenas da Petrobras.
- A defasagem **não é margem nem lucro**: ignora custos logísticos, tributos estaduais e a
  estrutura comercial de cada agente.

## E-mail semanal

Toda segunda-feira, depois de atualizar os dados, o workflow envia um e-mail com:

- resumo do PPI por produto e variação sobre a semana anterior;
- defasagem em R$ e em %, com a semana anterior para comparação;
- **destaques**: defasagem acima de 15% em módulo, inversão de sinal e variação semanal
  acima de 5%;
- **lacunas**: se algum dado estiver atrasado ou faltando, o e-mail sai assim mesmo e
  sinaliza o que faltou;
- link para o dashboard e o CSV da defasagem em anexo.

### Configurar o envio

O envio usa SMTP e depende de secrets do repositório. **Nenhuma credencial fica no código.**

1. Gere uma **senha de app** do Gmail em <https://myaccount.google.com/apppasswords>
   (requer verificação em duas etapas ativa). A senha da conta não funciona.
2. Em **Settings → Secrets and variables → Actions → New repository secret**, crie:

   | Secret | Valor |
   |---|---|
   | `SMTP_HOST` | `smtp.gmail.com` |
   | `SMTP_PORT` | `587` |
   | `SMTP_USER` | seu endereço Gmail |
   | `SMTP_PASS` | a senha de app de 16 caracteres |
   | `MAIL_TO` | destinatários, separados por vírgula |
   | `MAIL_FROM` | opcional; padrão é `SMTP_USER` |

3. Teste em **Actions → Atualizar PPI (ANP) → Run workflow**.

Enquanto os secrets não existirem, o passo de e-mail roda e apenas gera a prévia, sem falhar.
O e-mail sai apenas no agendamento e nas execuções manuais — commits de código não disparam envio.

## Estrutura

```
scripts/build_data.py                 coleta e normalizacao do PPI
scripts/defasagem.py                  preco de realizacao, deducao de tributos e defasagem
scripts/tributos.json                 aliquotas ad rem, com vigencia e fonte
scripts/email_semanal.py              montagem e envio do e-mail
.github/workflows/atualizar-ppi.yml   automacao semanal
docs/index.html                       dashboard (pagina unica, Chart.js via CDN)
docs/data/ppi.json                    serie completa do PPI
docs/data/defasagem.json              serie pareada PPI x realizacao
docs/data/defasagem.csv               mesma serie em formato longo
docs/data/meta.json                   metadados da ultima execucao
docs/data/_debug_ppidp.json           diagnostico e validacao contra a ANP
```

## Automação

O workflow `Atualizar PPI (ANP)` roda:

- **toda segunda-feira às 20:00 (America/Sao_Paulo)** — cron `0 23 * * 1` em UTC;
- sob demanda, pelo botão **Run workflow**;
- a cada push em `scripts/` ou `requirements.txt` (sem enviar e-mail).

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
- ANP — [Síntese Semanal de Preços dos Combustíveis](https://www.gov.br/anp/pt-br/assuntos/precos-e-defesa-da-concorrencia/precos/sintese-semanal-de-precos)

Dados públicos; este repositório apenas coleta, organiza e visualiza.
