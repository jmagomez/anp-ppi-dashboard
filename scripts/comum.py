#!/usr/bin/env python3
"""
Peças compartilhadas pelo pipeline.

Existe porque `norm`, `as_number` e `download` estavam escritos duas vezes, em
build_data.py e em defasagem.py, e os formatadores de número, três vezes, com
nomes diferentes em cada módulo (`nfmt`/`_fmt`, `pfmt`/`_pct`, `dfmt`/`_dbr`).
Duas cópias de uma regra de parsing são duas chances de corrigir só uma.
"""

from __future__ import annotations

import json
import re
import unicodedata
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

BR_TZ = timezone(timedelta(hours=-3))

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; anp-ppi-dashboard/1.0)",
    "Accept": "*/*",
}


def agora_br() -> datetime:
    return datetime.now(BR_TZ)


def hoje_br() -> date:
    """Data corrente em Brasília.

    O runner do GitHub roda em UTC: na execução de segunda às 23:00 UTC o
    `date.today()` já é terça, enquanto o resto do pipeline carimba tudo em
    BRT. Duas noções de "hoje" no mesmo processo sempre divergem em algum caso
    de borda.
    """
    return agora_br().date()


# --------------------------------------------------------------------------- #
# texto e número
# --------------------------------------------------------------------------- #
def norm(texto) -> str:
    """Minúsculo, sem acentos, sem espaço duplicado."""
    if texto is None:
        return ""
    s = str(texto).replace("\xa0", " ").strip()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s).lower()


def limpar(texto) -> str:
    if texto is None:
        return ""
    return re.sub(r"\s+", " ", str(texto).replace("\xa0", " ")).strip()


def as_number(v):
    """Converte célula de planilha em float.

    Tolera vírgula decimal e separador de milhar brasileiros, "R$" e os marcadores
    de ausência que a ANP usa. Zero é valor, não ausência: confundir os dois
    estraga a média da semana.
    """
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
        f = float(s)
    except ValueError:
        return None
    return None if f != f else f


def media(vals):
    v = [x for x in vals if x is not None and x == x]
    return sum(v) / len(v) if v else None


# --------------------------------------------------------------------------- #
# semanas
# --------------------------------------------------------------------------- #
def semana_chave(iso: str) -> str:
    """Segunda-feira da semana ISO que contém a data.

    O PPI fecha a semana na sexta e a planilha de produtores usa outro
    fechamento. Ancorar as duas na segunda-feira da semana ISO é o que faz o
    pareamento funcionar; sem isso as séries se cruzam deslocadas de uma semana
    e a defasagem sai errada sem nenhum sintoma visível.
    """
    d = date.fromisoformat(iso)
    return (d - timedelta(days=d.weekday())).isoformat()


# --------------------------------------------------------------------------- #
# formatação (pt-BR)
# --------------------------------------------------------------------------- #
def fmt_num(v, casas=2) -> str:
    if v is None:
        return "—"
    s = f"{v:,.{casas}f}"
    return s.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def fmt_pct(v, casas=2) -> str:
    if v is None:
        return "—"
    return ("+" if v > 0 else "") + fmt_num(v, casas) + "%"


def fmt_data(iso) -> str:
    return date.fromisoformat(iso).strftime("%d/%m/%Y")


def casas_da_unidade(unidade: str) -> int:
    return 4 if "litro" in (unidade or "").lower() else 2


# --------------------------------------------------------------------------- #
# rede
# --------------------------------------------------------------------------- #
def baixar(url: str, minimo: int = 5000, tentativas: int = 4, timeout: int = 180) -> bytes:
    ultimo = None
    for n in range(tentativas):
        try:
            r = requests.get(url, headers=HEADERS, timeout=timeout)
            r.raise_for_status()
            if len(r.content) < minimo:
                raise RuntimeError(f"arquivo truncado: {len(r.content)} bytes")
            return r.content
        except Exception as exc:  # noqa: BLE001
            ultimo = exc
            print(f"  tentativa {n + 1} falhou: {exc}")
    raise RuntimeError(f"download falhou em {url}: {ultimo}")


# --------------------------------------------------------------------------- #
# escrita
# --------------------------------------------------------------------------- #
def escrever_json(caminho: Path, payload: dict, ignorar=("generated_at",)) -> bool:
    """Grava só se o conteúdo mudou. Devolve True se gravou.

    O pipeline passou a rodar todo dia, mas o dado da ANP muda uma vez por
    semana. Como `generated_at` era reescrito a cada execução, o `git diff`
    nunca ficava vazio e saía um commit por dia: 105 commits de dados para umas
    15 semanas de dado real. Pior que o ruído, isso apagava a informação mais
    útil do histórico -- quando o dado efetivamente mudou.

    Ignorando os campos de carimbo na comparação, o arquivo só é tocado quando
    há conteúdo novo, e `generated_at` passa a significar "quando este dado foi
    gerado" em vez de "quando o robô passou por aqui".
    """
    def sem_carimbo(d):
        return {k: v for k, v in d.items() if k not in ignorar}

    if caminho.exists():
        try:
            with open(caminho, encoding="utf-8") as fh:
                antigo = json.load(fh)
            if sem_carimbo(antigo) == sem_carimbo(payload):
                return False
        except (json.JSONDecodeError, OSError):
            pass  # ilegível: reescreve

    indent = None if caminho.name in {"ppi.json", "defasagem.json"} else 1
    sep = (",", ":") if indent is None else None
    with open(caminho, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=indent, separators=sep)
    return True


def escrever_csv(caminho: Path, linhas) -> bool:
    """Mesma ideia do escrever_json, para os CSV."""
    import csv
    import io

    buf = io.StringIO()
    csv.writer(buf, lineterminator="\r\n").writerows(linhas)
    novo = buf.getvalue()
    if caminho.exists():
        try:
            # newline="" nos dois lados: sem isso a leitura traduz \r\n para \n,
            # a comparacao nunca bate e o arquivo e reescrito toda execucao --
            # exatamente o defeito que esta funcao existe para evitar.
            with open(caminho, encoding="utf-8", newline="") as fh:
                if fh.read() == novo:
                    return False
        except OSError:
            pass
    with open(caminho, "w", encoding="utf-8", newline="") as fh:
        fh.write(novo)
    return True
