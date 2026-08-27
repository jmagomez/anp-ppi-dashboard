"""Contrato dos arquivos que o dashboard busca em runtime.

Um JSON corrompido aqui nao levanta erro em lugar nenhum: o fetch do navegador
falha, o boot() para no meio e a pagina fica presa em "Carregando dados da ANP".
Testar so se o arquivo e JSON valido nao basta -- o que quebra a pagina e campo
faltando, entao o teste confere a forma que o index.html realmente le.
"""

import json
from pathlib import Path

import pytest

DADOS = Path(__file__).resolve().parents[1] / "docs" / "data"


def carregar(nome):
    caminho = DADOS / nome
    if not caminho.exists():
        pytest.skip(f"{nome} ainda nao foi gerado")
    with open(caminho, encoding="utf-8") as fh:
        return json.load(fh)


class TestPpiJson:
    def test_estrutura(self):
        d = carregar("ppi.json")
        assert d["order"], "ordem dos produtos vazia"
        assert d["latest_week_end"]
        for key in d["order"]:
            p = d["products"][key]
            assert p["weeks"], f"{key} sem semanas"
            assert p["locations"], f"{key} sem terminais"
            assert p["unit"]

    def test_semanas_ordenadas_e_sem_repeticao(self):
        d = carregar("ppi.json")
        for key, p in d["products"].items():
            fins = [w["end"] for w in p["weeks"]]
            assert fins == sorted(fins), f"{key}: semanas fora de ordem"
            assert len(fins) == len(set(fins)), f"{key}: semana repetida"

    def test_todo_valor_bate_com_a_lista_de_terminais(self):
        d = carregar("ppi.json")
        for key, p in d["products"].items():
            n = len(p["locations"])
            for w in p["weeks"]:
                assert len(w["v"]) == n, f"{key}/{w['end']}: {len(w['v'])} valores para {n} terminais"


class TestDefasagemJson:
    def test_estrutura(self):
        d = carregar("defasagem.json")
        assert d["inicio_serie"]
        for key in d["order"]:
            p = d["products"][key]
            assert p["weeks"]
            for w in p["weeks"][:5]:
                assert {"end", "ppi", "real", "gap"} <= set(w)

    def test_defasagem_e_realizacao_menos_ppi(self):
        # A identidade que da nome ao numero. Se ela deixar de valer, alguem
        # mexeu no calculo sem perceber.
        d = carregar("defasagem.json")
        for key, p in d["products"].items():
            for w in p["weeks"][-20:]:
                assert w["gap"] == pytest.approx(w["real"] - w["ppi"], abs=1e-4), \
                    f"{key}/{w['end']}"

    def test_resumo_da_conferencia_esta_publicado(self):
        # O dashboard le daqui em vez de exibir um numero fixo.
        d = carregar("defasagem.json")
        v = d.get("validacao")
        assert v, "defasagem.json sem o resumo da conferencia"
        assert v["n_pontos"] > 0
        assert v["erro_max_pct"] is not None
        assert "natureza" in v

    def test_conferencia_dentro_da_tolerancia(self):
        # Erro acima de 1% contra a Sintese da ANP significa aliquota errada ou
        # pareamento deslocado -- nao arredondamento.
        d = carregar("defasagem.json")
        assert d["validacao"]["erro_max_pct"] < 1.0


class TestQualidadeJson:
    def test_estrutura(self):
        d = carregar("qualidade.json")
        assert set(d["resumo"]) == {"erro", "aviso", "info"}
        for a in d["achados"]:
            assert a["nivel"] in {"erro", "aviso", "info"}
            assert a["texto"]

    def test_execucao_publicada_nao_tem_erro_de_integridade(self):
        # Erro aqui e serie que encolheu, valor negativo ou degrau de aliquota:
        # coisas que nao deveriam ter chegado ao ar.
        d = carregar("qualidade.json")
        erros = [a["texto"] for a in d["achados"] if a["nivel"] == "erro"]
        assert not erros, erros


class TestMetaJson:
    def test_bate_com_ppi_json(self):
        meta, ppi = carregar("meta.json"), carregar("ppi.json")
        assert meta["latest_week_end"] == ppi["latest_week_end"]
        for key, m in meta["products"].items():
            assert m["weeks"] == len(ppi["products"][key]["weeks"])
