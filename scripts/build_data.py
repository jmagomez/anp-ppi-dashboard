#!/usr/bin/env python3
"""
Coleta o PPI da ANP e orquestra o pipeline.

Baixa a planilha de Preços de Paridade de Importação, normaliza as 4 abas
semanais e chama, na ordem, o cálculo da defasagem e as checagens de sanidade.

Saídas (em docs/data/):
  ppi.json          dataset completo consumido pelo dashboard
  ppi_long.csv      série longa dos 4 produtos
  meta.json         metadados da última execução

Fonte: https://www.gov.br/anp/pt-br/assuntos/precos-e-defesa-da-concorrencia/
       precos/precos-de-paridade-de-importacao
"""

from __future__ import annotations

import hashlib
import io
import json
import re
from datetime import datetime
from pathlib import Path

from openpyxl import load_workbook

from comum import (
    agora_br, as_number, escrever_csv, escrever_json, limpar, norm, baixar,
)

XLSX_URL = (
    "https://www.gov.br/anp/pt-br/assuntos/precos-e-defesa-da-concorrencia/"
    "precos/arq-ppi/ppi.xlsx"
)
PAGE_URL = (
    "https://www.gov.br/anp/pt-br/assuntos/precos-e-defesa-da-concorrencia/"
    "precos/precos-de-paridade-de-importacao"
)

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "docs" / "data"
RAW_DIR = ROOT / "data_raw"

DATE_RE = re.compile(r"(\d{2})/(\d{2})/(\d{4})")
HEADER_LABELS = {"data", "semana"}

# Aba na planilha -> (chave interna, rótulo curto)
SHEETS = [
    ("Gasolina R$ semanal", "gasolina", "Gasolina A"),
    ("Diesel R$ semanal", "diesel", "Diesel A S10"),
    ("QAV R$ semanal", "qav", "QAV"),
    ("GLP R$ kg semanal", "glp", "GLP"),
]


# --------------------------------------------------------------------------- #
# parsing
# --------------------------------------------------------------------------- #
def find_header(rows):
    """Linha de cabeçalho ('Data' ou 'Semana') e índice da coluna de data."""
    for i, row in enumerate(rows[:15]):
        for j, cell in enumerate(row):
            if norm(cell) in HEADER_LABELS:
                return i, j
    raise ValueError("cabecalho ('Data'/'Semana') nao encontrado")


def read_locations(header_row, date_col: int):
    """Terminais = células não vazias após a data, até a primeira lacuna.

    A ANP repete os mesmos terminais num segundo bloco (variação % semanal),
    separado por uma coluna em branco; paramos na lacuna.
    """
    locations = []
    for j in range(date_col + 1, len(header_row)):
        name = limpar(header_row[j])
        if not name:
            break
        locations.append({"col": j, "name": name})
    if not locations:
        raise ValueError("nenhum terminal encontrado no cabecalho")
    return locations


def parse_period(raw):
    """'05/11/2018 a 09/11/2018' -> (início ISO, fim ISO, rótulo)."""
    if isinstance(raw, datetime):
        iso = raw.date().isoformat()
        return iso, iso, raw.strftime("%d/%m/%Y")
    label = limpar(raw)
    matches = DATE_RE.findall(label)
    if not matches:
        return None

    def iso(m):
        d, mth, y = m
        return f"{y}-{mth}-{d}"

    start, end = iso(matches[0]), iso(matches[-1])
    try:
        datetime.fromisoformat(start)
        datetime.fromisoformat(end)
    except ValueError:
        return None
    return start, end, label


def parse_sheet(ws, key: str, fallback_label: str) -> dict:
    rows = [list(r) for r in ws.iter_rows(values_only=True)]
    header_idx, date_col = find_header(rows)
    locations = read_locations(rows[header_idx], date_col)

    def first_text(idx):
        if idx < 0 or idx >= len(rows):
            return ""
        for cell in rows[idx]:
            txt = limpar(cell)
            if txt and norm(txt) != "variacao % em relacao a semana anterior":
                return txt
        return ""

    title = first_text(0) or fallback_label
    unit_raw = first_text(1)
    m = re.search(r"(R\$\s*/\s*[^\s,;]+)", unit_raw)
    unit = limpar(m.group(1)) if m else (unit_raw or "R$")

    weeks, seen = [], set()
    for row in rows[header_idx + 1:]:
        if date_col >= len(row):
            continue
        period = parse_period(row[date_col])
        if not period:
            continue
        start, end, label = period
        if end in seen:
            continue
        values = []
        for loc in locations:
            v = as_number(row[loc["col"]]) if loc["col"] < len(row) else None
            values.append(round(v, 6) if v is not None else None)
        if all(v is None for v in values):
            continue
        seen.add(end)
        weeks.append({"start": start, "end": end, "label": label, "v": values})

    weeks.sort(key=lambda w: w["end"])
    if not weeks:
        raise ValueError(f"aba '{ws.title}' sem linhas de dados validas")

    return {
        "key": key,
        "label": fallback_label,
        "title": title,
        "unit": unit,
        "sheet": ws.title,
        "locations": [loc["name"] for loc in locations],
        "weeks": weeks,
    }


# --------------------------------------------------------------------------- #
# saídas
# --------------------------------------------------------------------------- #
def escrever_serie_longa(products: dict) -> bool:
    """Um CSV só, com produto e unidade em coluna.

    Havia também um CSV por produto -- 0,79 MB republicados a cada mudança de
    dado, que ninguém lia: nem o dashboard, nem o README, nem os testes. O
    ppi_long.csv é o superconjunto exato deles.
    """
    linhas = [("produto", "unidade", "semana_inicio", "semana_fim", "terminal", "preco")]
    for p in products.values():
        for w in p["weeks"]:
            for name, valor in zip(p["locations"], w["v"]):
                if valor is None:
                    continue
                linhas.append((p["label"], p["unit"], w["start"], w["end"],
                               name, f"{valor:.6f}"))
    return escrever_csv(OUT_DIR / "ppi_long.csv", linhas)


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    RAW_DIR.mkdir(parents=True, exist_ok=True)

    # Guardado antes de sobrescrever: as checagens de sanidade comparam a
    # execução atual com a anterior para pegar série que encolhe ou retrocede.
    anterior = None
    _prev = OUT_DIR / "ppi.json"
    if _prev.exists():
        try:
            with open(_prev, encoding="utf-8") as fh:
                anterior = json.load(fh)
        except Exception as exc:  # noqa: BLE001
            print(f"[qualidade] execucao anterior ilegivel, seguindo sem comparar: {exc}")

    print(f"Baixando {XLSX_URL}")
    blob = baixar(XLSX_URL, minimo=10000)
    digest = hashlib.sha256(blob).hexdigest()
    print(f"  {len(blob):,} bytes | sha256 {digest[:16]}...")
    (RAW_DIR / "ppi.xlsx").write_bytes(blob)

    wb = load_workbook(io.BytesIO(blob), data_only=True, read_only=True)
    print(f"  abas encontradas: {wb.sheetnames}")

    products = {}
    for sheet_name, key, label in SHEETS:
        if sheet_name not in set(wb.sheetnames):
            token = norm(label.split()[0])
            candidatos = [s for s in wb.sheetnames if token in norm(s)]
            if not candidatos:
                raise SystemExit(f"aba '{sheet_name}' ausente na planilha da ANP")
            sheet_name = candidatos[0]
        products[key] = parse_sheet(wb[sheet_name], key, label)
        p = products[key]
        print(
            f"  {label:<14} {len(p['weeks']):>4} semanas | "
            f"{len(p['locations']):>2} terminais | ate {p['weeks'][-1]['end']}"
        )

    latest = max(p["weeks"][-1]["end"] for p in products.values())
    carimbo = agora_br().isoformat(timespec="seconds")

    escreveu = escrever_json(OUT_DIR / "ppi.json", {
        "generated_at": carimbo,
        "source_file": XLSX_URL,
        "source_page": PAGE_URL,
        "source_sha256": digest,
        "latest_week_end": latest,
        "order": [key for _, key, _ in SHEETS],
        "products": products,
    })

    escrever_json(OUT_DIR / "meta.json", {
        "generated_at": carimbo,
        "latest_week_end": latest,
        "source_sha256": digest,
        "products": {
            k: {
                "weeks": len(p["weeks"]),
                "locations": len(p["locations"]),
                "unit": p["unit"],
                "last_week": p["weeks"][-1]["label"],
            }
            for k, p in products.items()
        },
    })

    escrever_serie_longa(products)
    print(f"[ppi] {'conteudo novo' if escreveu else 'sem mudanca desde a execucao anterior'}"
          f" | ultima semana {latest}")

    # A defasagem devolve seus alertas em vez de publicá-los por conta própria:
    # existe uma lista de alertas no projeto, e ela mora em qualidade.json.
    alertas_defasagem = []
    try:
        import defasagem
        alertas_defasagem = defasagem.run(products, OUT_DIR)
    except Exception as exc:  # noqa: BLE001
        print(f"[defasagem] etapa ignorada: {type(exc).__name__}: {exc}")

    try:
        import qualidade
        qualidade.run(products, anterior, OUT_DIR, alertas_defasagem)
    except Exception as exc:  # noqa: BLE001
        print(f"[qualidade] etapa ignorada: {type(exc).__name__}: {exc}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
