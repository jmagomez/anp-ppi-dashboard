#!/usr/bin/env python3
"""
Defasagem PPI x preco de realizacao.

Preco interno: ANP, "Precos Medios Ponderados Semanais" praticados por
produtores e importadores de derivados de petroleo e biodiesel (serie semanal
a partir de 2013), coluna Brasil.

Compatibilizacao de base tributaria
-----------------------------------
O PPI da ANP e publicado sem tributos. A planilha de produtores inclui Cide,
PIS/Pasep e Cofins (exclui ICMS). Para comparar as duas series deduzimos os
tributos federais ad rem definidos em scripts/tributos.json.

A premissa e conferida a cada execucao contra os valores de REALIZACAO
publicados pela propria ANP na Sintese Semanal de Precos (ver a secao
validacao_sintese em docs/data/_debug_ppidp.json). Semanas anteriores ao
inicio da serie de aliquotas ficam sem defasagem, em vez de receberem um
numero calculado sobre base desconhecida.

QAV fica de fora: seus tributos federais nao sao ad rem, entao nao ha valor
por litro a deduzir.

Saidas em docs/data/:
  defasagem.json     series pareadas PPI x realizacao + defasagem
  defasagem.csv      mesma coisa em formato longo
  _debug_ppidp.json  diagnostico da extracao e das validacoes
"""

from __future__ import annotations

import csv
import json
import re
import unicodedata
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

PPIDP_URL = (
    "https://www.gov.br/anp/pt-br/assuntos/precos-e-defesa-da-concorrencia/"
    "precos/ppidp/precos-medios-ponderados-semanais-2013.xls"
)
PPIDP_PAGE = (
    "https://www.gov.br/anp/pt-br/assuntos/precos-e-defesa-da-concorrencia/"
    "precos/precos-de-produtores-e-importadores-de-derivados-de-petroleo-e-biodiesel"
)
SINTESE_PAGE = (
    "https://www.gov.br/anp/pt-br/assuntos/precos-e-defesa-da-concorrencia/"
    "precos/sintese-semanal-de-precos"
)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; anp-ppi-dashboard/1.0)",
    "Accept": "*/*",
}

BR_TZ = timezone(timedelta(hours=-3))
COL_PRODUTO, COL_INI, COL_FIM, COL_BRASIL = 0, 1, 2, 8

# chave do PPI -> (regex do produto na planilha de produtores, fator de unidade)
MATCH = {
    "gasolina": (r"^gasolina a comum", 1.0),
    "diesel": (r"^oleo diesel s-?10", 1.0),
    "glp": (r"^gas liquefeito de petroleo", 13.0),
}

# Valores de REALIZACAO publicados pela ANP na Sintese Semanal de Precos,
# edicao 13/2026. Usados para conferir base tributaria e ordem de grandeza.
# chave -> {segunda-feira da semana ISO: valor}
SINTESE_REF = {
    "gasolina": {"2026-02-23": 2.70, "2026-03-02": 2.85,
                 "2026-03-09": 2.68, "2026-03-16": 3.58},
    "diesel": {"2026-02-23": 3.59, "2026-03-02": 4.71,
               "2026-03-09": 5.36, "2026-03-16": 5.29},
    "glp": {"2026-02-23": 36.71, "2026-03-02": 39.03,
            "2026-03-09": 36.02, "2026-03-16": 35.91},
}
SINTESE_PPI_REF = {
    "gasolina": {"2026-03-16": 3.92},
    "diesel": {"2026-03-16": 6.01},
    "glp": {"2026-03-16": 48.02},
}


def semana_chave(iso: str) -> str:
    """Segunda-feira da semana ISO que contem a data.

    O PPI fecha a semana na sexta e a planilha de produtores usa outro
    fechamento; ancorar as duas na segunda-feira da semana ISO faz o
    pareamento correto.
    """
    d = date.fromisoformat(iso)
    return (d - timedelta(days=d.weekday())).isoformat()


def norm(text) -> str:
    if text is None:
        return ""
    s = str(text).replace("\xa0", " ").strip()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s).lower()


def download(url: str) -> bytes:
    last = None
    for attempt in range(3):
        try:
            r = requests.get(url, headers=HEADERS, timeout=180)
            r.raise_for_status()
            if len(r.content) < 5000:
                raise RuntimeError(f"arquivo truncado: {len(r.content)} bytes")
            return r.content
        except Exception as exc:  # noqa: BLE001
            last = exc
            print(f"  [ppidp] tentativa {attempt + 1} falhou: {exc}")
    raise RuntimeError(f"download falhou: {last}")


def as_number(v):
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        f = float(v)
        return None if f != f else f
    s = str(v).strip()
    if not s or set(s) <= {"*", "-", "."} or s.lower() in {"n/d", "nd"}:
        return None
    s = s.replace("R$", "").replace(" ", "")
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


# --------------------------------------------------------------------------- #
# tributos
# --------------------------------------------------------------------------- #
def carregar_tributos() -> dict:
    path = Path(__file__).resolve().parent / "tributos.json"
    with open(path, encoding="utf-8") as fh:
        cfg = json.load(fh)
    faixas = {}
    for f in cfg["faixas"]:
        if "total" in f:
            total = round(float(f["total"]), 6)
        else:
            total = round(f.get("pis", 0) + f.get("cofins", 0) + f.get("cide", 0), 6)
        faixas.setdefault(f["produto"], []).append(
            {"de": f["de"], "ate": f.get("ate"), "total": total, "fonte": f.get("fonte")}
        )
    for v in faixas.values():
        v.sort(key=lambda x: x["de"])
    return {"inicio_serie": cfg["inicio_serie"], "faixas": faixas}


def deducao(trib: dict, produto: str, semana_iso: str):
    """Tributo federal ad rem vigente na semana, ou None se fora de cobertura."""
    for f in trib["faixas"].get(produto, []):
        if semana_iso >= f["de"] and (f["ate"] is None or semana_iso <= f["ate"]):
            return f["total"]
    return None


# --------------------------------------------------------------------------- #
# parsing
# --------------------------------------------------------------------------- #
def parse_ppidp(blob: bytes) -> tuple[dict, dict]:
    import xlrd

    wb = xlrd.open_workbook(file_contents=blob)
    ws = wb.sheet_by_index(0)
    datemode = wb.datemode

    def to_iso(v):
        n = as_number(v)
        if n is None or n < 20000:
            return None
        try:
            y, m, d, *_ = xlrd.xldate_as_tuple(n, datemode)
            return date(y, m, d).isoformat()
        except Exception:  # noqa: BLE001
            return None

    produtos: dict[str, dict[str, float]] = {}
    rotulos: dict[str, str] = {}

    for r in range(9, ws.nrows):
        raw = ws.cell_value(r, COL_PRODUTO)
        label = str(raw).strip() if raw is not None else ""
        if not label:
            continue
        key = norm(label)
        fim = to_iso(ws.cell_value(r, COL_FIM))
        if not fim:
            continue
        val = as_number(ws.cell_value(r, COL_BRASIL))
        if val is None:
            continue
        produtos.setdefault(key, {})[semana_chave(fim)] = val
        rotulos.setdefault(key, label)

    return ({"series": produtos, "rotulos": rotulos},
            {"linhas": ws.nrows, "produtos_encontrados": sorted(rotulos.values())})


def escolher(produtos: dict, pattern: str) -> str | None:
    rx = re.compile(pattern)
    hits = [k for k in produtos if rx.search(k)]
    return sorted(hits, key=len)[0] if hits else None


def build(products: dict, raw: dict, trib: dict) -> tuple[dict, dict]:
    series, rotulos = raw["series"], raw["rotulos"]
    out, checks = {}, {}
    inicio = trib["inicio_serie"]

    for key, (pattern, fator) in MATCH.items():
        ppi = products.get(key)
        if not ppi:
            continue
        pkey = escolher(series, pattern)
        if not pkey:
            checks[key] = {"status": "produto nao encontrado na planilha"}
            continue

        real = series[pkey]
        weeks, sem_cobertura = [], 0

        for w in ppi["weeks"]:
            chave = semana_chave(w["end"])
            bruto = real.get(chave)
            if bruto is None:
                continue
            vals = [v for v in w["v"] if v is not None]
            if not vals:
                continue
            ppi_med = sum(vals) / len(vals)

            ded = deducao(trib, key, chave) if chave >= inicio else None
            if ded is None:
                sem_cobertura += 1
                continue

            r = (bruto - ded) * fator
            weeks.append({
                "end": w["end"],
                "label": w["label"],
                "ppi": round(ppi_med, 6),
                "real": round(r, 6),
                "bruto": round(bruto * fator, 6),
                "gap": round(r - ppi_med, 6),
                "gap_pct": round((r / ppi_med - 1) * 100, 4) if ppi_med else None,
            })

        weeks.sort(key=lambda x: x["end"])
        if not weeks:
            checks[key] = {"status": "sem semanas cobertas", "produto": rotulos[pkey]}
            continue

        out[key] = {
            "key": key,
            "label": ppi["label"],
            "unit": ppi["unit"],
            "fonte_realizacao": rotulos[pkey],
            "fator": fator,
            "weeks": weeks,
        }
        last = weeks[-1]
        checks[key] = {
            "status": "ok",
            "produto": rotulos[pkey],
            "semanas": len(weeks),
            "semanas_sem_cobertura": sem_cobertura,
            "ultima": last["end"],
            "ppi": last["ppi"],
            "realizacao": last["real"],
            "defasagem_pct": last["gap_pct"],
        }

    return out, checks


def validar(data: dict, raw: dict, trib: dict) -> dict:
    """Confere contra os valores publicados pela ANP e mede o tributo implicito."""
    res = {}
    for key, refs in SINTESE_REF.items():
        pkey = escolher(raw["series"], MATCH[key][0]) if key in MATCH else None
        if not pkey:
            res[key] = {"status": "produto ausente"}
            continue
        fator = MATCH[key][1]
        itens = []
        for semana, esperado in sorted(refs.items()):
            bruto = raw["series"][pkey].get(semana)
            if bruto is None:
                itens.append({"semana": semana, "status": "semana ausente"})
                continue
            ded = deducao(trib, key, semana)
            calc = (bruto - (ded or 0)) * fator
            itens.append({
                "semana": semana,
                "anp": esperado,
                "calculado": round(calc, 4),
                "erro_pct": round((calc / esperado - 1) * 100, 2) if esperado else None,
                "tributo_aplicado": ded,
                "tributo_implicito": round(bruto - esperado / fator, 4),
            })
        ppi_chk = []
        p = data.get(key)
        if p:
            for semana, esperado in SINTESE_PPI_REF.get(key, {}).items():
                w = next((x for x in p["weeks"] if semana_chave(x["end"]) == semana), None)
                if w:
                    ppi_chk.append({
                        "semana": semana, "anp": esperado, "calculado": w["ppi"],
                        "erro_pct": round((w["ppi"] / esperado - 1) * 100, 2),
                    })
        res[key] = {"realizacao": itens, "ppi": ppi_chk}
    return res


def write_outputs(data: dict, trib: dict, out_dir: Path) -> None:
    payload = {
        "generated_at": datetime.now(BR_TZ).isoformat(timespec="seconds"),
        "source_file": PPIDP_URL,
        "source_page": PPIDP_PAGE,
        "sintese_page": SINTESE_PAGE,
        "inicio_serie": trib["inicio_serie"],
        "metodologia": (
            "Defasagem = preco de realizacao menos PPI, ambos sem tributos. "
            "O preco de realizacao e o preco medio ponderado semanal de produtores "
            "e importadores publicado pela ANP (coluna Brasil), do qual sao "
            "deduzidos os tributos federais ad rem (PIS/Pasep, Cofins e Cide) "
            "listados em scripts/tributos.json. Valor negativo indica preco interno "
            "abaixo da paridade de importacao."
        ),
        "order": [k for k in MATCH if k in data],
        "products": data,
    }
    with open(out_dir / "defasagem.json", "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))

    rows = [("produto", "unidade", "semana_fim", "ppi", "realizacao",
             "defasagem_abs", "defasagem_pct")]
    for p in data.values():
        for w in p["weeks"]:
            rows.append((p["label"], p["unit"], w["end"],
                         f"{w['ppi']:.6f}", f"{w['real']:.6f}", f"{w['gap']:.6f}",
                         "" if w["gap_pct"] is None else f"{w['gap_pct']:.4f}"))
    with open(out_dir / "defasagem.csv", "w", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerows(rows)


def run(products: dict, out_dir: Path) -> None:
    """Executado ao final do build principal. Nunca levanta excecao."""
    debug = {"source_file": PPIDP_URL, "source_page": PPIDP_PAGE}
    try:
        trib = carregar_tributos()
        print(f"[ppidp] baixando {PPIDP_URL}")
        blob = download(PPIDP_URL)
        print(f"[ppidp] {len(blob):,} bytes")

        raw, diag = parse_ppidp(blob)
        debug.update(diag)

        data, checks = build(products, raw, trib)
        debug["inicio_serie"] = trib["inicio_serie"]
        debug["checagens"] = checks
        debug["validacao_sintese"] = validar(data, raw, trib)

        if data:
            write_outputs(data, trib, out_dir)
            for k, c in checks.items():
                if c.get("status") == "ok":
                    print(f"[ppidp] {k:<9} {c['semanas']:>4} semanas | ate {c['ultima']} "
                          f"| PPI {c['ppi']:.4f} vs realizacao {c['realizacao']:.4f} "
                          f"| defasagem {c['defasagem_pct']:+.2f}%")
                else:
                    print(f"[ppidp] {k:<9} {c['status']}")
        else:
            debug["error"] = "nenhum produto pareado"
            print("[ppidp] nenhum produto pareado")
    except Exception as exc:  # noqa: BLE001
        debug["error"] = f"{type(exc).__name__}: {exc}"
        print(f"[ppidp] ERRO: {debug['error']}")

    with open(out_dir / "_debug_ppidp.json", "w", encoding="utf-8") as fh:
        json.dump(debug, fh, ensure_ascii=False, indent=1)
