#!/usr/bin/env python3
"""
Defasagem PPI x preco de realizacao.

Preco interno: ANP, "Precos Medios Ponderados Semanais" praticados por
produtores e importadores de derivados de petroleo e biodiesel (serie
semanal a partir de 2013), coluna Brasil.

A Sintese Semanal de Precos da ANP usa exatamente essa fonte para a linha
"REALIZACAO" e a descreve como "livres de tributos", mesma base do PPI
("Todos os precos divulgados nao incluem tributos"). O modulo checa essa
premissa numericamente e registra o resultado em _debug_ppidp.json.

Saidas em docs/data/:
  defasagem.json       series pareadas PPI x realizacao + defasagem
  defasagem.csv        mesma coisa em formato longo
  _debug_ppidp.json    diagnostico da extracao (produtos, amostras, checagens)
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

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; anp-ppi-dashboard/1.0)",
    "Accept": "*/*",
}

BR_TZ = timezone(timedelta(hours=-3))

# Coluna "Brasil" na planilha da ANP (0-based).
COL_PRODUTO, COL_INI, COL_FIM, COL_BRASIL = 0, 1, 2, 8

# chave do PPI -> (regex do produto na planilha de produtores, fator de conversao)
# GLP: planilha em R$/kg, PPI em R$/13kg.
MATCH = {
    "gasolina": (r"^gasolina a\b", 1.0),
    "diesel": (r"^oleo diesel a s-?10\b|^diesel a s-?10\b|^oleo diesel a\b|^oleo diesel\b", 1.0),
    "qav": (r"querosene de aviacao", 1.0),
    "glp": (r"gas liquefeito de petroleo", 13.0),
}

# Tributos federais ad rem (R$/l), caso a serie venha COM tributos.
# Fonte: Sintese Semanal de Precos da ANP, nota (9).
# Mantido apenas como referencia auditavel; so e aplicado se
# APLICAR_DEDUCAO_TRIBUTOS = True.
TRIBUTOS_FEDERAIS = {
    "gasolina": {"pis": 0.1411, "cofins": 0.6514, "cide": 0.10},
    "diesel": {"pis": 0.0, "cofins": 0.0, "cide": 0.0},
}
APLICAR_DEDUCAO_TRIBUTOS = False


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


def parse_ppidp(blob: bytes) -> tuple[dict, dict]:
    """Retorna (series_por_produto_bruto, diagnostico)."""
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
        produtos.setdefault(key, {})[fim] = val
        rotulos.setdefault(key, label)

    diag = {
        "linhas": ws.nrows,
        "produtos_encontrados": sorted(rotulos.values()),
    }
    return {"series": produtos, "rotulos": rotulos}, diag


def escolher(produtos: dict, pattern: str) -> str | None:
    """Primeiro produto cujo nome normalizado casa com o padrao."""
    rx = re.compile(pattern)
    hits = [k for k in produtos if rx.search(k)]
    if not hits:
        return None
    # prefere o rotulo mais curto (menos qualificadores)
    return sorted(hits, key=len)[0]


def build(products: dict, raw: dict) -> tuple[dict, dict]:
    series, rotulos = raw["series"], raw["rotulos"]
    out, checks = {}, {}

    for key, (pattern, fator) in MATCH.items():
        ppi = products.get(key)
        if not ppi:
            continue
        pkey = escolher(series, pattern)
        if not pkey:
            checks[key] = {"status": "produto nao encontrado na planilha"}
            continue

        real = series[pkey]
        ded = 0.0
        if APLICAR_DEDUCAO_TRIBUTOS and key in TRIBUTOS_FEDERAIS:
            ded = sum(TRIBUTOS_FEDERAIS[key].values())

        weeks = []
        for w in ppi["weeks"]:
            vals = [v for v in w["v"] if v is not None]
            if not vals:
                continue
            ppi_med = sum(vals) / len(vals)
            r = real.get(w["end"])
            if r is None:
                continue
            r = (r - ded) * fator
            weeks.append({
                "end": w["end"],
                "label": w["label"],
                "ppi": round(ppi_med, 6),
                "real": round(r, 6),
                "gap": round(r - ppi_med, 6),
                "gap_pct": round((r / ppi_med - 1) * 100, 4) if ppi_med else None,
            })

        weeks.sort(key=lambda x: x["end"])
        if not weeks:
            checks[key] = {"status": "sem semanas em comum", "produto": rotulos[pkey]}
            continue

        out[key] = {
            "key": key,
            "label": ppi["label"],
            "unit": ppi["unit"],
            "fonte_realizacao": rotulos[pkey],
            "fator": fator,
            "deducao_tributos": round(ded, 6),
            "weeks": weeks,
        }
        last = weeks[-1]
        checks[key] = {
            "status": "ok",
            "produto": rotulos[pkey],
            "semanas": len(weeks),
            "ultima": last["end"],
            "ppi": last["ppi"],
            "realizacao": last["real"],
            "defasagem_pct": last["gap_pct"],
            "amostra": weeks[-5:],
        }

    return out, checks


def write_outputs(data: dict, out_dir: Path) -> None:
    payload = {
        "generated_at": datetime.now(BR_TZ).isoformat(timespec="seconds"),
        "source_file": PPIDP_URL,
        "source_page": PPIDP_PAGE,
        "base_tributaria": (
            "Ambas as series sem tributos: o PPI da ANP nao inclui tributos e a "
            "Sintese Semanal da ANP descreve os precos de produtores/importadores "
            "como livres de tributos."
        ),
        "deducao_aplicada": APLICAR_DEDUCAO_TRIBUTOS,
        "order": [k for k in MATCH if k in data],
        "products": data,
    }
    with open(out_dir / "defasagem.json", "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))

    rows = [("produto", "unidade", "semana_fim", "ppi", "realizacao",
             "defasagem_abs", "defasagem_pct")]
    for key, p in data.items():
        for w in p["weeks"]:
            rows.append((p["label"], p["unit"], w["end"],
                         f"{w['ppi']:.6f}", f"{w['real']:.6f}",
                         f"{w['gap']:.6f}",
                         "" if w["gap_pct"] is None else f"{w['gap_pct']:.4f}"))
    with open(out_dir / "defasagem.csv", "w", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerows(rows)


def run(products: dict, out_dir: Path) -> None:
    """Executado ao final do build principal. Nunca levanta excecao."""
    debug = {"source_file": PPIDP_URL, "source_page": PPIDP_PAGE}
    try:
        print(f"[ppidp] baixando {PPIDP_URL}")
        blob = download(PPIDP_URL)
        print(f"[ppidp] {len(blob):,} bytes")

        raw, diag = parse_ppidp(blob)
        debug.update(diag)

        data, checks = build(products, raw)
        debug["checagens"] = checks

        if data:
            write_outputs(data, out_dir)
            for k, c in checks.items():
                if c.get("status") == "ok":
                    print(f"[ppidp] {k:<9} {c['semanas']:>4} semanas | ate {c['ultima']} "
                          f"| PPI {c['ppi']:.4f} vs realizacao {c['real'] if 'real' in c else c['realizacao']:.4f} "
                          f"| defasagem {c['defasagem_pct']:.2f}%")
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
