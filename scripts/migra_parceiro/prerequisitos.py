"""
prerequisitos - checagens obrigatorias antes de escrever no PARCEIRO (por lote).

So le o banco local. As checagens de rede (autenticacao, destino ao vivo, amostra da
origem) ficam no 340, que chama este modulo e o rclone.
"""

from __future__ import annotations

import re
import sqlite3
import unicodedata

from .regras import GRUPOS, MESES

ESTADOS_FILA = ("AGUARDANDO_APROVACAO", "APROVADO", "FALHOU", "EM_TRANSFERENCIA")
_MES = "|".join(MESES)
_G = "|".join(re.escape(g) for g in GRUPOS.values())

# Caminho real no PARCEIRO (pastas existentes reaproveitadas: "01.Coordenação Programa", "01.Gestão do Contrato")
# -> padrao exigido pela EAP + no FIXO da EAP que precisa existir na planilha oficial (eap_no).
PADROES_EAP = [
    (r"01\.Coordenação Programa/01\.Gestão do Contrato/01\. Documentos das Consorciadas/\d{4}-T[1-4](/.*)?",
     "01. Coordenação Programa/01. Gestão do Contrato/01. Documentos das Consorciadas"),
    (r"01\.Coordenação Programa/01\.Gestão do Contrato/02\. ARTs(/.*)?",
     "01. Coordenação Programa/01. Gestão do Contrato/02. ARTs"),
    (rf"01\.Coordenação Programa/02\. Produtos Mensais/\d{{4}}-(\d{{2}}) - ({_MES})(/({_G})(/.*)?)?",
     "01. Coordenação Programa/02. Produtos Mensais"),
    (r"02\. Programa/03\. Prestação de Contas/01\. Anual/\d{4}(/.*)?",
     "02. Programa/03. Prestação de Contas/01. Anual"),
    (rf"02\. Programa/03\. Prestação de Contas/03\. Estruturadora/01\. Mensal/\d{{4}}-(\d{{2}}) - ({_MES})(/.*)?",
     "02. Programa/03. Prestação de Contas/03. Estruturadora/01. Mensal"),
    (r"02\. Programa/03\. Prestação de Contas/03\. Estruturadora/02\. Trimestral/\d{4}-T[1-4](/.*)?",
     "02. Programa/03. Prestação de Contas/03. Estruturadora/02. Trimestral"),
    (r"02\. Programa/03\. Prestação de Contas/04\. Obras/(RD-101|RD-102A|RD-103|RD-104)/Trimestre [1-9]\d*(/.*)?",
     "02. Programa/03. Prestação de Contas/04. Obras"),
    (r"02\. Programa/04\. Atendimento aos Órgãos de Controle/02\. TCE-Estado/\d{4}-\d{2}-\d{2} - Resposta TCE - [^/{}]+(/.*)?",
     "02. Programa/04. Atendimento aos Órgãos de Controle/02. TCE-Estado"),
    (r"02\. Programa/04\. Atendimento aos Órgãos de Controle/01\. MP-Estado/\d{4}-\d{2}-\d{2} - Resposta MP - [^/{}]+(/.*)?",
     "02. Programa/04. Atendimento aos Órgãos de Controle/01. MP-Estado"),
    # correspondencias (decisao da Emmily 19/10/2026): a EAP nao tem modelo de subpasta; a carta e seus
    # anexos seguem com a identificacao original
    (r"02\. Programa/01\. Correspondências(/[^/{}]+)*",
     "02. Programa/01. Correspondências"),
]


def conforme_eap(destino_pasta: str) -> tuple[bool, str]:
    if "{" in destino_pasta or "}" in destino_pasta:
        return False, "destino com pendencia {..}"
    for padrao, no_eap in PADROES_EAP:
        m = re.fullmatch(padrao, destino_pasta)
        if m:
            # mes por extenso tem de bater com o numero (2025-07 - Julho)
            if "Produtos Mensais" in padrao or "01\\. Mensal" in padrao:
                mm, nome = int(m.group(1)), m.group(2)
                if MESES[mm - 1] != nome:
                    return False, f"mes {mm:02d} != {nome}"
            return True, no_eap
    return False, "fora dos padroes da EAP"


def _k(s: str) -> str:
    return unicodedata.normalize("NFC", s).casefold()


def checar_lote(con: sqlite3.Connection, lote: str, esperado_arquivos: int | None = None,
                tamanho_max: int | None = None) -> list[tuple[str, bool, str]]:
    r: list[tuple[str, bool, str]] = []
    lt = con.execute("SELECT * FROM lote WHERE nome=?", (lote,)).fetchone()
    r.append(("lote existe", lt is not None, lote))
    if not lt:
        return r
    itens = con.execute(
        "SELECT p.item_id, p.estado, p.escopo, p.destino_pasta, p.nome_final, p.conflito, o.tamanho, "
        "o.caminho_rel FROM plano_item p JOIN origem_item o USING(item_id) WHERE p.lote=?", (lote,)).fetchall()
    n, b = len(itens), sum(i["tamanho"] or 0 for i in itens)
    if esperado_arquivos is not None:
        r.append(("1. manifesto: quantidade = esperado", n == esperado_arquivos, f"{n} (esperado {esperado_arquivos})"))
    sem = [i for i in itens if not i["destino_pasta"] or "{" in i["destino_pasta"] or not i["nome_final"]]
    r.append(("1. manifesto: destino e nome definitivos, sem ambiguidade", not sem, f"{len(sem)} indefinidos"))
    fora = [i for i in itens if i["escopo"] != "migrar"]
    r.append(("1. manifesto: todos no escopo aprovado ('migrar')", not fora, f"{len(fora)} fora do escopo"))

    # 2. sobreposicao: um item so pertence a um lote (coluna unica); nada do PILOTO-01 aqui
    p01 = con.execute("SELECT COUNT(*) FROM plano_item WHERE lote=? AND item_id IN "
                      "(SELECT item_id FROM plano_item WHERE lote='PILOTO-01')", (lote,)).fetchone()[0]
    r.append(("2. sem sobreposicao com o PILOTO-01 ou outros lotes", p01 == 0 or lote == "PILOTO-01",
              "cada item pertence a um unico lote (plano_item.lote)"))

    # 3. conciliacao com o inventario
    inv = con.execute("SELECT COUNT(*), COALESCE(SUM(tamanho),0) FROM origem_item WHERE eh_pasta=0 "
                      "AND ausente_desde IS NULL").fetchone()
    pl = con.execute("SELECT COUNT(*), COALESCE(SUM(o.tamanho),0) FROM plano_item p "
                     "JOIN origem_item o USING(item_id) WHERE o.ausente_desde IS NULL").fetchone()
    r.append(("3. plano conciliado com o inventario (arquivos e bytes)", tuple(inv) == tuple(pl),
              f"inventario {inv[0]} / {inv[1]:,} B; plano {pl[0]} / {pl[1]:,} B; lote {n} / {b:,} B"))

    # 4. EAP
    ruins = []
    for i in itens:
        ok, det = conforme_eap(i["destino_pasta"])
        if not ok:
            ruins.append(f"{i['destino_pasta']} ({det})")
    nos_eap = {row[0] for row in con.execute("SELECT caminho FROM eap_no WHERE arvore='principal'")}
    usados = {conforme_eap(i["destino_pasta"])[1] for i in itens if not ruins}
    faltando_na_planilha = sorted(u for u in usados if u not in nos_eap)
    r.append(("4. caminhos finais seguem a EAP", not ruins and not faltando_na_planilha,
              f"{len(ruins)} fora do padrao{': ' + ruins[0] if ruins else ''}; nos EAP usados: {len(usados)}"
              + (f"; ausentes da planilha: {faltando_na_planilha}" if faltando_na_planilha else "")))

    # 6. colisoes no plano inteiro (caixa/NFC) e conflitos registrados
    finais: dict[str, int] = {}
    for d, nm in con.execute("SELECT destino_pasta, nome_final FROM plano_item WHERE escopo='migrar'"):
        finais[_k(f"{d}/{nm}")] = finais.get(_k(f"{d}/{nm}"), 0) + 1
    col = [k for k, v in finais.items() if v > 1]
    conf = [i for i in itens if i["conflito"]]
    r.append(("6. sem colisao de caminho final (plano inteiro) nem conflito registrado", not col and not conf,
              f"{len(col)} colisoes; {len(conf)} conflitos"))

    if tamanho_max:
        grandes = [i for i in itens if (i["tamanho"] or 0) > tamanho_max]
        r.append((f"arquivos acima de {tamanho_max / 2**30:.0f} GiB exigem estrategia aprovada", not grandes,
                  f"{len(grandes)} acima do limite"))

    estados: dict[str, int] = {}
    for i in itens:
        estados[i["estado"]] = estados.get(i["estado"], 0) + 1
    r.append(("estados atuais do lote", True, str(estados)))
    return r
