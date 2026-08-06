#!/usr/bin/env python3
"""
Monta e envia o e-mail semanal do dashboard de PPI.

Conteudo: resumo do PPI por produto, defasagem contra o preco de realizacao,
destaques e alertas, link para o dashboard e o CSV da defasagem em anexo.

O e-mail e enviado mesmo quando falta dado: as lacunas aparecem sinalizadas
numa secao propria, em vez de o envio ser cancelado.

Variaveis de ambiente (definidas como secrets do repositorio):
  SMTP_HOST   servidor SMTP            (ex.: smtp.gmail.com)
  SMTP_PORT   porta                    (587 STARTTLS ou 465 SSL; padrao 587)
  SMTP_USER   usuario / remetente
  SMTP_PASS   senha de app
  MAIL_TO     destinatarios separados por virgula (padrao: SMTP_USER)
  MAIL_FROM   remetente exibido        (padrao: SMTP_USER)
  DASHBOARD_URL  link do dashboard

Sem SMTP_HOST/USER/PASS o script apenas escreve a previa e termina com
sucesso, para nao quebrar o pipeline de dados.
"""

from __future__ import annotations

import json
import os
import smtplib
import ssl
import sys
from datetime import date, datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "docs" / "data"
BR_TZ = timezone(timedelta(hours=-3))

DASHBOARD = os.environ.get(
    "DASHBOARD_URL", "https://jmagomez.github.io/anp-ppi-dashboard/"
)
REPO = "https://github.com/jmagomez/anp-ppi-dashboard"

# limiares dos alertas
LIM_DEFASAGEM = 15.0   # % em modulo
LIM_VARIACAO = 5.0     # % em modulo, semana contra semana
LIM_ATRASO_DIAS = 14   # dado considerado atrasado


# --------------------------------------------------------------------------- #
# formatacao
# --------------------------------------------------------------------------- #
def nfmt(v, casas=2):
    if v is None:
        return "—"
    s = f"{v:,.{casas}f}"
    return s.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def pfmt(v, casas=2):
    if v is None:
        return "—"
    return ("+" if v > 0 else "") + nfmt(v, casas) + "%"


def dfmt(iso):
    d = date.fromisoformat(iso)
    return d.strftime("%d/%m/%Y")


def casas(unit):
    return 4 if "litro" in unit.lower() else 2


def mean(vals):
    v = [x for x in vals if x is not None]
    return sum(v) / len(v) if v else None


def load(name):
    path = DATA / name
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


# --------------------------------------------------------------------------- #
# montagem do conteudo
# --------------------------------------------------------------------------- #
def resumo(ppi, defas):
    linhas = []
    for key in ppi["order"]:
        p = ppi["products"][key]
        c = casas(p["unit"])
        w = p["weeks"][-1]
        prev = p["weeks"][-2] if len(p["weeks"]) > 1 else None
        atual = mean(w["v"])
        ant = mean(prev["v"]) if prev else None
        var = (atual / ant - 1) * 100 if (atual and ant) else None

        d = (defas or {}).get("products", {}).get(key)
        dw = d["weeks"][-1] if d else None
        dprev = d["weeks"][-2] if d and len(d["weeks"]) > 1 else None

        linhas.append({
            "key": key,
            "titulo": p["title"],
            "unidade": p["unit"],
            "casas": c,
            "semana": w["label"],
            "ppi": atual,
            "var": var,
            "realizacao": dw["real"] if dw else None,
            "defasagem": dw["gap"] if dw else None,
            "defasagem_pct": dw["gap_pct"] if dw else None,
            "defasagem_ant": dprev["gap_pct"] if dprev else None,
            "semana_defasagem": dw["end"] if dw else None,
        })
    return linhas


def alertas(linhas, ppi, defas):
    out = []
    for l in linhas:
        nome = l["titulo"]
        if l["defasagem_pct"] is not None:
            if abs(l["defasagem_pct"]) >= LIM_DEFASAGEM:
                lado = "abaixo" if l["defasagem_pct"] < 0 else "acima"
                out.append(
                    f"{nome}: preço interno {nfmt(abs(l['defasagem_pct']), 1)}% {lado} "
                    f"da paridade de importação."
                )
            if (l["defasagem_ant"] is not None
                    and l["defasagem_pct"] * l["defasagem_ant"] < 0):
                out.append(
                    f"{nome}: a defasagem inverteu de sinal — de "
                    f"{pfmt(l['defasagem_ant'])} para {pfmt(l['defasagem_pct'])}."
                )
        if l["var"] is not None and abs(l["var"]) >= LIM_VARIACAO:
            out.append(f"{nome}: PPI variou {pfmt(l['var'])} em uma semana.")
    return out


def lacunas(ppi, defas):
    out = []
    hoje = datetime.now(BR_TZ).date()

    ultima = max(date.fromisoformat(p["weeks"][-1]["end"])
                 for p in ppi["products"].values())
    atraso = (hoje - ultima).days
    if atraso > LIM_ATRASO_DIAS:
        out.append(
            f"O PPI mais recente é da semana encerrada em {dfmt(ultima.isoformat())}, "
            f"há {atraso} dias. A ANP pode estar com a publicação atrasada."
        )

    if not defas:
        out.append(
            "A série de defasagem não foi gerada nesta execução: o arquivo de "
            "preços de produtores da ANP não pôde ser lido. O resumo do PPI "
            "abaixo segue válido."
        )
        return out

    for key, d in defas.get("products", {}).items():
        fim = date.fromisoformat(d["weeks"][-1]["end"])
        if (ultima - fim).days >= 7:
            out.append(
                f"{d['label']}: o preço de realização mais recente é de "
                f"{dfmt(fim.isoformat())}, {(ultima - fim).days} dias atrás do PPI. "
                f"A planilha de produtores da ANP costuma sair com defasagem de "
                f"cerca de 12 dias."
            )

    faltando = [ppi["products"][k]["title"] for k in ppi["order"]
                if k not in defas.get("products", {})]
    if faltando:
        out.append("Sem defasagem calculada para: " + ", ".join(faltando) + ".")

    # Aliquotas: faixa vencida (semanas ja saindo da serie) ou perto de vencer.
    # Sem este aviso, o produto encolhe no grafico sem que nada sinalize -- foi
    # exatamente o que aconteceria com o QAV depois de 31/07/2026.
    for a in defas.get("avisos_cobertura", []):
        out.append(a["texto"])
    return out


# --------------------------------------------------------------------------- #
# render
# --------------------------------------------------------------------------- #
def render_texto(linhas, alerts, gaps, ppi):
    L = []
    L.append("PPI ANP — resumo semanal")
    L.append(f"Semana de referência: {linhas[0]['semana']}")
    L.append("")
    if gaps:
        L.append("LACUNAS NESTA EDIÇÃO")
        for g in gaps:
            L.append(f"  - {g}")
        L.append("")
    if alerts:
        L.append("DESTAQUES")
        for a in alerts:
            L.append(f"  - {a}")
        L.append("")
    L.append("RESUMO POR PRODUTO")
    for l in linhas:
        c = l["casas"]
        L.append(f"  {l['titulo']} ({l['unidade']})")
        L.append(f"    PPI médio ......... {nfmt(l['ppi'], c)}   ({pfmt(l['var'])} na semana)")
        if l["defasagem_pct"] is not None:
            L.append(f"    Realização ........ {nfmt(l['realizacao'], c)}")
            L.append(f"    Defasagem ......... {nfmt(l['defasagem'], c)}  ({pfmt(l['defasagem_pct'])})")
        else:
            L.append("    Defasagem ......... não publicada para este produto")
        L.append("")
    L.append(f"Dashboard: {DASHBOARD}")
    L.append(f"Código e dados: {REPO}")
    L.append("")
    L.append("Defasagem = preço de realização menos PPI, ambos sem tributos. "
             "Valor negativo indica preço interno abaixo da paridade de importação.")
    return "\n".join(L)


def render_html(linhas, alerts, gaps, ppi, defas):
    UP, DOWN, MUT = "#C0392B", "#1E8E63", "#5A6B80"

    def cor(v):
        return UP if (v or 0) > 0 else DOWN

    partes = []
    partes.append(f"""<!DOCTYPE html><html><body style="margin:0;padding:0;background:#F4F6F9;">
<div style="max-width:640px;margin:0 auto;padding:24px 18px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;color:#0F1B2D;">
  <h1 style="margin:0 0 4px;font-size:20px;font-weight:650;letter-spacing:-.02em;">PPI ANP — resumo semanal</h1>
  <p style="margin:0 0 20px;color:{MUT};font-size:13px;">Semana de referência: <b style="color:#0F1B2D;">{linhas[0]['semana']}</b></p>""")

    if gaps:
        itens = "".join(f"<li style='margin-bottom:5px;'>{g}</li>" for g in gaps)
        partes.append(f"""
  <div style="background:#FFF7E6;border:1px solid #F0D9A8;border-radius:10px;padding:13px 16px;margin-bottom:18px;">
    <div style="font-size:11px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:#8A6410;margin-bottom:7px;">Lacunas nesta edição</div>
    <ul style="margin:0;padding-left:17px;font-size:13px;color:#5C4409;line-height:1.6;">{itens}</ul>
  </div>""")

    if alerts:
        itens = "".join(f"<li style='margin-bottom:5px;'>{a}</li>" for a in alerts)
        partes.append(f"""
  <div style="background:#FFFFFF;border:1px solid #E2E8F0;border-radius:10px;padding:13px 16px;margin-bottom:18px;">
    <div style="font-size:11px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:{MUT};margin-bottom:7px;">Destaques</div>
    <ul style="margin:0;padding-left:17px;font-size:13px;line-height:1.6;">{itens}</ul>
  </div>""")

    for l in linhas:
        c = l["casas"]
        if l["defasagem_pct"] is not None:
            bloco = f"""
      <table role="presentation" style="width:100%;border-collapse:collapse;font-size:13px;">
        <tr>
          <td style="padding:5px 0;color:{MUT};">PPI médio</td>
          <td style="padding:5px 0;text-align:right;font-weight:600;">{nfmt(l['ppi'], c)}</td>
          <td style="padding:5px 0 5px 12px;text-align:right;color:{cor(l['var'])};">{pfmt(l['var'])}</td>
        </tr>
        <tr>
          <td style="padding:5px 0;color:{MUT};">Preço de realização</td>
          <td style="padding:5px 0;text-align:right;font-weight:600;">{nfmt(l['realizacao'], c)}</td>
          <td></td>
        </tr>
        <tr>
          <td style="padding:9px 0 0;border-top:1px solid #E2E8F0;font-weight:650;">Defasagem</td>
          <td style="padding:9px 0 0;border-top:1px solid #E2E8F0;text-align:right;font-weight:650;color:{cor(l['defasagem_pct'])};">{nfmt(l['defasagem'], c)}</td>
          <td style="padding:9px 0 0 12px;border-top:1px solid #E2E8F0;text-align:right;font-weight:650;color:{cor(l['defasagem_pct'])};">{pfmt(l['defasagem_pct'])}</td>
        </tr>
      </table>"""
        else:
            bloco = f"""
      <table role="presentation" style="width:100%;border-collapse:collapse;font-size:13px;">
        <tr>
          <td style="padding:5px 0;color:{MUT};">PPI médio</td>
          <td style="padding:5px 0;text-align:right;font-weight:600;">{nfmt(l['ppi'], c)}</td>
          <td style="padding:5px 0 5px 12px;text-align:right;color:{cor(l['var'])};">{pfmt(l['var'])}</td>
        </tr>
      </table>
      <p style="margin:9px 0 0;font-size:12px;color:{MUT};">Defasagem não publicada para este produto.</p>"""

        partes.append(f"""
  <div style="background:#FFFFFF;border:1px solid #E2E8F0;border-radius:10px;padding:15px 16px;margin-bottom:12px;">
    <div style="display:block;margin-bottom:9px;">
      <span style="font-size:15px;font-weight:650;">{l['titulo']}</span>
      <span style="font-size:12px;color:{MUT};"> · {l['unidade']}</span>
    </div>{bloco}
  </div>""")

    partes.append(f"""
  <div style="text-align:center;margin:22px 0 18px;">
    <a href="{DASHBOARD}" style="display:inline-block;background:#0F1B2D;color:#FFFFFF;text-decoration:none;padding:11px 22px;border-radius:8px;font-size:13px;font-weight:600;">Abrir o dashboard</a>
  </div>
  <p style="font-size:11.5px;color:{MUT};line-height:1.65;margin:0;border-top:1px solid #E2E8F0;padding-top:14px;">
    Defasagem = preço de realização menos PPI, ambos sem tributos. Valor negativo indica preço
    interno abaixo da paridade de importação. Não é margem nem lucro: ignora custos logísticos,
    tributos estaduais e a estrutura comercial de cada agente.<br><br>
    Fonte: ANP. O CSV da semana vai em anexo.
    <a href="{REPO}" style="color:{MUT};">Código e dados no GitHub</a>.
  </p>
</div></body></html>""")
    return "".join(partes)


# --------------------------------------------------------------------------- #
# envio
# --------------------------------------------------------------------------- #
def enviar(assunto, texto, html, anexos):
    host = os.environ.get("SMTP_HOST")
    user = os.environ.get("SMTP_USER")
    senha = os.environ.get("SMTP_PASS")
    if not (host and user and senha):
        print("[email] SMTP nao configurado; gerando apenas a previa.")
        return False

    porta = int(os.environ.get("SMTP_PORT", "587"))
    destino = [x.strip() for x in os.environ.get("MAIL_TO", user).split(",") if x.strip()]
    remetente = os.environ.get("MAIL_FROM", user)

    msg = EmailMessage()
    msg["Subject"] = assunto
    msg["From"] = remetente
    msg["To"] = ", ".join(destino)
    msg.set_content(texto)
    msg.add_alternative(html, subtype="html")

    for path in anexos:
        if path.exists():
            msg.add_attachment(path.read_bytes(), maintype="text",
                               subtype="csv", filename=path.name)

    ctx = ssl.create_default_context()
    if porta == 465:
        with smtplib.SMTP_SSL(host, porta, context=ctx, timeout=60) as s:
            s.login(user, senha)
            s.send_message(msg)
    else:
        with smtplib.SMTP(host, porta, timeout=60) as s:
            s.ehlo()
            s.starttls(context=ctx)
            s.login(user, senha)
            s.send_message(msg)
    print(f"[email] enviado para {', '.join(destino)}")
    return True


def main() -> int:
    ppi = load("ppi.json")
    if not ppi:
        print("[email] ppi.json ausente; nada a enviar.")
        return 0
    defas = load("defasagem.json")

    linhas = resumo(ppi, defas)
    alerts = alertas(linhas, ppi, defas)
    gaps = lacunas(ppi, defas)

    principal = next((l for l in linhas if l["defasagem_pct"] is not None), linhas[0])
    if principal["defasagem_pct"] is not None:
        assunto = (f"PPI ANP · {principal['titulo']} {pfmt(principal['defasagem_pct'])} "
                   f"vs. paridade · {dfmt(ppi['latest_week_end'])}")
    else:
        assunto = f"PPI ANP · resumo da semana de {dfmt(ppi['latest_week_end'])}"
    if gaps:
        assunto += " (com lacunas)"

    texto = render_texto(linhas, alerts, gaps, ppi)
    html = render_html(linhas, alerts, gaps, ppi, defas)

    (DATA.parent / "_email_previa.html").write_text(html, encoding="utf-8")
    print(texto)

    try:
        enviar(assunto, texto, html, [DATA / "defasagem.csv"])
    except Exception as exc:  # noqa: BLE001
        print(f"[email] ERRO no envio: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
