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
         degrau com assinatura de aliquota errada
  aviso  algo fora do esperado que pode ser real -- variacao extrema,
         terminal que some, cobertura baixa, aliquota presumida
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
RESIDUO_ACUSA = 0.5     # |salto + d_taxa| / |d_taxa| abaixo disso acusa erro
RESIDUO_INCONCLUSIVO = 2.0  # acima disso o mercado se moveu demais para concluir


def hoje_br() -> date:
    """Data corrente em Brasilia.

    O runner do GitHub roda em UTC: na execucao de segunda as 23:00 UTC o
    date.today() ja e terca, enquanto o resto do pipeline carimba tudo em BRT.
    Duas nocoes de "hoje" no mesmo processo sempre acabam divergindo em algum
    caso de borda.
    """
    return datetime.now(BR_TZ).date()


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
    return date.fromisoformat(iso).strftime("%d/%m/%Y")


class Achados:
    def __init__(self):
        self.itens = []

    def add(self, nivel, codigo, produto, label, texto):
        self.itens.append({
            "nivel": nivel, "codigo": codigo,
            "produto": produto, "label": label, "texto": texto,
        })

    def erro(self, *a):
        self.add("erro", *a)

    def aviso(self, *a):
        self.add("aviso", *a)

    def info(self, *a):
        self.add("info", *a)

    def codigos(self, nivel=None):
        return [i["codigo"] for i in self.itens if nivel is None or i["nivel"] == nivel]


# --------------------------------------------------------------------------- #
# checagens sobre o PPI
# --------------------------------------------------------------------------- #
def checar_ppi(products: dict, anterior: dict | None, ach: Achados,
               hoje: date | None = None) -> None:
    hoje = hoje or hoje_br()
    ant_prod = (anterior or {}).get("products", {})
    parados = []

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
                parados.append((label, fim_ant))
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
                f"{label}: semana(s) repetida(s) na serie: "
                f"{', '.join(_dbr(d) for d in dup[:5])}."
            )

        limite = hoje - timedelta(days=365)
        recentes = []
        for a, b in zip(weeks, weeks[1:]):
            fim = date.fromisoformat(b["end"])
            delta = (fim - date.fromisoformat(a["end"])).days
            if delta > 7 and fim >= limite:
                recentes.append((a["end"], b["end"], delta // 7))
        if recentes:
            ult = recentes[-1]
            ach.aviso(
                "buraco_calendario", key, label,
                f"{label}: {len(recentes)} intervalo(s) sem publicacao no ultimo ano; "
                f"o mais recente entre {_dbr(ult[0])} e {_dbr(ult[1])} "
                f"({ult[2]} semana(s) sem dado)."
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

    if parados:
        quando = _dbr(parados[0][1])
        quais = "todos os produtos" if len(parados) == len(products) \
            else ", ".join(l for l, _ in parados)
        ach.info(
            "sem_semana_nova", None, None,
            f"A ANP nao publicou semana nova desde {quando} ({quais}). "
            f"O painel segue mostrando a ultima semana disponivel."
        )


# --------------------------------------------------------------------------- #
# degrau artificial na virada de aliquota
# --------------------------------------------------------------------------- #
def total_faixa(f: dict) -> float:
    return round(float(f["total"]) if "total" in f
                 else f.get("pis", 0) + f.get("cofins", 0) + f.get("cide", 0), 6)


def checar_degraus(out_dir: Path, ach: Achados,
                   defas: dict | None = None, cfg: dict | None = None) -> list:
    """Procura a assinatura de aliquota errada na virada de faixa.

    Se aplicamos a aliquota nova quando a antiga ainda valia, o preco de
    realizacao reconstruido erra por exatamente a diferenca entre as duas, e a
    defasagem da um salto de -d_taxa na data do corte. O teste mede o quanto o
    salto observado se parece com esse valor:

        residuo = |salto + d_taxa|      razao = residuo / |d_taxa|

    Perto de zero, a assinatura bate e a aliquota provavelmente esta errada.
    Muito acima de 1, o mercado se moveu tanto no periodo que o teste nao
    consegue concluir nada -- e isso e dito, em vez de virar um falso positivo.
    E a unica conferencia disponivel para o QAV, que a Sintese nao cobre.

    defas e cfg podem ser injetados; sem eles, sao lidos do disco.
    """
    degraus = []
    if defas is None or cfg is None:
        try:
            with open(out_dir / "defasagem.json", encoding="utf-8") as fh:
                defas = json.load(fh)
            with open(Path(__file__).resolve().parent / "tributos.json",
                      encoding="utf-8") as fh:
                cfg = json.load(fh)
        except (FileNotFoundError, json.JSONDecodeError):
            return degraus

    por_produto = {}
    for f in cfg["faixas"]:
        por_produto.setdefault(f["produto"], []).append(f)
    for v in por_produto.values():
        v.sort(key=lambda x: x["de"])

    for key, faixas in por_produto.items():
        p = (defas.get("products") or {}).get(key)
        if not p or len(faixas) < 2:
            continue
        weeks, label = p["weeks"], p.get("label", key)

        for anterior, atual in zip(faixas, faixas[1:]):
            corte = atual["de"]
            d_taxa = total_faixa(atual) - total_faixa(anterior)
            if abs(d_taxa) < 1e-6:
                continue

            antes = [w["gap"] for w in weeks if w["end"] < corte][-JANELA_DEGRAU:]
            depois = [w["gap"] for w in weeks if w["end"] >= corte][:JANELA_DEGRAU]
            if len(antes) < 2 or len(depois) < 2:
                if atual.get("presumido"):
                    ach.aviso(
                        "aliquota_presumida", key, label,
                        f"{label}: a aliquota vigente desde {_dbr(corte)} e presumida e "
                        f"ainda nao ha semanas suficientes depois do corte para o teste "
                        f"de continuidade rodar. Confirmar em fonte oficial."
                    )
                continue

            salto = median(depois) - median(antes)
            residuo = abs(salto + d_taxa)
            razao = residuo / abs(d_taxa)
            reg = {
                "produto": key, "label": label, "corte": corte,
                "taxa_antes": total_faixa(anterior), "taxa_depois": total_faixa(atual),
                "mudanca_taxa": round(d_taxa, 6),
                "salto_defasagem": round(salto, 6),
                "residuo": round(residuo, 6),
                "razao_residuo": round(razao, 3),
                "semanas_antes": len(antes), "semanas_depois": len(depois),
                "presumida": bool(atual.get("presumido")),
            }
            degraus.append(reg)

            if razao <= RESIDUO_ACUSA:
                ach.erro(
                    "degrau_aliquota", key, label,
                    f"{label}: na virada de aliquota de {_dbr(corte)} a defasagem saltou "
                    f"{_fmt(salto)} R$, que e quase exatamente o oposto da mudanca de "
                    f"aliquota ({_fmt(d_taxa)} R$). Essa e a assinatura de aliquota "
                    f"errada em scripts/tributos.json -- residuo de {_fmt(residuo)} R$, "
                    f"{razao:.0%} da mudanca."
                )
            elif atual.get("presumido"):
                if razao >= RESIDUO_INCONCLUSIVO:
                    ach.aviso(
                        "aliquota_presumida", key, label,
                        f"{label}: a aliquota vigente desde {_dbr(corte)} e presumida, nao "
                        f"confirmada em fonte oficial. O teste de continuidade e "
                        f"inconclusivo: o preco se moveu {razao:.0f} vezes mais que a "
                        f"mudanca de aliquota no periodo, entao ele nao confirma nem "
                        f"desmente a presuncao."
                    )
                else:
                    ach.aviso(
                        "aliquota_presumida", key, label,
                        f"{label}: a aliquota vigente desde {_dbr(corte)} e presumida, nao "
                        f"confirmada em fonte oficial. O teste de continuidade nao acusou "
                        f"a assinatura de erro (residuo de {_fmt(residuo)} R$), o que "
                        f"sustenta a presuncao sem confirma-la."
                    )
    return degraus


# --------------------------------------------------------------------------- #
def run(products: dict, anterior: dict | None, out_dir: Path) -> dict:
    ach = Achados()
    checar_ppi(products, anterior, ach)
    degraus = checar_degraus(out_dir, ach)

    ordem = {"erro": 0, "aviso": 1, "info": 2}
    ach.itens.sort(key=lambda x: (ordem[x["nivel"]], x["produto"] or ""))
    resumo = {n: sum(1 for i in ach.itens if i["nivel"] == n)
              for n in ("erro", "aviso", "info")}

    payload = {
        "generated_at": datetime.now(BR_TZ).isoformat(timespec="seconds"),
        "comparado_com": (anterior or {}).get("generated_at"),
        "limiares": {
            "variacao_aviso_pct": VAR_AVISO, "variacao_erro_pct": VAR_ERRO,
            "desvio_terminal_pct": DESVIO_TERMINAL,
            "cobertura_minima": COBERTURA_MIN,
            "residuo_acusa": RESIDUO_ACUSA,
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
