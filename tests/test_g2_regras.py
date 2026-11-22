"""Regras deterministicas da operacao G2: trava de escopo e auxiliares do planejador. Sem rede."""

import copy
import importlib.util
import sys
from datetime import date
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "scripts"))

from migra_g2.base import cfg, escrita_permitida  # noqa: E402

_spec = importlib.util.spec_from_file_location("planejar", RAIZ / "scripts" / "366_g2_planejar.py")
plan = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(plan)

R103 = "PARCEIRO-INFRA/05. RD-103/04. Engenharia"


def c_fase(fase, liberadas=("RD-103",)):
    c = copy.deepcopy(cfg())
    c["fase"] = fase
    c["destino"]["rodovias_liberadas"] = list(liberadas)
    return c


def test_diagnostico_nao_escreve_nada():
    c = c_fase("diagnostico")
    assert not escrita_permitida(f"{R103}/01. Projetos/x.pdf", False, c)[0]
    assert not escrita_permitida(c["destino"]["pasta_teste"] + "/a.bin", False, c)[0]


def test_pasta_teste_so_na_fase_teste():
    t = cfg()["destino"]["pasta_teste"] + "/a.bin"
    assert escrita_permitida(t, False, c_fase("teste"))[0]
    assert not escrita_permitida(t, False, c_fase("execucao"))[0]
    assert not escrita_permitida(f"{R103}/01. Projetos/x.pdf", False, c_fase("teste"))[0]


def test_rodovias_bloqueadas():
    c = c_fase("execucao")
    for go in ("03. RD-101", "04. RD-102A", "06. RD-104"):
        assert not escrita_permitida(f"PARCEIRO-INFRA/{go}/04. Engenharia/01. Projetos/x.pdf", False, c)[0]
    assert escrita_permitida(f"{R103}/01. Projetos/x.pdf", False, c)[0]


def test_eventograma_rpfo_dup_e_fora_da_engenharia():
    c = c_fase("execucao")
    assert not escrita_permitida(f"{R103}/09. Eventograma", True, c)[0]
    assert not escrita_permitida(f"{R103}/09. Eventograma/01. Anteprojeto/x.pdf", False, c)[0]
    rpfo = f"{R103}/01. Projetos/03. RPFO (Revisão de Projeto em Fase de Obra)"
    assert escrita_permitida(rpfo, True, c)[0]
    assert not escrita_permitida(rpfo + "/x.pdf", False, c)[0]
    dup = f"{R103}/04. Desapropriação/01. DUP"
    assert escrita_permitida(dup, True, c)[0]
    assert not escrita_permitida(dup + "/sub", True, c)[0]
    assert not escrita_permitida("PARCEIRO-INFRA/05. RD-103/05. Certificação/x.pdf", False, c)[0]
    assert not escrita_permitida(R103, True, c)[0]


def test_unicode_nfd_e_normalizado():
    import unicodedata
    c = c_fase("execucao")
    p = unicodedata.normalize("NFD", f"{R103}/04. Desapropriação/01. DUP/x.pdf")
    assert not escrita_permitida(p, False, c)[0]


def test_outra_rodovia():
    assert plan.outra_rodovia("NC-026-1-Parecer_Tecnico_270 - RD102.pdf", "RD-103")
    assert not plan.outra_rodovia("NC-027-1 - Parecer_Técnico_321 - RD103.pdf", "RD-103")
    assert not plan.outra_rodovia("RO_024.1 - RD-103.pdf", "RD-103")


def test_excluidos():
    assert plan.excluido("a/Obsoleto/x.pdf")
    assert plan.excluido("a/REV 05 - 23 10 26 (Entrega Informal)/x.pdf")
    assert plan.excluido("a/Arquivos Auxiliares/x.xlsx")
    assert plan.excluido("a/~$doc.docx")
    assert plan.excluido("a/pacote.zip")
    assert plan.excluido("a/0. Pleitos/25.10.09 - Resistência (MINUTA)/x.pdf")
    assert not plan.excluido("a/REV 05 - 08 07 26/RD103_123.156_RT.EST.TRA_05.0.pdf")


def test_codigos_e_datas():
    assert plan.codigos("RI–003.1 - Relatório de inspeção")[0][:2] == ("RI", 3)
    assert plan.codigos("RO_056.1 - RD-103.docx")[0][:2] == ("RO", 56)
    assert plan.data_br("REV 04 - 15 07 2026") == date(2026, 7, 15)
    assert plan.data_br("REV 05 - 08 07 26") == date(2026, 7, 8)


def test_preferir_pdf():
    its = [{"nome": "RO_032.2 - RD-103.docx", "caminho": "p/RO_032.2 - RD-103.docx"},
           {"nome": "RO_032.2 - RD-103.pdf", "caminho": "p/RO_032.2 - RD-103.pdf"},
           {"nome": "RO_005.1 - RD-103.docx", "caminho": "q/RO_005.1 - RD-103.docx"}]
    ficam, saem = plan.preferir_pdf(its)
    assert {i["nome"] for i in ficam} == {"RO_032.2 - RD-103.pdf", "RO_005.1 - RD-103.docx"}
    assert [i["nome"] for i in saem] == ["RO_032.2 - RD-103.docx"]


def test_trava_fase_executoria_e_supervisora():
    c = c_fase("execucao", ("RD-103", "RD-102A"))
    fe = "04. Desapropriação/02. Fase Executória"
    assert escrita_permitida(f"{R103}/{fe}", True, c)[0]
    assert not escrita_permitida(f"{R103}/{fe}/CTD-19/x.pdf", False, c)[0]
    assert escrita_permitida(f"PARCEIRO-INFRA/04. RD-102A/04. Engenharia/{fe}/CTD-19/x.pdf", False, c)[0]
    sup = f"{R103}/02. Boletim de Medição/01. Mensal/2026-01 - Janeiro/02. Documentação Supervisora"
    assert escrita_permitida(sup, True, c)[0]
    assert not escrita_permitida(sup + "/x.pdf", False, c)[0]


class _CtlFake(plan.Controles):
    def __init__(self, lista):            # sem ler planilhas
        self.lista = lista


def test_entregue_exige_a_versao_da_nt():
    lista = {("NT", 175): [{"codigo": "NT-175.1", "versao": "1", "status": "Entregue", "linha": 1, "data": None},
                           {"codigo": "NT-175.2", "versao": "2", "status": "Em elaboração", "linha": 2, "data": None}]}
    ctl = _CtlFake(lista)
    assert ctl.entregue("NT", 175) is not None            # sem versao: qualquer entregue
    assert ctl.entregue("NT", 175, "1")["codigo"] == "NT-175.1"
    assert ctl.entregue("NT", 175, "2") is None           # a .2 citada no controle nao esta entregue


def test_codigo_de_nt_com_versao():
    assert plan.codigos("NT-175.2")[0] == ("NT", 175, "2")
    assert plan.codigos("NT-054.1")[0] == ("NT", 54, "1")


# ---------------------------------------------------------------- 29/10/2026: quatro rodovias
def test_data_da_pasta_rev_ignora_o_numero_da_revisao():
    assert plan.data_br("REV02 06 07 26") == date(2026, 7, 6)          # antes lia 02/06/2007
    assert plan.data_br("REV 05 07 08 2026") == date(2026, 8, 7)
    assert plan.data_br("REV 05 - 06 07 2026") == date(2026, 7, 6)
    assert plan.data_br("REV 01 23 07 26") == date(2026, 7, 23)
    assert plan.data_br("REV 00") is None


def test_aprovado_aceita_genero_e_espaco_mas_nunca_ressalva():
    for s in ("Aprovado", "Aprovada", "Aprovado ", " aprovado"):
        assert plan.aprovado(s)
    for s in ("Aprovado com ressalvas", "Aprovado com Ressalva", "Reprovado", "Parecer emitido", None, ""):
        assert not plan.aprovado(s)


def test_trava_libera_as_quatro_quando_autorizadas():
    c = c_fase("execucao", ("RD-101", "RD-102A", "RD-103", "RD-104"))
    for go in ("03. RD-101", "04. RD-102A", "06. RD-104"):
        assert escrita_permitida(f"PARCEIRO-INFRA/{go}/04. Engenharia/01. Projetos/x.pdf", False, c)[0]
        assert not escrita_permitida(f"PARCEIRO-INFRA/{go}/04. Engenharia/09. Eventograma/x.pdf", False, c)[0]
    # CTD so na RD-102A
    fe = "04. Desapropriação/02. Fase Executória/CTD-19/x.pdf"
    assert escrita_permitida(f"PARCEIRO-INFRA/04. RD-102A/04. Engenharia/{fe}", False, c)[0]
    assert not escrita_permitida(f"PARCEIRO-INFRA/06. RD-104/04. Engenharia/{fe}", False, c)[0]


class _Ctl:
    """Controles minimos para exercitar planejar() sem planilhas."""
    def __init__(self, entregues=()):
        self.disciplinas, self.ncs, self.meta = [], {}, {}
        self._e = set(entregues)

    def entregue(self, p, n, v=None):
        return {"codigo": f"{p}-{n}", "versao": v, "linha": 1, "status": "Entregue", "data": None,
                "descricao": "", "tipo": "", "periodo": None} if (p, n) in self._e else None

    def ev(self, e):
        return f"Lista Mestre: {e['codigo']} Entregue"


def _con_com(itens):
    from migra_g2 import base
    import tempfile
    con = base.conectar(Path(tempfile.mkdtemp()) / "t.db")
    con.execute("INSERT INTO execucao (id, script, inicio) VALUES (7, '363_g2_inventario_origem', 'x')")
    for k, (cam, fonte) in enumerate(itens):
        con.execute("INSERT INTO origem_item (drive_id, item_id, fonte, caminho, nome, eh_pasta, tamanho, etag,"
                    " quick_xor_hash, inventariado_em, execucao_id) VALUES ('D', ?, ?, ?, ?, 0, 10, 'e', ?, 'x', 7)",
                    (f"I{k}", fonte, cam, cam.rsplit("/", 1)[-1], f"h{k}"))
    con.commit()
    return con


def test_ctd_so_ultimo_envio_e_documento_comum_fica_pendente():
    PL = "RD102A/4_Projeto Executivo/0. Pleitos de Obra"
    con = _con_com([
        (f"{PL}/26.09.04 - Encaminhamento da Desapropriação CTD-19 a Órgão Contratante/PA-244.1- Encaminhamento.pdf", "Programa"),
        (f"{PL}/26.09.04 - Encaminhamento da Desapropriação CTD-19 a Órgão Contratante/Anexos/3. CTD 19_MAT_R00.pdf", "Programa"),
        (f"{PL}/26.09.30 - Encaminhamento da Desapropriação CTD-43 e 45/PA-304.1- Enc.pdf", "Programa"),
        (f"{PL}/26.10.09 - Encaminhamento da Desapropriação CTD-43 e 45/PA-322.1- Enc.pdf", "Programa"),
        (f"{PL}/26.10.09 - Encaminhamento da Desapropriação CTD-43 e 45/Anexos/CTD 43.zip", "Programa"),
        (f"{PL}/26.10.09 - Encaminhamento da Desapropriação CTD-43 e 45/Anexos/CTD 45.zip", "Programa"),
        (f"{PL}/26.09.24 - Encaminhamento de Interferências a DSV/x.pdf", "Programa"),
        ("RD102A/4_Projeto Executivo/10. Volumes Finais/VOL.03A.pdf", "Programa"),
    ])
    P = plan.planejar(con, _Ctl({("PA", 244), ("PA", 322), ("PA", 304)}), "R", "RD-102A", 7)
    por = {ln["it"]["nome"]: (ln["classificacao"], ln["destino"]) for ln in P.linhas}
    fe = "R/04. Desapropriação/02. Fase Executória"
    assert por["PA-244.1- Encaminhamento.pdf"] == ("AUSENTE_E_ELEGIVEL", f"{fe}/CTD-19/PA-244.1- Encaminhamento.pdf")
    assert por["3. CTD 19_MAT_R00.pdf"] == ("AUSENTE_E_ELEGIVEL", f"{fe}/CTD-19/Anexos/3. CTD 19_MAT_R00.pdf")
    assert por["CTD 43.zip"] == ("AUSENTE_E_ELEGIVEL", f"{fe}/CTD-43/Anexos/CTD 43.zip")
    assert por["CTD 45.zip"] == ("AUSENTE_E_ELEGIVEL", f"{fe}/CTD-45/Anexos/CTD 45.zip")
    assert por["PA-322.1- Enc.pdf"][0] == "EQUIVALENCIA_INCERTA"          # comum a 43 e 45: nao duplica
    assert por["PA-304.1- Enc.pdf"][0] == "FORA_DO_ESCOPO"                # envio anterior: so o ultimo
    assert por["x.pdf"][0] == "FORA_DO_ESCOPO"
    assert por["VOL.03A.pdf"][0] == "SEM_EVIDENCIA"                       # volume final sem aprovacao


def test_ctd_nunca_em_outra_rodovia():
    con = _con_com([("RD104/4_Projeto Executivo/0. Pleitos de Obra/26.09.04 - Encaminhamento da Desapropriação "
                     "CTD-19/a.pdf", "Programa")])
    P = plan.planejar(con, _Ctl({("PA", 1)}), "R", "RD-104", 7)
    assert [ln["classificacao"] for ln in P.linhas] == ["FORA_DO_ESCOPO"]


def test_bm_executora_por_rodovia_e_mes_com_espaco():
    con = _con_com([
        ("05ª MP - JAN 26 -101/5ª Medição - Janeiro - DOCUMENTOS EXECUTORA A/a.pdf", "BM_RD101"),
        ("04ª MP - DEZ-25 - 101/4ª Medição - Dezembro - EXECUTORA A/b.pdf", "BM_RD101"),
        ("07ª MP - MAR-26 101/7ª Medição - Março - ELABORADO Programa/c.pdf", "BM_RD101"),
        ("07ª MP - MAR-26 101/7ª Medição - Março - OUTRA COISA/d.pdf", "BM_RD101"),
    ])
    P = plan.planejar(con, _Ctl(), "R", "RD-101", 7)
    por = {ln["it"]["nome"]: (ln["classificacao"], ln["destino"]) for ln in P.linhas}
    m = "R/02. Boletim de Medição/01. Mensal"
    assert por["a.pdf"] == ("AUSENTE_E_ELEGIVEL", f"{m}/2026-01 - Janeiro/01. Documentação Executora/a.pdf")
    assert por["b.pdf"] == ("AUSENTE_E_ELEGIVEL", f"{m}/2025-12 - Dezembro/01. Documentação Executora/b.pdf")
    assert por["c.pdf"] == ("AUSENTE_E_ELEGIVEL", f"{m}/2026-03 - Março/03. Elaboração Programa/c.pdf")
    assert por["d.pdf"][0] == "SEM_EVIDENCIA"


def test_pasta_de_ramo_ambigua_nao_e_adivinhada():
    G = "RD104/3_Gestao Tecnica de Obra"
    con = _con_com([(f"{G}/5. Relatório Mensal da Obra/RM-001.1 - RD104/x.pdf", "Programa"),
                    (f"{G}/5. Relatório mensal antigo/RM-001.1 - RD104/y.pdf", "Programa")])
    P = plan.planejar(con, _Ctl({("RM", 1)}), "R", "RD-104", 7)
    assert any("Relatorio mensal" in a for a in P.avisos)
    assert all(ln["classificacao"] != "AUSENTE_E_ELEGIVEL" for ln in P.linhas)


def test_cruzar_caminho_da_eap_ja_ocupado_por_terceiro():
    con = _con_com([("RD104/0_Anteprojeto/a.pdf", "Programa"), ("RD104/0_Anteprojeto/b.pdf", "Programa"),
                    ("RD104/0_Anteprojeto/c.pdf", "Programa")])
    R = "PARCEIRO-INFRA/06. RD-104/04. Engenharia"
    P = plan.planejar(con, _Ctl(), R, "RD-104", 7)
    con.execute("INSERT INTO parceiro_foto (id, tipo, inicio, fim) VALUES (1, 'baseline', 'x', 'y')")
    for nome, fid, et, tam, h in (("a.pdf", "F1", "E1", 10, "h0"), ("b.pdf", "F2", "E2", 10, "OUTRO"),
                                  ("c.pdf", "F3", "E3", 10, None)):
        con.execute("INSERT INTO parceiro_item (foto_id, caminho, rodovia, eh_pasta, tamanho, etag, fileid) VALUES"
                    " (1, ?, 'RD-104', 0, ?, ?, ?)", (f"{R}/01. Projetos/01. Anteprojeto/{nome}", tam, et, fid))
        if h:
            con.execute("INSERT INTO parceiro_hash (fileid, etag, caminho, tamanho, quick_xor, lido_em) VALUES"
                        " (?,?,?,?,?,'x')", (fid, et, nome, tam, h))
    con.commit()
    plan.cruzar(con, P, {})
    por = {ln["it"]["nome"]: ln for ln in P.linhas}
    assert por["a.pdf"]["classificacao"] == "JA_EXISTIA_CORRETO"                 # mesmo conteudo no lugar certo
    assert por["b.pdf"]["classificacao"] == "EQUIVALENCIA_INCERTA"               # conteudo diferente: decisao
    assert por["c.pdf"]["classificacao"] == "AUSENTE_E_ELEGIVEL" and por["c.pdf"]["pendente_hash"]  # le o hash


def test_bm_respeita_o_mes_limite_da_rodovia():
    con = _con_com([("10ª MP - JUN-26 104/10ª Medição Junho 2026 - EXECUTORA D/a.pdf", "BM_RD104"),
                    ("11ª MP - JUL-26 104/11ª Medição Julho 2026 - EXECUTORA D/b.pdf", "BM_RD104"),
                    ("09ª MP - MAI-26 101/9ª Medição - Maio - EXECUTORA A/c.pdf", "BM_RD101"),
                    ("10ª MP - JUN-26 101/10ª Medição - Junho- EXECUTORA A/d.pdf", "BM_RD101")])
    por = {}
    for go in ("RD-104", "RD-101"):
        por.update({ln["it"]["nome"]: ln["classificacao"] for ln in plan.planejar(con, _Ctl(), "R", go, 7).linhas})
    assert por == {"a.pdf": "AUSENTE_E_ELEGIVEL", "b.pdf": "FORA_DO_ESCOPO",       # 104: ate junho
                   "c.pdf": "AUSENTE_E_ELEGIVEL", "d.pdf": "FORA_DO_ESCOPO"}       # 101: ate maio
