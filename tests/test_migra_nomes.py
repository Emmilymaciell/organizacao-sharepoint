import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from migra_parceiro.nomes import chave, colisoes, problemas_segmento  # noqa: E402


def test_chave_ignora_espaco_apos_numero_acento_e_caixa():
    assert chave("01.Gestão do Contrato") == chave("01. Gestao do contrato")
    assert chave("1. Anual") == chave("01. Anual")


def test_chave_nao_iguala_plural():
    assert chave("01. Matriz de Riscos") != chave("01. Matriz de Risco")


def test_problemas_segmento():
    assert problemas_segmento("2026-10-06 - Resposta TCE - RD-104") == []
    assert "termina em espaco ou ponto" in problemas_segmento("Relatorio. ")
    assert any("proibido" in p for p in problemas_segmento("a:b?c"))
    assert "nome reservado do Windows" in problemas_segmento("CON.txt")
    assert problemas_segmento("x" * 251)[0].startswith("nome com 251")


def test_problemas_segmento_nfd():
    nfd = "Prestação"
    assert "unicode nao normalizado (NFC)" in problemas_segmento(nfd)


def test_colisoes_por_caixa_e_normalizacao():
    c = colisoes(["A/Relatório.pdf", "A/RELATÓRIO.pdf", "A/Relatório.pdf", "B/x.pdf"])
    assert len(c) == 1
    assert len(next(iter(c.values()))) == 3
