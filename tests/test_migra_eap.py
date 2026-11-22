import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

openpyxl = pytest.importorskip("openpyxl")

from migra_parceiro.eap import cruzar, ler_eap  # noqa: E402

# Arvore ficticia no formato da EAP real: nivel = coluna B, E, H, K (passo 3)
ARVORE = [
    ("B", "01. Coordenação Programa", None),
    ("E", "01. Gestão do Contrato", None),
    ("H", "01. Documentos das Consorciadas", None),
    ("E", "02. Produtos Mensais", None),
    ("H", "[AAAA-MM - Mês]", None),
    ("K", "G1 - Coordenação Geral Executiva", None),
    ("B", "02. Programa", None),
    ("E", "05. Gestão de Riscos", None),
    ("H", "03. Matriz de Risco", None),
]


def montar(tmp_path, lista=None):
    wb = openpyxl.Workbook()
    fl = wb.active
    fl.title = "Fluxograma"
    fl["S5"] = "Descrição / o que armazenar"
    ep = wb.create_sheet("EAP de Pastas")
    for i, (col, nome, _) in enumerate(ARVORE):
        fl[f"{col}{7 + 3 * i}"] = nome
        fl[f"S{7 + 3 * i}"] = f"desc {i}"
    for i, nome in enumerate(lista or [n for _, n, _ in ARVORE]):
        ep[f"A{4 + i}"] = "-"
        ep[f"B{4 + i}"] = nome
    cf = wb.create_sheet("Fluxograma Consorciadas")
    cf["Q5"] = "Descrição / o que armazenar"
    cf["B7"] = "[Consorciada]"
    ec = wb.create_sheet("EAP Consorciadas")
    ec["A4"], ec["B4"] = "-", "[Consorciada]"
    p = tmp_path / "eap.xlsx"
    wb.save(p)
    return p


def test_hierarquia_e_modelos(tmp_path):
    nos, div = ler_eap(montar(tmp_path))
    assert div == []
    princ = {n.caminho: n for n in nos if n.arvore == "principal"}
    g1 = princ["01. Coordenação Programa/02. Produtos Mensais/[AAAA-MM - Mês]/G1 - Coordenação Geral Executiva"]
    assert g1.nivel == 3
    assert princ["01. Coordenação Programa/02. Produtos Mensais/[AAAA-MM - Mês]"].modelo
    assert princ["02. Programa/05. Gestão de Riscos"].pai == "02. Programa"


def test_divergencia_entre_abas_e_relatada(tmp_path):
    lista = [n for _, n, _ in ARVORE]
    lista[2] = "01. Documentos das Empresas"
    _, div = ler_eap(montar(tmp_path, lista))
    assert any("Documentos das Empresas" in d for d in div)


def test_cruzar_reusa_equivalente_e_pede_decisao_em_semelhante(tmp_path):
    nos, _ = ler_eap(montar(tmp_path))
    destino = {"01.Coordenação Programa", "01.Coordenação Programa/01.Gestão do Contrato",
               "02. Programa", "02. Programa/05. Gestão de Riscos",
               "02. Programa/05. Gestão de Riscos/03. Matriz de Riscos"}
    r = {l["caminho_eap"]: l for l in cruzar(nos, destino)}
    assert r["01. Coordenação Programa"]["situacao"] == "equivalente"
    assert r["01. Coordenação Programa"]["caminho_real"] == "01.Coordenação Programa"
    assert r["01. Coordenação Programa/01. Gestão do Contrato"]["acao"] == "reusar"
    assert r["01. Coordenação Programa/01. Gestão do Contrato/01. Documentos das Consorciadas"]["acao"] == "criar"
    assert r["02. Programa/05. Gestão de Riscos/03. Matriz de Risco"]["situacao"] == "semelhante"
    assert r["01. Coordenação Programa/02. Produtos Mensais/[AAAA-MM - Mês]"]["situacao"] == "modelo"
