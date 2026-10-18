"""O projeto tem UMA lista de alertas.

Havia dois sistemas paralelos: `avisos_cobertura`, nascido em defasagem.py e
publicado em defasagem.json, e `achados`, de qualidade.py, em qualidade.json.
Schemas diferentes, dois painéis no dashboard e uma costura ad hoc no e-mail
para juntar os dois. Estes testes impedem que voltem a divergir.
"""

from datetime import date

import defasagem as d
import qualidade as q

CHAVES = {"nivel", "codigo", "produto", "label", "texto"}
NIVEIS = {"erro", "aviso", "info"}


def trib_vencido():
    return {
        "inicio_serie": "2023-09-04",
        "faixas": {"qav": [{"de": "2023-09-04", "ate": "2026-07-31",
                            "total": 0.0712, "presumido": False}]},
    }


def produto_com_cobertura(**cob):
    base = {"sem_faixa": 0, "sem_faixa_recente": 0, "vigencia_ate": "2026-07-31"}
    base.update(cob)
    return {"qav": {"label": "QAV", "cobertura": base,
                    "weeks": [{"end": "2026-07-31", "gap": -1.0}]}}


class TestSchemaUnico:
    def test_alertas_de_cobertura_usam_o_schema_comum(self):
        alertas = d.alertas_cobertura(produto_com_cobertura(), trib_vencido(),
                                      date(2026, 10, 18))
        assert alertas, "vigencia vencida deveria alertar"
        for a in alertas:
            assert set(a) == CHAVES, a
            assert a["nivel"] in NIVEIS

    def test_alertas_de_qualidade_usam_o_schema_comum(self):
        ach = q.Alertas()
        ach.erro("codigo_x", "gasolina", "Gasolina A", "texto")
        ach.info("codigo_y", None, None, "texto")
        for a in ach.itens:
            assert set(a) == CHAVES, a
            assert a["nivel"] in NIVEIS

    def test_os_dois_se_misturam_sem_traducao(self):
        # É o ponto todo: qualidade.run() concatena as duas listas e ordena
        # por nível. Sem schema igual, isso exigiria um adaptador.
        externos = d.alertas_cobertura(produto_com_cobertura(), trib_vencido(),
                                       date(2026, 10, 18))
        ach = q.Alertas()
        ach.aviso("interno", "glp", "GLP", "texto")
        juntos = ach.itens + externos
        assert all(set(a) == CHAVES for a in juntos)
        ordem = {"erro": 0, "aviso": 1, "info": 2}
        juntos.sort(key=lambda x: (ordem[x["nivel"]], x.get("produto") or ""))
        assert [a["nivel"] for a in juntos] == sorted(
            [a["nivel"] for a in juntos], key=lambda n: ordem[n])


class TestCoberturaDeAliquota:
    def test_vigencia_vencida_e_erro(self):
        a = d.alertas_cobertura(produto_com_cobertura(), trib_vencido(),
                                date(2026, 10, 18))
        assert any(x["codigo"] == "cobertura_vencida" and x["nivel"] == "erro" for x in a)

    def test_vigencia_proxima_do_fim_e_aviso(self):
        a = d.alertas_cobertura(produto_com_cobertura(), trib_vencido(),
                                date(2026, 7, 20))
        assert any(x["codigo"] == "cobertura_a_vencer" and x["nivel"] == "aviso" for x in a)

    def test_serie_parada_por_falta_de_aliquota_e_erro(self):
        prod = produto_com_cobertura(sem_faixa=3, sem_faixa_recente=3,
                                     recente_de="2026-08-03", recente_ate="2026-08-17")
        a = d.alertas_cobertura(prod, trib_vencido(), date(2026, 8, 20))
        assert any(x["codigo"] == "cobertura_parada" and x["nivel"] == "erro" for x in a)

    def test_buraco_historico_e_so_info(self):
        # Exclusão deliberada (semana mista do QAV em abril/2026): não é alarme.
        prod = produto_com_cobertura(sem_faixa=1, sem_faixa_recente=0,
                                     de="2026-04-06", ate="2026-04-06",
                                     vigencia_ate=None)
        a = d.alertas_cobertura(prod, trib_vencido(), date(2026, 10, 18))
        assert [x["nivel"] for x in a] == ["info"]
        assert a[0]["codigo"] == "cobertura_buraco"
