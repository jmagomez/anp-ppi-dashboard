"""Testes do módulo compartilhado.

A escrita idempotente tem teste porque o defeito que ela corrige foi invisível
por sete semanas: o pipeline passou a rodar diariamente, `generated_at` mudava
a cada execução, o `git diff` nunca ficava vazio e saiu um commit por dia —
105 commits de dados para cerca de 15 semanas de dado real.
"""

import json

import pytest

import comum as c


class TestEscritaIdempotente:
    def test_nao_reescreve_quando_so_o_carimbo_muda(self, tmp_path):
        alvo = tmp_path / "x.json"
        assert c.escrever_json(alvo, {"generated_at": "A", "dado": 1}) is True
        antes = alvo.stat().st_mtime_ns

        assert c.escrever_json(alvo, {"generated_at": "B", "dado": 1}) is False
        assert alvo.stat().st_mtime_ns == antes
        # O carimbo preservado é o da geração do conteúdo, não o da última
        # vez que o robô passou — é essa a pergunta que o painel responde.
        assert json.loads(alvo.read_text(encoding="utf-8"))["generated_at"] == "A"

    def test_reescreve_quando_o_conteudo_muda(self, tmp_path):
        alvo = tmp_path / "x.json"
        c.escrever_json(alvo, {"generated_at": "A", "dado": 1})
        assert c.escrever_json(alvo, {"generated_at": "B", "dado": 2}) is True
        lido = json.loads(alvo.read_text(encoding="utf-8"))
        assert lido == {"generated_at": "B", "dado": 2}

    def test_arquivo_inexistente_e_sempre_escrita(self, tmp_path):
        assert c.escrever_json(tmp_path / "novo.json", {"a": 1}) is True

    def test_arquivo_corrompido_e_reescrito(self, tmp_path):
        alvo = tmp_path / "x.json"
        alvo.write_text("{ isto nao e json", encoding="utf-8")
        assert c.escrever_json(alvo, {"a": 1}) is True

    def test_csv_idempotente(self, tmp_path):
        alvo = tmp_path / "x.csv"
        linhas = [("a", "b"), ("1", "2")]
        assert c.escrever_csv(alvo, linhas) is True
        assert c.escrever_csv(alvo, linhas) is False
        assert c.escrever_csv(alvo, [("a", "b"), ("1", "3")]) is True


class TestFormatacao:
    @pytest.mark.parametrize("v,casas,esperado", [
        (1234.5678, 2, "1.234,57"),
        (3.6034, 4, "3,6034"),
        (-0.5, 2, "-0,50"),
        (None, 2, "—"),
    ])
    def test_numero_em_pt_br(self, v, casas, esperado):
        assert c.fmt_num(v, casas) == esperado

    def test_percentual_traz_sinal_explicito(self):
        assert c.fmt_pct(2.5) == "+2,50%"
        assert c.fmt_pct(-20.55) == "-20,55%"

    def test_data_em_pt_br(self):
        assert c.fmt_data("2026-10-09") == "09/10/2026"

    def test_casas_seguem_a_unidade(self):
        # Preço por litro anda na quarta casa; por 13 kg, na segunda.
        assert c.casas_da_unidade("R$/litro") == 4
        assert c.casas_da_unidade("R$/13kg") == 2


class TestMedia:
    def test_ignora_ausentes_mas_nao_zeros(self):
        assert c.media([1.0, None, 3.0]) == 2.0
        assert c.media([0.0, 2.0]) == 1.0

    def test_tudo_ausente_devolve_none(self):
        assert c.media([None, None]) is None
        assert c.media([]) is None
