#!/usr/bin/env python3
"""
Checagens de sanidade do dado de entrada da ANP.

O pipeline confiava na fonte: o que a ANP publicasse entrava direto no painel.
Este modulo roda depois do build e compara o resultado com a execucao anterior
e com o que e plausivel, sinalizando o que precisa de olho humano.

Nada aqui interrompe o pipeline. Os achados sao gravados em
docs/data/qualidade.json e consumidos pelo dashboard e pelo e-mail semanal.

Niveis:
  erro   quebra de integridade -- serie que encolhe, valor impossivel,
         degrau artificial na virada de aliquota
  aviso  algo fora do esperado que pode ser real -- variacao extrema,
         terminal que some, cobertura baixa
  info   comportamento normal que vale registrar -- ANP nao publicou
         semana nova, buraco conhecido na serie
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from statistics import median

BR_TZ = timezone(timedelta(hours=-3))

# limiares
VAR_AVISO = 20.0        # % de variacao semanal da media que merece aviso
VAR_ERRO = 60.0         # % que so pode ser erro de publicacao
DESVIO_TERMINAL = 15.0  # % de desvio de um terminal contra a media da semana
COBERTURA_MIN = 0.80    # fracao minima de terminais com dado na ultima semana
JANELA_DEGRAU = 6       # semanas de cada lado na deteccao de degrau
DEGRAU_FRACAO = 0.5     # fracao da mudanca de aliquota que acusa degrau


def _mean(vals):
    v = [x for x in vals if x is not None]
    return sum(v) / len(v) if v else None


def _fmt(v, casas=4):
    if v is None:
        return "sem dado"
    s = f"{v:,.{casas}f}"
    return s.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def _pct(v):
    return "sem dado" if v is None else ("+" if v > 0 else "") + _fmt(v, 2) + "%"


def _dbr(iso):
    d = date.fromisoformat(iso)
    return d.strftime("%d/%m/%Y")


class Achados:
    def __init__(self):
        self.itens = []

    def add(self, nivel, codigo, produto, label, texto):
        self.itens.append({
            "nivel": nivel, "codigo": codigo,
            "produto": produto, "label": label, "texto": texto,
        })

    erro = lambda self, *a: self.add("erro", *a)      # noqa: E731
    aviso = lambda self, *a: self.add("aviso", *a)    # noqa: E731
    info = lambda self, *a: self.add("info", *a)      # noqa: E731


# --------------------------------------------------------------------------- #
# checagens sobre o PPI
# --------------------------------------------------------------------------- #
def checar_ppi(products: dict, anterior: dict | None, ach: Achados) -> None:
    ant_prod = (anterior or {}).get("products", {})

    for key, p in products.items():
        label = p.get("label") or p.get("title") or key
        weeks = p.get("weeks", [])
        locs = p.get("locations", [])
        if not weeks:
            ach.erro("sem_semanas", key, label, f"{label}: nenhuma semana lida da planilha.")
            continue

        # --- integridade contra a execucao anterior ---------------------------
        ant = ant_prod.get(key)
        if ant:
            n_ant, n_now = len(ant.get("weeks", [])), len(weeks)
            if n_now < n_ant:
                ach.erro(
                    "serie_encolheu", key, label,
                    f"{label}: a serie encolheu de {n_ant} para {n_now} semanas. "
                    f"A planilha da ANP pode ter sido republicada incompleta -- "
                    f"conferir antes de aceitar este dado."
                )
            fim_ant = ant["weeks"][-1]["end"] if ant.get("weeks") else None
            if fim_ant and weeks[-1]["end"] < fim_ant:
                ach.erro(
                    "serie_retrocedeu", key, label,
                    f"{label}: a ultima semana retrocedeu de {_dbr(fim_ant)} para "
                    f"{_dbr(weeks[-1]['end'])}."
                )
            elif fim_ant and weeks[-1]["end"] == fim_ant:
                ach.info(
                    "sem_semana_nova", key, label,
                    f"{label}: a ANP nao publicou semana nova desde {_dbr(fim_ant)}."
                )
            n_loc_ant = len(ant.get("locations", []))
            if n_loc_ant and len(locs) < n_loc_ant:
                sumidos = sorted(set(ant.get("locations", [])) - set(locs))
                ach.aviso(
                    "terminal_sumiu", key, label,
                    f"{label}: {n_loc_ant - len(locs)} terminal(is) sairam da planilha"
                    + (f" ({', '.join(sumidos)})" if sumidos else "") + "."
                )

        # --- integridade interna ---------------------------------------------
        ends = [w["end"] for w in weeks]
        dup = sorted({e for e in ends if ends.count(e) > 1})
        if dup:
            ach.erro(
                "semana_duplicada", key, label,
                f"{label}: semana(s) repetida(s) na serie: {', '.join(_dbr(d) for d in dup[:5])}."
            )

        buracos = []
        for a, b in zip(weeks, weeks[1:]):
            delta = (date.fromisoformat(b["end"]) - date.fromisoformat(a["end"])).days
            if delta > 7:
                buracos.append((a["end"], b["end"], delta // 7))
        if buracos:
            recentes = [x for x in buracos
                        if date.fromisoformat(x[1]) >= date.today() - timedelta(days=365)]
            alvo = recentes or buracos
            ach.info(
                "buraco_calendario", key, label,
                f"{label}: {len(buracos)} intervalo(s) sem publicacao na serie; o mais "
                f"recente entre {_dbr(alvo[-1][0])} e {_dbr(alvo[-1][1])} "
                f"({alvo[-1][2]} semana(s) sem dado)."
            )

        negativos = sum(1 for w in weeks for v in w["v"] if v is not None and v <= 0)
        if negativos:
            ach.erro(
                "valor_nao_positivo", key, label,
                f"{label}: {negativos} valor(es) zerados ou negativos na serie."
            )

        # --- ultima semana ----------------------------------------------------
        last = weeks[-1]
        com_dado = sum(1 for v in last["v"] if v is not None)
        if locs and com_dado / len(locs) < COBERTURA_MIN:
            ach.aviso(
                "cobertura_baixa", key, label,
                f"{label}: so {com_dado} de {len(locs)} terminais tem dado na semana "
                f"de {_dbr(last['end'])} ({com_dado / len(locs):.0%} de cobertura)."
            )

        med = _mean(last["v"])
        if med:
            fora = [(locs[i], v) for i, v in enumerate(last["v"])
                    if v is not None and i < len(locs)
                    and abs(v / med - 1) * 100 > DESVIO_TERMINAL]
            if fora:
                pior = max(fora, key=lambda x: abs(x[1] / med - 1))
                ach.aviso(
                    "terminal_fora_da_faixa", key, label,
                    f"{label}: {len(fora)} terminal(is) a mais de {DESVIO_TERMINAL:.0f}% "
                    f"da media na semana de {_dbr(last['end'])}; o maior desvio e "
                    f"{pior[0]} ({_pct((pior[1] / med - 1) * 100)})."
                )

        if len(weeks) > 1:
            ant_med = _mean(weeks[-2]["v"])
            if med and ant_med:
                var = (med / ant_med - 1) * 100
                if abs(var) >= VAR_ERRO:
                    ach.erro(
                        "variacao_impossivel", key, label,
                        f"{label}: PPI medio variou {_pct(var)} em uma semana "
                        f"({_fmt(ant_med)} para {_fmt(med)}). Variacao dessa ordem "
                        f"costuma ser erro de publicacao, nao mercado."
                    )
                elif abs(var) >= VAR_AVISO:
                    ach.aviso(
                        "variacao_extrema", key, label,
                        f"{label}: PPI medio variou {_pct(var)} em uma semana "
                        f"({_fmt(ant_med)} para {_fmt(med)})."
                    )


# --------------------------------------------------------------------------- #
# degrau artificial na virada de aliquota
# --------------------------------------------------------------------------- #
def checar_degraus(out_dir: Path, ach: Achados) -> list:
    """Um degrau na defasagem exatamente na virada de faixa acusa aliquota errada.

    Se a deducao esta correta, o preco de realizacao e continuo: a serie nao pode
    dar um salto do tamanho da mudanca de aliquota so porque a lei mudou. Esta e
    a unica conferencia disponivel para o QAV, que a Sintese Semanal nao cobre.
    """
    degraus = []
    try:
        with open(out_dir / "defasagem.json", encoding="utf-8") as fh:
            defas = json.load(fh)
        with open(Path(__file__).resolve().parent / "tributos.json", encoding="utf-8") as fh:
            cfg = json.load(fh)
    except FileNotFoundError:
        return degraus

    def total(f):
        return round(float(f["total"]) if "total" in f
                     else f.get("pis", 0) + f.get("cofins", 0) + f.get("cide", 0), 6)

    por_produto = {}
    for f in cfg["faixas"]:
        por_produto.setdefault(f["produto"], []).append(f)
    for v in por_produto.values():
        v.sort(key=lambda x: x["de"])

    for key, faixas in por_produto.items():
        p = (defas.get("products") or {}).get(key)
        if not p or len(faixas) < 2:
            continue
        weeks = p["weeks"]
        label = p.get("label", key)

        for anterior, atual in zip(faixas, faixas[1:]):
            corte = atual["de"]
            d_taxa = total(atual) - total(anterior)
            if abs(d_taxa) < 1e-6:
                continue

            antes = [w["gap"] for w in weeks if w["end"] < corte][-JANELA_DEGRAU:]
            depois = [w["gap"] for w in weeks if w["end"] >= corte][:JANELA_DEGRAU]
            if len(antes) < 2 or len(depois) < 2:
                continue

            salto = median(depois) - median(antes)
            reg = {
                "produto": key, "label": label, "corte": corte,
                "taxa_antes": total(anterior), "taxa_depois": total(atual),
                "mudanca_taxa": round(d_taxa, 6),
                "salto_defasagem": round(salto, 6),
                "semanas_antes": len(antes), "semanas_depois": len(depois),
                "presumida": bool(atual.get("presumido")),
            }
            degraus.append(reg)

            # Um erro de aliquota aparece como salto de sinal oposto ao da mudanca.
            suspeito = (abs(salto) >= DEGRAU_FRACAO * abs(d_taxa)
                        and salto * d_taxa < 0)
            if suspeito:
                ach.erro(
                    "degrau_aliquota", key, label,
                    f"{label}: a defasagem deu um salto de {_fmt(salto)} R$ na virada de "
                    f"aliquota de {_dbr(corte)}, contra uma mudanca de aliquota de "
                    f"{_fmt(d_taxa)} R$. Salto e mudanca tem sinais opostos e ordem de "
                    f"grandeza parecida, o que e a assinatura de aliquota errada em "
                    f"scripts/tributos.json."
                )
            elif atual.get("presumido"):
                ach.aviso(
                    "aliquota_presumida", key, label,
                    f"{label}: a aliquota vigente desde {_dbr(corte)} e presumida, nao "
                    f"confirmada em fonte oficial. O teste de continuidade nao acusou "
                    f"degrau (salto de {_fmt(salto)} R$ contra mudanca de "
                    f"{_fmt(d_taxa)} R$), o que sustenta a presuncao, mas ela segue "
                    f"pendente de confirmacao."
                )
    return degraus


# --------------------------------------------------------------------------- #
def run(products: dict, anterior: dict | None, out_dir: Path) -> dict:
    ach = Achados()
    checar_ppi(products, anterior, ach)
    degraus = checar_degraus(out_dir, ach)

    ordem = {"erro": 0, "aviso": 1, "info": 2}
    ach.itens.sort(key=lambda x: (ordem[x["nivel"]], x["produto"]))
    resumo = {n: sum(1 for i in ach.itens if i["nivel"] == n)
              for n in ("erro", "aviso", "info")}

    payload = {
        "generated_at": datetime.now(BR_TZ).isoformat(timespec="seconds"),
        "comparado_com": (anterior or {}).get("generated_at"),
        "limiares": {
            "variacao_aviso_pct": VAR_AVISO, "variacao_erro_pct": VAR_ERRO,
            "desvio_terminal_pct": DESVIO_TERMINAL,
            "cobertura_minima": COBERTURA_MIN,
        },
        "resumo": resumo,
        "achados": ach.itens,
        "degraus": degraus,
    }
    with open(out_dir / "qualidade.json", "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)

    print(f"[qualidade] {resumo['erro']} erro(s), {resumo['aviso']} aviso(s), "
          f"{resumo['info']} info")
    for i in ach.itens:
        if i["nivel"] != "info":
            print(f"[qualidade]   {i['nivel'].upper():<5} {i['texto']}")
    return payload
