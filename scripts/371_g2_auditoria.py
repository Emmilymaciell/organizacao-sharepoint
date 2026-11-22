"""
371_g2_auditoria - auditoria gerencial SO LEITURA de uma rodovia ja migrada (hoje: RD-103).

Nao escreve no PARCEIRO nem no SharePoint; le o banco G2, a foto mais recente do PARCEIRO (362), os controles
atuais (364, em tmp/migracao_parceiro_g2/controles) e, para os pendentes, os metadados atuais no Graph (GET).

Produz em output/migracao_parceiro_g2/auditoria_<GO>/:
  02_AUDITORIA_ESTRUTURA_EAP.csv / .md     cada pasta da arvore x EAP (nos fixos, placeholders, notas)
  03_AUDITORIA_NOMENCLATURA.csv            cada arquivo transferido: regras por ramo, conformidade, sugestao
  04_PENDENTES_REAVALIACAO.csv             cada caso pendente (SEM_EVIDENCIA, EQUIVALENCIA_INCERTA, DUPLICIDADE,
                                           ERRO): classificacao original x reavaliacao com os controles atuais
  04b_NAO_APROVADOS_RESUMO.csv             Executivo NAO_APROVADO por disciplina/revisao (fora dos pendentes)
  05_MATRIZ_DECISOES_HUMANAS.csv           decisoes consolidadas e priorizadas
  RECONCILIACAO.json                       numeros, denominadores e checagens cruzadas
A classificacao historica (tabela plano) NAO e alterada: a reavaliacao fica so nestes arquivos.

Uso: python scripts/371_g2_auditoria.py --rodovia RD-103 [--sem-graph]
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "scripts"))

from migra_g2.base import agora, cfg, conectar, escrever_csv, gib, num, pasta, saida  # noqa: E402

_spec = importlib.util.spec_from_file_location("planejar", RAIZ / "scripts" / "366_g2_planejar.py")
P = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(P)

MESES = P.MESES
RE_MES = re.compile(r"^(\d{4})-(\d{2}) - (\S+)$")
RE_SEM = re.compile(r"^(\d{4}-\d{2}-\d{2}) a (\d{4}-\d{2}-\d{2}) - (\d+)º Relatório Semanal$")
RE_NC = re.compile(r"^((NC|NI)-\d{3}(?:-\d+)*) - (\d{4}-\d{2}-\d{2}) - (.+)$")
EXEC_SUB = ["01. FASE DE ESTUDOS", "02. VIARIO PRINC", "03. VIARIO SEC", "04. OAE", "05. VOLUMES FINAIS"]
PENDENTES = ("SEM_EVIDENCIA", "EQUIVALENCIA_INCERTA", "DUPLICIDADE_CONFIRMADA", "CONFLITO_CONCORRENTE",
             "ALTERADO_DURANTE_EXECUCAO")


def nfc(s):
    return unicodedata.normalize("NFC", s or "")


def mes_ok(nome):
    m = RE_MES.match(nome)
    return bool(m) and 1 <= int(m.group(2)) <= 12 and m.group(3) == MESES[int(m.group(2)) - 1]


# ------------------------------------------------------------------ estrutura
def auditar_estrutura(con, go, raiz, foto):
    eap = [dict(r) for r in con.execute("SELECT * FROM eap_no WHERE rodovia=?", (go,))]
    base = raiz.rsplit("/", 2)[0]                                   # PARCEIRO-INFRA
    filhos = defaultdict(list)                                      # caminho EAP -> rotulos filhos
    for n in eap:
        cam = f"{base}/{n['caminho']}"
        filhos[cam.rsplit("/", 1)[0]].append(n["rotulo"])
    filhos[f"{raiz}/01. Projetos/02. Executivo"] += EXEC_SUB
    folhas_livres = {"01. Anteprojeto", "01. FASE DE ESTUDOS", "02. VIARIO PRINC", "03. VIARIO SEC", "04. OAE",
                     "05. VOLUMES FINAIS", "01. Documentação Executora", "02. Documentação Supervisora",
                     "03. Elaboração Programa", "01. Estudos", "02. Licença", "03. Condicionantes",
                     "04. Pareceres de Avaliação das Condicionantes", "01. Relatório de Inspeção de Alojamento",
                     "02. Documentação SST"}
    pastas = {nfc(r[0]): r for r in con.execute(
        "SELECT caminho, fileid FROM parceiro_item WHERE foto_id=? AND eh_pasta=1 AND caminho LIKE ?", (foto, raiz + "/%"))}
    arquivos_por_pasta = Counter(nfc(r[0]).rsplit("/", 1)[0] for r in con.execute(
        "SELECT caminho FROM parceiro_item WHERE foto_id=? AND eh_pasta=0 AND caminho LIKE ?", (foto, raiz + "/%")))
    linhas = []
    esperado_fixo = [f"{base}/{n['caminho']}" for n in eap if "[" not in n["caminho"]]
    esperado_fixo += [f"{raiz}/01. Projetos/02. Executivo/{s}" for s in EXEC_SUB]
    for cam in sorted(set(esperado_fixo)):
        if cam == raiz:
            continue
        ev = "09. Eventograma" in cam
        existe = cam in pastas
        if ev:
            st, obs = ("CONFORME" if not existe else "NAO_CONFORME",
                       "nota 11: nao mexer - pasta NAO criada por esta operacao" if not existe else "existe: verificar origem")
        else:
            st, obs = ("CONFORME", "no fixo da EAP presente") if existe else ("NAO_CONFORME", "no fixo da EAP AUSENTE")
        linhas.append(("NO_FIXO_EAP", cam[len(raiz) + 1:], st, obs, arquivos_por_pasta.get(cam, 0)))

    def placeholder_ok(pai, nome):
        rel = pai[len(raiz) + 1:]
        if rel in ("02. Boletim de Medição/01. Mensal", "05. Diário de Obras", "06. Relatório de Obras/02. Mensal",
                   "07. Não Conformidades", "08. Segurança do Trabalho"):
            return mes_ok(nome), "[AAAA-MM - Mês]"
        if rel == "06. Relatório de Obras/01. Semanal":
            m = RE_SEM.match(nome)
            return bool(m) and m.group(1) <= m.group(2), "[AAAA-MM-DD a AAAA-MM-DD - nº Relatório Semanal]"
        if rel.startswith("07. Não Conformidades/") and rel.count("/") == 1:
            m = RE_NC.match(nome)
            if not m:
                return False, "[NC-nnn - AAAA-MM-DD - Assunto]"
            return (m.group(2) == "NC", "[NC-nnn - AAAA-MM-DD - Assunto] (prefixo NI = notificacao inicial: indeterminado)")
        if rel.startswith("02. Boletim de Medição/01. Mensal/") and rel.count("/") == 2:
            return nome in ("01. Documentação Executora", "02. Documentação Supervisora", "03. Elaboração Programa"), \
                "01/02/03 do mes de BM"
        if rel.startswith("08. Segurança do Trabalho/") and rel.count("/") == 1:
            return nome in ("01. Relatório de Inspeção de Alojamento", "02. Documentação SST"), "01/02 de SST"
        if rel == "04. Desapropriação/02. Fase Executória":
            return bool(re.match(r"^CTD-\d{2}$", nome)), "[CTD-XX]"
        return None, ""

    for cam in sorted(pastas):
        if cam in esperado_fixo:
            continue
        pai, nome = cam.rsplit("/", 1)
        ok, regra = placeholder_ok(pai, nome)
        rel = cam[len(raiz) + 1:]
        if ok is not None:
            st = "CONFORME" if ok else ("INDETERMINADO" if nome.startswith("NI-") else "NAO_CONFORME")
            obs = f"placeholder da EAP {regra}"
            if "07. Não Conformidades/" in rel and ok and len(nome) >= 90:
                st = "INDETERMINADO"
                obs += "; assunto truncado em 90 caracteres (corte no meio da palavra); sufixo -n preservado da planilha"
            linhas.append(("PLACEHOLDER_EAP", rel, st, obs, arquivos_por_pasta.get(cam, 0)))
            continue
        # abaixo de uma folha da EAP: subestrutura espelhada da origem (permitida pelas notas 01, 02 e 04)
        segs = rel.split("/")
        livre = next((s for s in segs if s in folhas_livres), None)
        if livre or (len(segs) >= 4 and segs[0] == "06. Relatório de Obras") or segs[0] == "07. Não Conformidades":
            linhas.append(("SUBPASTA_DA_ORIGEM", rel, "CONFORME",
                           f"subestrutura da entrega preservada abaixo de '{livre or segs[-2]}'", arquivos_por_pasta.get(cam, 0)))
        else:
            linhas.append(("FORA_DA_EAP", rel, "NAO_CONFORME", "pasta sem correspondencia na EAP", arquivos_por_pasta.get(cam, 0)))
    # vazios esperados (RPFO, DUP, Fase Executoria na RD-103, Volumes Finais, Supervisora)
    for vaz in ("01. Projetos/03. RPFO (Revisão de Projeto em Fase de Obra)", "04. Desapropriação/01. DUP",
                "04. Desapropriação/02. Fase Executória", "01. Projetos/02. Executivo/05. VOLUMES FINAIS"):
        n = sum(v for k, v in arquivos_por_pasta.items() if k == f"{raiz}/{vaz}" or k.startswith(f"{raiz}/{vaz}/"))
        linhas.append(("VAZIO_ESPERADO", vaz, "CONFORME" if n == 0 else "NAO_CONFORME",
                       f"{n} arquivo(s) (notas 02/03/06: deve ficar vazio nesta operacao)", n))
    sup = sum(v for k, v in arquivos_por_pasta.items() if "/02. Documentação Supervisora" in k)
    linhas.append(("VAZIO_ESPERADO", "02. Boletim de Medição/*/02. Documentação Supervisora",
                   "CONFORME" if sup == 0 else "NAO_CONFORME", f"{sup} arquivo(s) (nota 04: supervisoras nao exigidas)", sup))
    return linhas


# ------------------------------------------------------------------ nomenclatura
def auditar_nomes(con, go, raiz, ctl):
    q = """SELECT p.*, o.nome, o.caminho AS origem, o.tamanho, o.quick_xor_hash FROM plano p
           JOIN origem_item o USING (drive_id, item_id) WHERE p.rodovia=? AND p.estado='VALIDADO'"""
    rows = [dict(r) for r in con.execute(q, (go,))]
    aprov = {}
    for d in ctl.disciplinas:
        aprov[P.sem_acento(d["disciplina"])[:22]] = d
    out = []
    for r in rows:
        dest = nfc(r["destino_rel"])
        rel = dest[len(raiz) + 1:]
        nome = dest.rsplit("/", 1)[-1]
        segs = rel.split("/")
        cat = r["categoria"]
        st, regra, prob, sug = "CONFORME", "", [], ""
        if nome != nfc(r["nome"]):
            prob.append("nome final difere do original")
        if P.outra_rodovia(nome, go) and cat not in ("ANTEPROJETO", "EXECUTIVO", "BM"):
            prob.append("o nome cita outra rodovia")
        if re.search(r"\(\d\)|\bc[oó]pia\b|\bcopy\b", nome, re.I):
            prob.append("sufixo de copia no nome (veio assim da origem)")
        if cat == "BM":
            regra = "nota 04: 02. Boletim de Medição/01. Mensal/[AAAA-MM - Mês]/{01|03}"
            m = re.match(r"(\d+)ª MP - ([A-ZÇ]+)-(\d{2})", r["origem"].split("/")[0], re.I)
            esperado = P.mes_pasta(2000 + int(m.group(3)), P.MES_ABREV[P.sem_acento(m.group(2))[:3].upper()]) if m else None
            if segs[2] != esperado:
                prob.append(f"mes da pasta {segs[2]} difere da medicao de origem ({esperado})")
            if segs[3] not in ("01. Documentação Executora", "03. Elaboração Programa"):
                prob.append("categoria do BM fora de 01/03")
        elif cat == "EXECUTIVO":
            regra = "nota 02: so revisao final aprovada; subpastas 01..05"
            # _05.0 (codigo de documento) e _R08 (pacote): achado A3 da revisao independente
            revs = [x or y for x, y in re.findall(r"_(?:R(\d{2})|(\d{2})\.\d)(?=\b|_|\.)", nome)]
            rev_aprov = (r["revisao"] or "").replace("REV ", "")
            pasta_rev = next((s for s in segs if re.match(r"^REV\s*\d+", s, re.I)), "")
            nrev_pasta = re.match(r"^REV\s*(\d+)", pasta_rev, re.I)
            if revs and rev_aprov and int(revs[-1]) != int(rev_aprov):
                st = "INDETERMINADO"
                sug = (f"folha com codigo de revisao _{revs[-1]} dentro do pacote aprovado REV {rev_aprov} (folha nao "
                       "revisada mantida no pacote?): confirmar com a disciplina")
            if nrev_pasta and rev_aprov and int(nrev_pasta.group(1)) != int(rev_aprov):
                st = "INDETERMINADO"
                sug = f"pasta '{pasta_rev}' (numeracao de envio da origem) contem a revisao aprovada {rev_aprov}: " \
                      f"considerar renomear a pasta para 'REV {int(rev_aprov):02d} - <data>'"
        elif cat == "REL_SEMANAL":
            regra = "nota 08: [AAAA-MM-DD a AAAA-MM-DD - nº Relatório Semanal], numero oficial"
            m = RE_SEM.match(segs[2])
            ros = [c for c in P.codigos(nome) if c[0] == "RO"]
            if m and ros and ros[0][1] != int(m.group(3)):
                prob.append(f"RO-{ros[0][1]:03d} dentro da pasta do {m.group(3)}º relatorio")
            ent = ctl.entregue("RO", int(m.group(3))) if m else None
            if ent and ent.get("periodo"):
                p = P.data_br(str(ent["periodo"]))
                if p and m and p.isoformat() != m.group(1):
                    st = "INDETERMINADO" if st == "CONFORME" else st
                    sug = f"periodo da pasta ({m.group(1)} a {m.group(2)}) difere do periodo na Lista Mestre ({ent['periodo']})"
        elif cat == "REL_MENSAL":
            regra = "nota 08: 02. Mensal/[AAAA-MM - Mês] + anexos"
            mm = re.search(r"RM-\d+\.\d+\s*-\s*RD\s*103\s*-\s*(\d{2})\s+(\d{4})", nome, re.I)
            if mm and segs[2] != P.mes_pasta(int(mm.group(2)), int(mm.group(1))):
                prob.append(f"RM do mes {mm.group(1)}/{mm.group(2)} na pasta {segs[2]}")
        elif cat == "DIARIO":
            regra = "nota 07: [AAAA-MM - Mês] pela data inicial do documento"
            m = re.match(r"(\d{4})-(\d{2})", nome)
            if m and segs[1] != P.mes_pasta(int(m.group(1)), int(m.group(2))):
                prob.append("mes da pasta difere da data inicial do diario")
        elif cat == "NC":
            regra = "nota 09: [AAAA-MM - Mês]/[NC-nnn - data - assunto]"
            pn = re.match(r"^((?:NC|NI)-\d{3})", segs[2])
            fn = [c for c in P.codigos(nome) if c[0] in ("NC", "NI")]
            if pn and fn and f"{fn[0][0]}-{fn[0][1]:03d}" != pn.group(1):
                prob.append(f"arquivo de {fn[0][0]}-{fn[0][1]:03d} na pasta {pn.group(1)}")
            if segs[2].startswith("NI-"):
                st = "INDETERMINADO" if st == "CONFORME" else st
                sug = "pasta com prefixo NI (notificacao inicial) dentro de Nao Conformidades: confirmar se NI conta como NC"
            if len(segs[2]) >= 90:
                st = "INDETERMINADO" if st == "CONFORME" else st
                sug = (sug + "; " if sug else "") + ("assunto da pasta truncado em 90 caracteres (corte no meio da "
                                                     "palavra): considerar encurtar em palavra inteira")
        elif cat == "SST":
            regra = "nota 10: [AAAA-MM - Mês]/{01. Relatório de Inspeção de Alojamento|02. Documentação SST}"
            if not mes_ok(segs[1]):
                prob.append("pasta de mes fora do padrao")
        elif cat == "LICENCIAMENTO":
            regra = "nota 05: 01..04 do Licenciamento"
        else:
            regra = "nota 01: acervo do anteprojeto espelhado"
        if prob:
            st = "NAO_CONFORME"
        out.append((r["item_id"], cat, r["origem"], nfc(r["nome"]), nome, rel, regra, st, " | ".join(prob), sug,
                    r["tamanho"], r["quick_xor_hash"]))
    return out


# ------------------------------------------------------------------ pendentes
GRUPO = [
    ("ENTREGA INFORMAL", "entrega informal"), ("obsolet", "versao obsoleta"),
]


def reavaliar_pendentes(con, go, raiz, ctl, usar_graph):
    q = """SELECT p.*, o.nome, o.caminho AS origem, o.tamanho, o.etag AS etag_inv, o.quick_xor_hash FROM plano p
           JOIN origem_item o USING (drive_id, item_id) WHERE p.rodovia=? AND (p.classificacao IN ({}) OR p.estado IN
           ('ERRO','DIVERGENTE','NA_FILA'))""".format(",".join("?" * len(PENDENTES)))
    pend = [dict(r) for r in con.execute(q, (go, *PENDENTES))]
    novo = P.planejar_rd103(con, ctl, raiz)                     # mesmas regras, controles ATUAIS, em memoria
    nova = {(ln["it"]["drive_id"], ln["it"]["item_id"]): ln for ln in novo.linhas}
    g = None
    if usar_graph:
        from graph_util import ErroGraph, Graph
        g = Graph()
    foto = con.execute("SELECT MAX(id) FROM parceiro_foto WHERE fim IS NOT NULL").fetchone()[0]
    por_tam = defaultdict(list)
    for r in con.execute("SELECT caminho, tamanho, fileid, etag, rodovia FROM parceiro_item WHERE foto_id=? AND eh_pasta=0",
                         (foto,)):
        por_tam[r[1]].append(r)
    hashes = {(r[0], r[1]): r[2] for r in con.execute("SELECT fileid, etag, quick_xor FROM parceiro_hash")}
    nossos = {r[0]: r[1] for r in con.execute("SELECT fileid_destino, destino_rel FROM plano WHERE fileid_destino IS NOT NULL")}
    out = []
    for i, r in enumerate(pend, 1):
        n = nova.get((r["drive_id"], r["item_id"]))
        atual = "nao consultado"
        if g:
            try:
                it = g.get(f"/drives/{r['drive_id']}/items/{r['item_id']}?$select=size,eTag,lastModifiedDateTime,file")
                q_ = ((it.get("file") or {}).get("hashes") or {}).get("quickXorHash")
                atual = ("inalterado" if it.get("eTag") == r["etag_inv"] else
                         ("so metadados mudaram" if q_ == r["quick_xor_hash"] else "CONTEUDO MUDOU")) + \
                        f" ({it.get('lastModifiedDateTime')})"
            except ErroGraph as e:
                atual = f"inacessivel/removido ({e.status})"
        corr = ""
        for rr in por_tam.get(r["tamanho"], []):
            h = hashes.get((rr[2], rr[3]))
            if rr[2] in nossos and r["quick_xor_hash"]:
                o2 = con.execute("SELECT quick_xor_hash FROM origem_item o JOIN plano p USING (drive_id,item_id) "
                                 "WHERE p.fileid_destino=?", (rr[2],)).fetchone()
                h = o2[0] if o2 else h
            if h and h == r["quick_xor_hash"]:
                corr = rr[0]
                break
        motivo = r["motivo"] or r["erro"] or ""
        mesma_rodovia = bool(corr) and corr.startswith(raiz + "/")
        corr_txt = (("JA PRESENTE NA RD-103 (outro caminho): " if mesma_rodovia else
                     "conteudo identico em OUTRA rodovia (generico, nao e o mesmo documento): ") + corr) if corr else ""
        cls_nova = n["classificacao"] if n else "SEM_LINHA_NA_REAVALIACAO"
        # tipologia gerencial pedida
        m = P.sem_acento(motivo + " " + (n["motivo"] if n else ""))
        if r["classificacao"] == "DUPLICIDADE_CONFIRMADA":
            tipo = "Duplicado"
        elif "ressalva" in m:
            tipo = "Aprovado com ressalvas"
        elif "reprovad" in m:
            tipo = "Reprovado"
        elif "em analise" in m:
            tipo = "Em analise"
        elif "nao consta na planilha" in m or "sem numero" in m:
            tipo = "Sem documento correspondente no controle"
        elif "varias ncs" in m or "outra rodovia" in m or "subpasta dentro" in m or "dois arquivos" in m:
            tipo = "Classificacao ambigua"
        elif "nt de aprovacao" in m and "entregue" in m:
            tipo = "Aprovado, mas sem comprovacao de entrega da NT"
        elif "sem registro 'entregue'" in m:
            tipo = "Sem comprovacao de entrega"
        elif "nenhuma pasta rev" in m:
            tipo = "Aprovado, mas sem a pasta da entrega aprovada"
        elif "sem ramo da eap" in m:
            tipo = "Entregue, mas sem ramo da EAP correspondente"
        else:
            tipo = "Evidencia insuficiente"
        liberavel = cls_nova == "AUSENTE_E_ELEGIVEL" and not mesma_rodovia and "CONTEUDO MUDOU" not in atual
        falta = {"Aprovado, mas sem comprovacao de entrega da NT": "registro da NT como 'Entregue' na Lista Mestre",
                 "Sem comprovacao de entrega": "registro do codigo como 'Entregue' na Lista Mestre",
                 "Aprovado, mas sem a pasta da entrega aprovada": "localizar a entrega aprovada (data do controle) na origem",
                 "Classificacao ambigua": "decidir a NC/pasta correta ou confirmar o arquivo",
                 "Entregue, mas sem ramo da EAP correspondente": "decidir em que ramo da EAP o documento entra",
                 "Sem documento correspondente no controle": "confirmar se o documento integra a entrega",
                 }.get(tipo, "evidencia formal de aprovacao/entrega")
        rec = ("LIBERAVEL em etapa complementar (evidencia nova com os controles atuais) - aguarda autorizacao"
               if liberavel else ("ja coberto: o mesmo conteudo foi transferido em outra entrega da RD-103; nao copiar"
                                  if mesma_rodovia else "manter fora; " + falta))
        out.append((f"G2-{go}-P{i:04d}", r["categoria"], r["nome"], r["origem"], r["destino_rel"] or (n or {}).get("destino"),
                    r["tamanho"], r["revisao"], r["classificacao"], motivo, r["evidencia"], cls_nova,
                    (n["motivo"] if n else ""), (n["evidencia"] if n else ""), atual, tipo,
                    "SIM" if "aprovad" in P.sem_acento(r["aprovacao"] or "") and r["aprovacao"] == "Aprovado" else
                    ("NAO" if r["aprovacao"] else "N/A"),
                    "SIM" if "entregue" in P.sem_acento((n or {}).get("evidencia") or "") else "NAO/NAO SE APLICA",
                    corr_txt, falta, rec, "NAO (ja coberto)" if mesma_rodovia else "SIM"))
    return out


def resumo_nao_aprovados(con, go):
    return [tuple(r) for r in con.execute(
        """SELECT substr(o.caminho, 1, instr(o.caminho || '/REV', '/REV') - 1) AS disciplina, p.revisao, p.aprovacao,
           p.motivo, COUNT(*), SUM(o.tamanho) FROM plano p JOIN origem_item o USING (drive_id, item_id)
           WHERE p.rodovia=? AND p.classificacao='NAO_APROVADO' GROUP BY 1, 2, 3, 4 ORDER BY 1""", (go,))]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rodovia", default="RD-103")
    ap.add_argument("--sem-graph", action="store_true")
    a = ap.parse_args()
    c = cfg()
    if a.rodovia != "RD-103":
        print("so a RD-103 foi migrada")
        return 1
    con = conectar()
    raiz = c["destino"]["arvores"][a.rodovia]
    foto = con.execute("SELECT MAX(id) FROM parceiro_foto WHERE fim IS NOT NULL").fetchone()[0]
    out = saida(c) / f"auditoria_{a.rodovia}"
    out.mkdir(exist_ok=True)
    ctl = P.Controles(a.rodovia)
    meta = json.loads((pasta("tmp", c) / "controles" / "controles.json").read_text(encoding="utf-8"))

    est = auditar_estrutura(con, a.rodovia, raiz, foto)
    escrever_csv(out / "02_AUDITORIA_ESTRUTURA_EAP.csv", ["tipo", "pasta", "conformidade", "observacao", "arquivos_diretos"], est)
    nomes = auditar_nomes(con, a.rodovia, raiz, ctl)
    escrever_csv(out / "03_AUDITORIA_NOMENCLATURA.csv",
                 ["item_id", "categoria", "origem", "nome_original", "nome_atual", "destino", "regra", "conformidade",
                  "problema", "sugestao (nao executada)", "tamanho", "quick_xor"], nomes)
    pend = reavaliar_pendentes(con, a.rodovia, raiz, ctl, not a.sem_graph)
    escrever_csv(out / "04_PENDENTES_REAVALIACAO.csv",
                 ["id", "categoria", "nome", "origem_sharepoint", "destino_previsto", "tamanho", "revisao",
                  "classificacao_original", "motivo_original", "evidencia_original", "classificacao_reavaliada",
                  "motivo_reavaliado", "evidencia_reavaliada", "estado_atual_origem", "tipologia", "aprovacao_formal",
                  "entrega_formal", "correspondencia_parceiro", "o_que_falta", "recomendacao", "decisao_humana"], pend)
    na = resumo_nao_aprovados(con, a.rodovia)
    escrever_csv(out / "04b_NAO_APROVADOS_RESUMO.csv",
                 ["disciplina_origem", "revisao_aprovada", "status_controle", "motivo", "arquivos", "bytes"], na)

    # ---------- reconciliacao
    tot = {r[0] + "|" + r[1]: r[2] for r in con.execute(
        "SELECT classificacao, estado, COUNT(*) FROM plano WHERE rodovia=? GROUP BY 1, 2", (a.rodovia,))}
    n_plano = sum(tot.values())
    n_origem = con.execute("SELECT COUNT(*) FROM origem_item WHERE eh_pasta=0 AND ((fonte='Programa' AND caminho LIKE "
                           "'RD103/%') OR fonte='BM_RD103')").fetchone()[0]
    val = con.execute("SELECT COUNT(*), SUM(o.tamanho) FROM plano p JOIN origem_item o USING (drive_id,item_id) WHERE "
                      "p.rodovia=? AND p.estado='VALIDADO'", (a.rodovia,)).fetchone()
    eleg = con.execute("SELECT COUNT(*) FROM plano WHERE rodovia=? AND (classificacao='AUSENTE_E_ELEGIVEL' OR "
                       "estado='VALIDADO')", (a.rodovia,)).fetchone()[0]
    parceiro_go = con.execute("SELECT COUNT(*), SUM(tamanho) FROM parceiro_item WHERE foto_id=? AND eh_pasta=0 AND caminho "
                          "LIKE ?", (foto, raiz + "/%")).fetchone()
    casados = con.execute("""SELECT COUNT(*) FROM plano p JOIN origem_item o USING (drive_id,item_id) JOIN parceiro_item i
        ON i.foto_id=? AND i.fileid=p.fileid_destino AND i.caminho=p.destino_rel AND i.tamanho=o.tamanho
        WHERE p.rodovia=? AND p.estado='VALIDADO'""", (foto, a.rodovia)).fetchone()[0]
    hash_ok = con.execute("""SELECT COUNT(*) FROM plano p JOIN origem_item o USING (drive_id,item_id) WHERE p.rodovia=?
        AND p.estado='VALIDADO' AND p.sha256 IS NOT NULL AND p.quick_xor_dest = o.quick_xor_hash""", (a.rodovia,)).fetchone()[0]
    dup_nossos = con.execute("""SELECT COUNT(*), SUM(n) FROM (SELECT quick_xor_dest, COUNT(*) n FROM plano WHERE rodovia=?
        AND estado='VALIDADO' GROUP BY quick_xor_dest HAVING n > 1)""", (a.rodovia,)).fetchone()
    base_alt = con.execute("""SELECT COUNT(*) FROM parceiro_item b JOIN parceiro_item f ON f.fileid=b.fileid AND f.foto_id=?
        WHERE b.foto_id=1 AND b.eh_pasta=0 AND (b.etag!=f.etag OR b.tamanho!=f.tamanho OR b.caminho!=f.caminho)""",
                           (foto,)).fetchone()[0]
    base_aus = con.execute("SELECT COUNT(*) FROM parceiro_item b WHERE foto_id=1 AND NOT EXISTS (SELECT 1 FROM parceiro_item f "
                           "WHERE f.foto_id=? AND f.fileid=b.fileid)", (foto,)).fetchone()[0]
    fora = con.execute("SELECT COUNT(*) FROM envio WHERE destino_rel NOT LIKE ?", (raiz + "/%",)).fetchone()[0]
    estrut = Counter(x[2] for x in est)
    nom = Counter(x[7] for x in nomes)
    tip = Counter(x[14] for x in pend)
    rec = {
        "quando": agora(), "rodovia": a.rodovia, "foto_parceiro": foto,
        "controles_usados": {m_["nome"]: {"etag": m_["etag"], "modificado": m_["modificado"], "item_id": m_["item_id"],
                                          "sha256": m_["sha256"], "consultado": m_["baixado_em"]} for m_ in meta},
        "origem_arquivos_considerados": n_origem, "linhas_no_plano": n_plano,
        "plano_por_classificacao_estado": tot,
        "A_conclusao_operacional": {"elegiveis": eleg, "validados": val[0], "bytes_validados": val[1],
                                    "pct": round(100 * val[0] / eleg, 4) if eleg else None},
        "B_situacao_documental_denominador": n_origem,
        "B_distribuicao": {k: v for k, v in Counter(
            ("TRANSFERIDO_VALIDADO" if r[1] == "VALIDADO" else
             "PENDENTE_DECISAO" if r[0] in PENDENTES or r[1] in ("ERRO", "NA_FILA") else
             "NAO_APROVADO" if r[0] == "NAO_APROVADO" else
             "FORA_DO_ESCOPO" if r[0] == "FORA_DO_ESCOPO" else r[0])
            for r in con.execute("SELECT classificacao, estado FROM plano WHERE rodovia=?", (a.rodovia,))).items()},
        "pendentes": len(pend), "pendentes_por_tipologia": dict(tip),
        "pendentes_liberaveis_com_controles_atuais": sum(1 for x in pend if x[19].startswith("LIBERAVEL")),
        "parceiro_arquivos_na_arvore": parceiro_go[0], "parceiro_bytes_na_arvore": parceiro_go[1],
        "validados_casados_na_foto": casados, "validados_com_hash_igual_microsoft": hash_ok,
        "ja_existentes_pulados": tot.get("JA_EXISTIA_EM_OUTRO_DESTINO|PLANEJADO", 0) + tot.get("JA_EXISTIA_CORRETO|PULADO", 0),
        "conteudo_repetido_entre_nossos": {"grupos": dup_nossos[0], "arquivos": dup_nossos[1]},
        "conflitos_concorrentes": sum(v for k, v in tot.items() if k.startswith("CONFLITO_CONCORRENTE")),
        "estrutura": dict(estrut), "nomenclatura": dict(nom),
        "nomes_nao_conformes": nom.get("NAO_CONFORME", 0),
        "pastas_nao_conformes": estrut.get("NAO_CONFORME", 0),
        "baseline_alterado": base_alt, "baseline_ausente": base_aus, "escritas_fora_da_rodovia": fora,
    }
    rec["checagens"] = {
        "plano_cobre_toda_origem": n_plano == n_origem,
        "validados_igual_elegiveis": val[0] == eleg,
        "arvore_parceiro_igual_validados": parceiro_go[0] == val[0],
        "todos_validados_casados": casados == val[0],
        "todos_hash_microsoft": hash_ok == val[0],
        "baseline_sem_alteracao_pelo_motor": fora == 0 and base_alt == 0,
        "baseline_ausente_explicado_por_terceiros": base_aus == con.execute(
            "SELECT COUNT(*) FROM alteracao_externa WHERE tipo LIKE 'REMOVIDO%' AND nosso=0").fetchone()[0],
    }
    (out / "RECONCILIACAO.json").write_text(json.dumps(rec, ensure_ascii=False, indent=1, default=str), encoding="utf-8")

    # ---------- errata de evidencia (achado A1): regra corrigida x evidencia gravada no plano (que nao e alterado)
    novo = {(ln["it"]["drive_id"], ln["it"]["item_id"]): ln for ln in P.planejar_rd103(con, ctl, raiz).linhas}
    errata = []
    for r in con.execute("SELECT drive_id, item_id, destino_rel, evidencia FROM plano WHERE rodovia=? AND "
                         "estado='VALIDADO' AND categoria='EXECUTIVO'", (a.rodovia,)):
        n = novo.get((r[0], r[1]))
        if n and n["evidencia"] != r[3]:
            errata.append((r[1], r[2], r[3], n["evidencia"], n["classificacao"],
                           "evidencia corrigida; arquivo continua elegivel" if n["classificacao"] == "AUSENTE_E_ELEGIVEL"
                           else "ATENCAO: com a regra corrigida o arquivo NAO seria elegivel"))
    escrever_csv(out / "08_ERRATA_EVIDENCIA.csv", ["item_id", "destino", "evidencia_gravada", "evidencia_corrigida",
                                                   "classificacao_com_regra_corrigida", "conclusao"], errata)
    rec["errata_evidencia"] = {"arquivos": len(errata),
                               "continuam_elegiveis": sum(1 for e in errata if e[4] == "AUSENTE_E_ELEGIVEL")}
    (out / "RECONCILIACAO.json").write_text(json.dumps(rec, ensure_ascii=False, indent=1, default=str), encoding="utf-8")

    # ---------- matriz de decisoes
    dec = []
    grupos = defaultdict(list)
    for x in pend:
        grupos[(x[1], x[14])].append(x)
    prio = {}
    for (cat, tipo), xs in sorted(grupos.items(), key=lambda kv: -len(kv[1])):
        lib = sum(1 for x in xs if x[19].startswith("LIBERAVEL"))
        dec.append((("ALTA" if lib else "MEDIA") if prio.get(tipo) != 3 else "BAIXA", "PENDENTE", cat, tipo, len(xs),
                    sum(x[5] or 0 for x in xs), xs[0][2] + (f" (+{len(xs) - 1})" if len(xs) > 1 else ""), xs[0][18],
                    f"{lib} liberavel(is) com os controles atuais" if lib else "manter fora ate evidencia",
                    "aprovar etapa complementar G2-RD103-C1" if lib else "decidir/obter evidencia", "SIM"))
    for d_ in na:
        dec.append(("MEDIA", "NAO_APROVADO", "EXECUTIVO", d_[3][:80], d_[4], d_[5], d_[0], "controle 03-Acompanhamento",
                    "nao transferir enquanto o status nao for 'Aprovado'", "acompanhar nova revisao", "NAO (regra)"))
    nn = Counter((y[1], y[7], y[8] or y[9][:60]) for y in nomes if y[7] != "CONFORME")
    for (cat, st, p), k in nn.most_common():
        dec.append(("BAIXA" if st == "INDETERMINADO" else "MEDIA", "NOMENCLATURA", cat, p, k, None, "", "",
                    "renomear pasta/arquivo no PARCEIRO (nao executado)", "autorizar correcao", "SIM"))
    escrever_csv(out / "05_MATRIZ_DECISOES_HUMANAS.csv",
                 ["prioridade", "tipo", "categoria", "problema", "arquivos", "bytes", "documento_exemplo", "evidencia",
                  "recomendacao", "acao_proposta", "autorizacao"], dec)
    print(json.dumps({k: rec[k] for k in ("A_conclusao_operacional", "B_distribuicao", "pendentes",
                                          "pendentes_por_tipologia", "pendentes_liberaveis_com_controles_atuais",
                                          "estrutura", "nomenclatura", "checagens", "ja_existentes_pulados",
                                          "conteudo_repetido_entre_nossos", "baseline_alterado", "baseline_ausente",
                                          "escritas_fora_da_rodovia")}, ensure_ascii=False, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
