"""
384_g2_decisoes_20261104 - executa as decisoes da Emmily de 04/11/2026 sobre as pendencias G2 (PARCEIRO INFRA).

Acoes (cada uma simula sem --executar; com --executar exige fase 'execucao' - PD-18):
  nc    Renomeia as pastas de NC/NI (07. Nao Conformidades/<mes>/<pasta>) cujo assunto estava cortado em 90 caracteres:
        "<NC> - <data> - <assunto resumido: estaca/local + problema>". Pedido: "REVISE TODOS E RENOMEIE COM O ASSUNTO
        PRINCIPAL RESUMIDO, PRA NAO CORTAR NADA, COLOCA A ESTACA OU INFO PRINCIPAL SE TIVER E O PROBLEMA".
        Resumos: --resumos (arquivo 'indice|resumo', indice da lista gerada pela foto). So a pasta e renomeada (MOVE
        com Overwrite: F; fileid preservado); os arquivos dentro nao mudam. plano.destino_rel acompanha.
  pd09  RD-104, pasta paralela '01.Projetos': copia comprovada (tamanho + QuickXor iguais a um arquivo da EAP) e
        excluida; o que NAO e copia e movido para o lugar equivalente dentro de '01. Projetos' (nada unico e apagado).
        Depois, a arvore paralela sai se nao tiver mais nenhum arquivo.
  pd11  Itens que nao tinham sido movidos (423/origem alterada) e continuam fora do lugar: destino livre -> MOVE;
        destino ocupado com conteudo identico -> a copia fora do lugar e excluida; diferente -> mantido e listado.
  word  Relatorios SEMANAIS em Word sem PDF (PD-03): baixa o Word, gera o PDF no Word (COM, scratch em Temp), envia o
        PDF na mesma pasta (PUT If-None-Match), confere o PDF relido (SHA-256) e so entao exclui o Word (If-Match).

Toda escrita: escopo/fase por base.escrita_permitida; MOVE com Overwrite: F; DELETE de arquivo com If-Match: <etag>;
pastas relidas imediatamente antes. Exclusoes vao para a lixeira do Nextcloud. Registro: tabelas movimentacao e
exclusao + output/migracao_parceiro_g2/DECISOES_20261104_<ACAO>[_SIMULACAO].csv.

Uso: python scripts/384_g2_decisoes_20261104.py --acao nc|pd09|pd11|word [--executar] [--foto N] [--resumos F]
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
import tempfile
import unicodedata
from collections import Counter
from pathlib import Path, PurePosixPath

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "scripts"))

from migra_g2.base import agora, cfg, conectar, escrever_csv, escrita_permitida, saida  # noqa: E402
from migra_g2.trava import exigir_fase_execucao  # noqa: E402

INSTR = "instrucao da Emmily 04/11/2026"


def nfc(s):
    return unicodedata.normalize("NFC", s or "")


def limpo(s: str) -> str:
    s = re.sub(r'[\\/:*?"<>|\r\n\t]+', " ", str(s or "")).strip().rstrip(".")
    return re.sub(r"\s+", " ", s)


class Ops:
    def __init__(self, a, c, con):
        self.a, self.c, self.con = a, c, con
        self.nc = None
        self.reg: list[list] = []
        if a.executar:
            from migra_g2.credencial import parceiro
            from migra_g2.governador import baixar_prioridade_propria
            baixar_prioridade_propria()
            self.nc = parceiro(c)

    # ------------------------------------------------------------------ primitivas
    def mkcol_cadeia(self, pasta: str, raiz: str):
        partes = pasta[len(raiz) + 1:].split("/")
        for k in range(1, len(partes) + 1):
            p = raiz + "/" + "/".join(partes[:k])
            if self.nc.estado(p) is None:
                self.nc.criar_pasta(p, self.c)

    def mover(self, src: str, dst: str, eh_pasta: bool, fileid: str | None) -> tuple[str, int | None]:
        ok, mot = escrita_permitida(dst, eh_pasta, self.c)
        if not ok:
            return f"BLOQUEADO: {mot}", None
        e = self.nc.estado(src)
        if e is None or (fileid and e.fileid != fileid):
            return "PRESERVADO_ALTERADO", None
        if self.nc.estado(dst) is not None:
            return "DESTINO_OCUPADO", None
        st, _ = self.nc._req_ret("MOVE", self.nc.url_arquivo(src),
                                 {"Destination": self.nc.url_arquivo(dst), "Overwrite": "F"})
        if st == 412:
            return "DESTINO_OCUPADO", st
        d = self.nc.estado(dst)
        if st in (201, 204) and d is not None and d.fileid == e.fileid and self.nc.estado(src) is None:
            return "MOVIDO", st
        return "NAO_MOVIDO", st

    def excluir_arquivo(self, cam: str, fileid: str, manter: str | None = None) -> tuple[str, int | None, object]:
        e = self.nc.estado(cam)
        if e is None or e.eh_pasta or e.fileid != fileid:
            return "PRESERVADO_ALTERADO", None, e
        if manter and self.nc.estado(manter) is None:
            return "PRESERVADO_ORIGINAL_SUMIU", None, e
        st, _ = self.nc._req_ret("DELETE", self.nc.url_arquivo(cam), {"If-Match": f'"{e.etag}"'})
        ok = st in (200, 204) and self.nc.estado(cam) is None
        return ("EXCLUIDO" if ok else "NAO_EXCLUIDO"), st, e

    def qxh(self, cam: str) -> str:
        from migra_parceiro.quickxor import QuickXorHash
        q = QuickXorHash()
        for b in self.nc.ler_stream(cam):
            q.update(b)
        return q.base64()

    def mov_db(self, go, arquivo, de, para, fileid, tam, res, http, motivo):
        self.con.execute("INSERT INTO movimentacao VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                         (agora(), go, arquivo, de, para, fileid, None, tam or 0, res, http, motivo))
        self.con.commit()

    def exc_db(self, go, tipo, cam, fileid, etag, tam, qx, res, http, motivo):
        self.con.execute("INSERT INTO exclusao VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                         (agora(), go, tipo, "", cam, fileid, etag, tam or 0, qx, res, http, motivo))
        self.con.commit()


def foto_atual(con, a) -> int:
    return a.foto or con.execute("SELECT MAX(id) FROM parceiro_foto WHERE fim IS NOT NULL").fetchone()[0]


# ---------------------------------------------------------------------- nc
def lista_nc(con, foto):
    out = []
    for cam, go, fid in con.execute("SELECT caminho, rodovia, fileid FROM parceiro_item WHERE foto_id=? AND eh_pasta=1 "
                                    "ORDER BY rodovia, caminho", (foto,)):
        m = re.search(r"/07\. Não Conformidades/([^/]+)/([^/]+)$", nfc(cam))
        if m:
            out.append((go, nfc(cam), fid, m.group(2)))
    return out


def acao_nc(o: Ops, foto: int):
    linhas = lista_nc(o.con, foto)
    res = {}
    for ln in Path(o.a.resumos).read_text(encoding="utf-8").splitlines():
        if "|" in ln:
            i, t = ln.split("|", 1)
            res[int(i)] = limpo(t)
    if len(res) != len(linhas):
        raise SystemExit(f"resumos ({len(res)}) diferente das pastas de NC na foto {foto} ({len(linhas)}): abortado")
    for i, (go, cam, fid, nome) in enumerate(linhas):
        partes = nome.split(" - ", 2)
        novo = limpo(f"{partes[0]} - {partes[1]} - {res[i]}")
        pai = cam.rsplit("/", 1)[0]
        dst = f"{pai}/{novo}"
        base = [agora(), go, nome, novo, len(novo), fid]
        if novo == nome:
            o.reg.append(base + ["SEM_MUDANCA", None])
            continue
        if not o.a.executar:
            o.reg.append(base + ["SIMULADO", None])
            continue
        r, st = o.mover(cam, dst, True, fid)
        o.reg.append(base + [r, st])
        if r == "MOVIDO":
            n = o.con.execute("UPDATE plano SET destino_rel = ? || substr(destino_rel, ?), atualizado_em=? WHERE "
                              "destino_rel LIKE ? ESCAPE '\\'",
                              (dst, len(cam) + 1, agora(),
                               cam.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "/%")).rowcount
            o.mov_db(go, f"{nome} -> {novo}", pai, pai, fid, 0, "PASTA_RENOMEADA", st,
                     f"assunto da NC resumido (estava cortado em 90 caracteres); plano atualizado: {n}; {INSTR}")
    return ["em", "rodovia", "pasta_antiga", "pasta_nova", "caracteres", "fileid", "resultado", "http"]


# ---------------------------------------------------------------------- pd09
def hashes_db(con) -> dict:
    h = dict(con.execute("SELECT fileid, quick_xor FROM parceiro_hash"))
    for fid, qx in con.execute("SELECT fileid_destino, quick_xor_dest FROM plano WHERE fileid_destino IS NOT NULL"):
        h.setdefault(fid, qx)
    return h


def mapa_pd09(rel: str) -> str:
    for de, para in (("01.Projetos/01. Anteprojeto/", "01. Projetos/01. Anteprojeto/"),
                     ("01.Projetos/02. Executivo/1. Fase de Estudos/", "01. Projetos/02. Executivo/01. FASE DE ESTUDOS/"),
                     ("01.Projetos/04. ART/", "01. Projetos/02. Executivo/01. FASE DE ESTUDOS/ART/")):
        if rel.startswith(de):
            return para + rel[len(de):]
    raise ValueError(f"sem mapeamento: {rel}")


def acao_pd09(o: Ops, foto: int):
    go = "RD-104"
    raiz = nfc(o.c["destino"]["arvores"][go])
    par = raiz + "/01.Projetos"
    itens = [(nfc(c), t, f, e) for c, t, f, e in o.con.execute(
        "SELECT caminho, tamanho, fileid, etag FROM parceiro_item WHERE foto_id=? AND eh_pasta=0 AND rodovia=?", (foto, go))]
    h = hashes_db(o.con)
    eap = [x for x in itens if x[0].startswith(raiz + "/01. Projetos/")]
    for cam, tam, fid, etag in [x for x in itens if x[0].startswith(par + "/")]:
        rel = cam[len(raiz) + 1:]
        copias = [c for c, t, f, _ in eap if t == tam and h.get(f) and h.get(f) == h.get(fid)]
        base = [agora(), go, rel]
        if copias:
            mant = copias[0]
            if not o.a.executar:
                o.reg.append(base + [mant[len(raiz) + 1:], "SIMULADO_EXCLUIR_COPIA", None])
                continue
            r, st, e = o.excluir_arquivo(cam, fid, manter=mant)
            o.reg.append(base + [mant[len(raiz) + 1:], r, st])
            o.exc_db(go, "COPIA_PASTA_PARALELA", cam, fid, e.etag if e else None, tam, h.get(fid), r, st,
                     f"copia comprovada (tamanho + QuickXor) de {mant[len(raiz) + 1:]} (mantido); PD-09; {INSTR}")
            continue
        dst = raiz + "/" + mapa_pd09(rel)
        if not o.a.executar:
            o.reg.append(base + [dst[len(raiz) + 1:], "SIMULADO_MOVER", None])
            continue
        o.mkcol_cadeia(dst.rsplit("/", 1)[0], raiz)
        r, st = o.mover(cam, dst, False, fid)
        o.reg.append(base + [dst[len(raiz) + 1:], r, st])
        o.mov_db(go, PurePosixPath(rel).name, str(PurePosixPath(rel).parent), str(PurePosixPath(dst[len(raiz) + 1:]).parent),
                 fid, tam, r, st, f"pasta paralela 01.Projetos -> EAP (nao e copia: nada igual na EAP); PD-09; {INSTR}")
    return ["em", "rodovia", "arquivo_na_pasta_paralela", "lugar_na_eap", "resultado", "http"]


# ---------------------------------------------------------------------- pd11
def acao_pd11(o: Ops, foto: int):
    byfid = {f: nfc(c) for c, f in o.con.execute("SELECT caminho, fileid FROM parceiro_item WHERE foto_id=? AND eh_pasta=0",
                                                 (foto,))}
    vistos = set()
    for go, nome, de, para, fid, tam in o.con.execute(
            "SELECT rodovia, arquivo, de, para, fileid, tamanho FROM movimentacao WHERE resultado IN ('NAO_MOVIDO',"
            "'NAO_MOVIDO_CORRIGIDO','PRESERVADO_ALTERADO_CORRIGIDO')").fetchall():
        if fid in vistos:
            continue
        vistos.add(fid)
        raiz = nfc(o.c["destino"]["arvores"][go])
        src, dst = f"{raiz}/{nfc(de)}/{nfc(nome)}", f"{raiz}/{nfc(para)}/{nfc(nome)}"
        if byfid.get(fid) != src or nfc(de).startswith("01.Projetos/"):      # ja no lugar, ou tratado na pd09
            continue
        base = [agora(), go, nome, de, para]
        ocupado = dst in byfid.values()
        if not o.a.executar:
            o.reg.append(base + ["SIMULADO_" + ("COMPARAR" if ocupado else "MOVER"), None, ""])
            continue
        if not ocupado:
            o.mkcol_cadeia(dst.rsplit("/", 1)[0], raiz)
            r, st = o.mover(src, dst, False, fid)
            o.reg.append(base + [r, st, ""])
            o.mov_db(go, nome, de, para, fid, tam, r, st, f"nova tentativa (PD-11); {INSTR}")
            continue
        q1, q2 = o.qxh(src), o.qxh(dst)
        if q1 != q2:
            o.reg.append(base + ["MANTIDO_DIFERENTE", None, f"QuickXor {q1} x {q2}: nao e copia"])
            continue
        r, st, e = o.excluir_arquivo(src, fid, manter=dst)
        o.reg.append(base + [r, st, f"copia comprovada (QuickXor {q1})"])
        o.exc_db(go, "COPIA_FORA_DA_EAP", src, fid, e.etag if e else None, tam, q1, r, st,
                 f"copia comprovada de {para}/{nome} (mantido no lugar da EAP); PD-11; {INSTR}")
    return ["em", "rodovia", "arquivo", "de", "para", "resultado", "http", "obs"]


# ---------------------------------------------------------------------- word
def acao_word(o: Ops, foto: int):
    alvo = []
    for cam, tam, fid, etag, go in o.con.execute(
            "SELECT caminho, tamanho, fileid, etag, rodovia FROM parceiro_item WHERE foto_id=? AND eh_pasta=0", (foto,)):
        cam = nfc(cam)
        pasta_alvo = ("/06. Relatório de Obras/01. Semanal/" if o.a.alvo == "semanal" else
                      "PARCEIRO-INFRA/03. RD-101/04. Engenharia/06. Relatório de Obras/02. Mensal/")
        if pasta_alvo in cam and PurePosixPath(cam).suffix.lower() in (".doc", ".docx"):
            alvo.append((go, cam, tam, fid, etag))
    todos = {nfc(c) for c, in o.con.execute("SELECT caminho FROM parceiro_item WHERE foto_id=? AND eh_pasta=0", (foto,))}
    tmp = Path(tempfile.mkdtemp(prefix="g2_word_", dir=os.environ.get("G2_SCRATCH") or None))
    word = None
    try:
        for go, cam, tam, fid, etag in sorted(alvo):
            pdf = str(PurePosixPath(cam).with_suffix(".pdf"))
            base = [agora(), go, cam.split("/04. Engenharia/", 1)[1], PurePosixPath(pdf).name]
            if pdf in todos:
                o.reg.append(base + ["JA_TEM_PDF", None, ""])
                continue
            if not o.a.executar:
                o.reg.append(base + ["SIMULADO", None, ""])
                continue
            e = o.nc.estado(cam)
            if e is None or e.fileid != fid:
                o.reg.append(base + ["PRESERVADO_ALTERADO", None, ""])
                continue
            loc = tmp / f"{fid}{PurePosixPath(cam).suffix.lower()}"
            with open(loc, "wb") as f:
                for b in o.nc.ler_stream(cam):
                    f.write(b)
            locpdf = loc.with_suffix(".pdf")
            if not locpdf.exists():
                import pythoncom
                import win32com.client
                pythoncom.CoInitialize()
                word = win32com.client.DispatchEx("Word.Application")      # instancia propria, uma por documento
                word.Visible = False
                word.DisplayAlerts = 0
                try:
                    doc = word.Documents.Open(str(loc), ReadOnly=True, AddToRecentFiles=False, ConfirmConversions=False)
                    doc.ExportAsFixedFormat(str(locpdf), 17)          # wdExportFormatPDF
                    try:
                        doc.Close(0)
                    except Exception:  # noqa: BLE001  (o Word as vezes cai ao fechar; o PDF ja foi gravado)
                        pass
                finally:
                    try:
                        word.Quit()
                    except Exception:  # noqa: BLE001
                        pass
                    word = None
            dados = locpdf.read_bytes()
            if not (dados[:5] == b"%PDF-" and b"%%EOF" in dados[-1024:]):
                o.reg.append(base + ["PDF_INVALIDO_WORD_MANTIDO", None, f"{len(dados)} B"])
                continue
            sha = hashlib.sha256(dados).hexdigest()
            o.nc.put_se_ausente(pdf, dados, o.c)
            lido = hashlib.sha256()
            for b in o.nc.ler_stream(pdf):
                lido.update(b)
            ep = o.nc.estado(pdf)
            if lido.hexdigest() != sha or ep is None:
                o.reg.append(base + ["PDF_DIVERGENTE_WORD_MANTIDO", None, sha])
                continue
            r, st, e2 = o.excluir_arquivo(cam, fid, manter=pdf)
            o.reg.append(base + ["PDF_CRIADO_" + r, st, f"PDF {len(dados)} B sha256 {sha}"])
            o.exc_db(go, "WORD_CONVERTIDO_PDF", cam, fid, e2.etag if e2 else None, tam, None, r, st,
                     f"relatorio {o.a.alvo}: PDF gerado e enviado ({PurePosixPath(pdf).name}, fileid {ep.fileid}, "
                     f"sha256 {sha}); Word excluido; PD-03; {INSTR}")
    finally:
        if word is not None:
            word.Quit()
    return ["em", "rodovia", "word", "pdf", "resultado", "http", "obs"]



# ---------------------------------------------------------------------- enviar (2a mensagem de 04/11)
def acao_enviar(o: Ops, foto: int):
    """Envia a lista --lista (JSON: drive_id, item_id, rodovia, destino, evidencia, motivo) com PUT If-None-Match /
    chunking com Overwrite: F (migra_g2.envio), confere SHA-256 + QuickXor da Microsoft relendo do PARCEIRO e registra no
    plano. Pula o que ja existe no destino. Nao usa o 367 (PD-19: ele recria pastas removidas da EAP)."""
    import json
    lista = json.loads(Path(o.a.lista).read_text(encoding="utf-8"))
    g = None
    if o.a.executar:
        from graph_util import Graph
        from migra_g2.envio import conferir, enviar
        from migra_parceiro.retomavel import FonteGraph
        g = Graph()
    for it in lista:
        d, i, go, dest = it["drive_id"], it["item_id"], it["rodovia"], nfc(it["destino"])
        base = [agora(), go, dest.split("/04. Engenharia/", 1)[1]]
        if not o.a.executar:
            o.reg.append(base + ["SIMULADO", None, ""])
            continue
        org = o.con.execute("SELECT tamanho, quick_xor_hash FROM origem_item WHERE drive_id=? AND item_id=?",
                            (d, i)).fetchone()
        meta = g.get(f"/drives/{d}/items/{i}?$select=id,size,eTag,file")
        qx = ((meta.get("file") or {}).get("hashes") or {}).get("quickXorHash")
        if meta.get("size") != org[0] or (org[1] and qx != org[1]):
            o.reg.append(base + ["ORIGEM_ALTERADA", None, "tamanho/hash mudou desde o inventario"])
            continue
        if o.nc.estado(dest) is not None:
            o.reg.append(base + ["JA_EXISTE_NO_DESTINO", None, ""])
            continue
        raiz = nfc(o.c["destino"]["arvores"][go])
        try:
            o.mkcol_cadeia(dest.rsplit("/", 1)[0], raiz)
            r = enviar(o.nc, FonteGraph(g, d, i, org[0]), dest, o.c)
            v = conferir(o.nc, dest, org[0], r["sha256_origem"], qx)
        except Exception as ex:  # noqa: BLE001
            o.reg.append(base + ["ERRO", None, f"{type(ex).__name__}: {str(ex)[:200]}"])
            continue
        res = "VALIDADO" if v["validado"] else "DIVERGENTE"
        o.reg.append(base + [res, None, f"{r['metodo']} {org[0]} B"])
        n = o.con.execute("UPDATE plano SET classificacao='AUSENTE_E_ELEGIVEL', estado=?, destino_rel=?, evidencia=?,"
                          " motivo=?, fileid_destino=?, quick_xor_dest=?, sha256=?, copiado_em=?, validado_em=?, erro=NULL,"
                          " atualizado_em=? WHERE drive_id=? AND item_id=?",
                          (res, dest, it.get("evidencia", ""), it.get("motivo", "") + f"; {INSTR}", v["fileid"],
                           v["quick_xor_destino"], v["sha256_destino"], agora(), agora() if v["validado"] else None,
                           agora(), d, i)).rowcount
        if not n:                                       # item novo no inventario de 04/11: entra no plano agora
            o.con.execute("INSERT INTO plano (drive_id, item_id, rodovia, categoria, regra, evidencia, classificacao, motivo,"
                          " destino_rel, estado, tentativas, fileid_destino, sha256, quick_xor_dest, copiado_em, validado_em,"
                          " atualizado_em) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                          (d, i, go, "EXECUTIVO", "nota 02", it.get("evidencia", ""), "AUSENTE_E_ELEGIVEL",
                           it.get("motivo", "") + f"; {INSTR}", dest, res, 1, v["fileid"], v["sha256_destino"],
                           v["quick_xor_destino"], agora(), agora() if v["validado"] else None, agora()))
            o.reg[-1][-1] += " | linha nova no plano"
        o.con.commit()
    return ["em", "rodovia", "destino", "resultado", "http", "obs"]


# ---------------------------------------------------------------------- sst julho (PD-10)
def acao_sst(o: Ops, foto: int):
    go = "RD-101"
    raiz = nfc(o.c["destino"]["arvores"][go])
    mes = f"{raiz}/08. Segurança do Trabalho/2026-07 - Julho"
    par = f"{mes}/MÊS - JULHO"
    subs = [(nfc(c), f) for c, f in o.con.execute("SELECT caminho, fileid FROM parceiro_item WHERE foto_id=? AND eh_pasta=1",
                                                  (foto,))
            if nfc(c).startswith(par + "/") and "/" not in nfc(c)[len(par) + 1:]]
    for cam, fid in sorted(subs):
        nome = cam.rsplit("/", 1)[1]
        dst = f"{mes}/{nome}"
        base = [agora(), go, cam[len(raiz) + 1:], dst[len(raiz) + 1:]]
        if not o.a.executar:
            o.reg.append(base + ["SIMULADO", None])
            continue
        r, st = o.mover(cam, dst, True, fid)
        o.reg.append(base + [r, st])
        o.mov_db(go, nome, par[len(raiz) + 1:], mes[len(raiz) + 1:], fid, 0, "PASTA_MOVIDA" if r == "MOVIDO" else r, st,
                 f"SST de julho/2026: subpasta tirada de 'MÊS - JULHO' para o mes da EAP; PD-10; {INSTR}")
    return ["em", "rodovia", "de", "para", "resultado", "http"]


# ---------------------------------------------------------------------- pd20: exclusao pasta a pasta
def acao_pd20(o: Ops, foto: int):
    import csv
    alvo = [r for r in csv.DictReader(open(saida(o.c) / "PASTAS_VAZIAS_REMOVIDAS_G2.csv", encoding="utf-8-sig"),
                                      delimiter=";") if r["resultado"] == "NAO_EXCLUIDA"]
    alvo += [{"rodovia": "RD-101", "pasta": "08. Segurança do Trabalho/2026-07 - Julho/MÊS - JULHO"}]
    for r in alvo:
        go = r["rodovia"]
        raiz = nfc(o.c["destino"]["arvores"][go])
        topo = f"{raiz}/{nfc(r['pasta'])}"
        if not o.a.executar:
            o.reg.append([agora(), go, r["pasta"], "", "SIMULADO", None])
            continue
        pastas, fila, tem_arq = [], [topo], False          # Depth 1 recursivo; para no primeiro arquivo
        while fila and not tem_arq:
            atual = fila.pop()
            lst = o.nc.listar(atual, "1")
            if lst is None:
                continue
            pastas.append(atual)
            for e in lst[1:]:
                if e.eh_pasta:
                    fila.append(e.caminho)
                else:
                    tem_arq = True
                    break
        if not pastas:
            o.reg.append([agora(), go, r["pasta"], "", "JA_AUSENTE", None])
            continue
        if tem_arq:
            o.reg.append([agora(), go, r["pasta"], "", "MANTIDA_TEM_ARQUIVO", None])
            continue
        for cam in sorted(pastas, key=lambda x: -x.count("/")):        # folhas primeiro, uma de cada vez
            lst = o.nc.listar(cam, "1")
            if lst is None or len(lst) > 1:
                continue
            st, _ = o.nc._req_ret("DELETE", o.nc.url_arquivo(cam))
            ok = st in (200, 204) and o.nc.estado(cam) is None
            o.reg.append([agora(), go, r["pasta"], cam[len(raiz) + 1:], "PASTA_EXCLUIDA" if ok else "NAO_EXCLUIDA", st])
            o.exc_db(go, "PASTA_VAZIA", cam, None, None, 0, None, o.reg[-1][4], st,
                     f"pasta vazia fora da EAP, excluida uma a uma (folhas primeiro); PD-20; {INSTR}")
            if not ok:
                break
    return ["em", "rodovia", "ramo", "pasta", "resultado", "http"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--acao", required=True, choices=["nc", "pd09", "pd11", "word", "enviar", "sst", "pd20"])
    ap.add_argument("--lista")
    ap.add_argument("--alvo", default="semanal", choices=["semanal", "mensal147"])
    ap.add_argument("--executar", action="store_true")
    ap.add_argument("--foto", type=int)
    ap.add_argument("--resumos")
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    c = cfg()
    if a.executar:
        exigir_fase_execucao(c, "384_g2_decisoes_20261104")
    con = conectar()
    con.execute("PRAGMA busy_timeout = 900000")
    foto = foto_atual(con, a)
    o = Ops(a, c, con)
    cab = {"nc": acao_nc, "pd09": acao_pd09, "pd11": acao_pd11, "word": acao_word, "enviar": acao_enviar,
           "sst": acao_sst, "pd20": acao_pd20}[a.acao](o, foto)
    sufx = "_MENSAL147" if a.acao == "word" and a.alvo == "mensal147" else ""
    nome = f"DECISOES_20261104_{a.acao.upper()}{sufx}{'' if a.executar else '_SIMULACAO'}.csv"
    escrever_csv(saida(c) / nome, cab, o.reg)
    res_col = cab.index("resultado")
    print(f"foto {foto} | {a.acao}: {len(o.reg)} linhas |", dict(Counter(r[res_col] for r in o.reg)), "->", nome)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
