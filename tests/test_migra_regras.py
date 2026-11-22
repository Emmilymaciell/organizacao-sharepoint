import importlib.util
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "scripts"))

from migra_parceiro.regras import (NOME_TCE_17, classificar, mes_de, nome_final, obra,  # noqa: E402
                               trimestre_ano)

spec = importlib.util.spec_from_file_location("planejar", RAIZ / "scripts" / "333_migra_planejar.py")
planejar = importlib.util.module_from_spec(spec)
spec.loader.exec_module(planejar)


def test_mes_de_formatos_reais():
    assert mes_de("01 - Julho - 2025") == (2025, 7)
    assert mes_de("04. Outubro 2025") == (2025, 10)
    assert mes_de("09. Março 2026") == (2026, 3)
    assert mes_de("1. ABRIL") == (None, 4)


def test_obra_rd102_e_rd102a():
    assert obra("RD103") == "RD-103"
    assert obra("RD-102A") == "RD-102A"
    assert obra("RD-102") == "RD-102A"
    assert obra("RD 102_A") is None   # nao e codigo isolado; nao inventa


def test_trimestre_ano():
    assert trimestre_ano("jan a mar-26") == "2026-T1"
    assert trimestre_ano("ABR A JUN - 2026") == "2026-T2"
    assert trimestre_ano("Jan a Mar - 26") == "2026-T1"
    assert trimestre_ano("REV 1") is None


def test_prestacao_prog_vai_para_estruturadora():
    for cam in ("07_Prestação de Contas Trimestral - Programa/jan a mar-26/REV 2/x.pdf",
                "11. Prestação de Contas Mensal Programa/1. ABRIL/REV 1/x.pdf",
                "5_Prestação de Contas (out a dez-25)/Anexo A/x.pdf"):
        c = classificar(cam)
        assert c.escopo == "migrar" and "/03. Estruturadora/" in c.destino_eap, cam
    assert "/01. Mensal/2026-04 - Abril/" in classificar(
        "11. Prestação de Contas Mensal Programa/1. ABRIL/REV 1/x.pdf").destino_eap + "/"


def test_set_a_nov_e_trimestre_1_e_rd102_vira_rd102a():
    assert classificar("09. Prestação de Contas Obras - Set a Nov - 25/RD104/a/x.pdf").destino_eap.endswith(
        "04. Obras/RD-104/Trimestre 1/Set a Nov - 25/a")
    assert "/04. Obras/RD-102A/Trimestre 2/" in classificar(
        "8. Prestação de Contas Obras/2º TRIMESTRE/RD-102/REV 0/x.pdf").destino_eap + "/"


def test_bv_fora_do_escopo():
    assert classificar("06. CERTIFICADORA ENTREGA/x/y.pdf").escopo == "fora"


def test_produto_vai_para_mes_e_grupo_da_eap_preservando_miolo():
    c = classificar("05. Produtos/01 - Julho - 2025/G2/Relatorios/a.pdf")
    assert c.escopo == "migrar"
    assert c.destino_eap == ("01. Coordenação Programa/02. Produtos Mensais/2025-07 - Julho/"
                             "G2 - Gestão de Engenharia e Socioambiental/Relatorios")


def test_respostas_tce_com_referencia_comprovada_e_mp_sem_referencia_fica_pendente():
    c = classificar("17_Resposta TCE 06 10 26/Anexos/x.pdf")
    assert c.escopo == "migrar"
    assert c.destino_eap.endswith(f"02. TCE-Estado/{NOME_TCE_17}/Anexos")
    m = classificar("16_Resposta MP 19 06 26/b) Projetos/x.pdf")
    assert m.escopo == "pendente" and m.decisoes == ["REF_MP_16"]
    assert "{REF}" in m.destino_eap


def test_requisicao_fica_pendente_sem_destino():
    c = classificar("0. Geral/REQUISICAO Nº 22026 - SERV-FISCINFRAENG/f.pdf")
    assert c.escopo == "pendente" and c.destino_eap is None


def test_sem_regra_nunca_some():
    c = classificar("pasta nova/arquivo.pdf")
    assert c.regra == "R99_sem_regra" and c.decisoes == ["SEM_REGRA"]


def test_nome_final_so_normaliza_nfc():
    assert nome_final("Relatório.pdf") == ("Relatório.pdf", True)
    assert nome_final("Relatório.pdf") == ("Relatório.pdf", False)


def test_resolvedor_reusa_pasta_real_e_conta_niveis_a_criar():
    r = planejar.Resolvedor({"01.Coordenação Programa", "01.Coordenação Programa/02. Produtos Mensais"})
    real, criar = r.resolver("01. Coordenação Programa/02. Produtos Mensais/2025-07 - Julho/G1 - X")
    assert real == "01.Coordenação Programa/02. Produtos Mensais/2025-07 - Julho/G1 - X"
    assert criar == 2
