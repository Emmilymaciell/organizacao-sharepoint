"""
377_g2_excluir_bm_fora_do_periodo - retira do PARCEIRO os Boletins de Medicao com mes de referencia POSTERIOR ao
mes-limite de cada rodovia (destino.bm_ate do yaml). Instrucao da Emmily, 29/10/2026: no repositorio PARCEIRO so
ficam BM ate junho/2026 (RD-104, RD-103) e ate maio/2026 (RD-101, RD-102A); "se foi subido BM posterior a esses
meses vc pode excluir do repositorio PARCEIRO e registrar isso no relatorio".

Unica excecao a regra "nada e apagado" desta operacao, restrita a este ramo e a este criterio.

Padrao: SIMULA (nada e apagado). Com --executar:
  arquivos ENVIADOS POR ESTA MIGRACAO (plano VALIDADO, categoria BM) em
    <arvore>/02. Boletim de Medição/01. Mensal/<AAAA-MM - Mes>/...  com AAAA-MM > limite:
      1. PROPFIND: o arquivo tem de estar no mesmo caminho, com o mesmo oc:fileid e o mesmo tamanho validados;
      2. DELETE com If-Match: <etag do passo 1> (se alguem mexeu no meio, 412: nao apaga, registra);
      3. confirma que sumiu; plano.estado = EXCLUIDO_CORTE_BM; linha na tabela exclusao.
  pastas de mes fora do limite que ESTA MIGRACAO criou: MANTIDAS e registradas (o Nextcloud recusa DELETE
  condicional em pasta e o incondicional apagaria o que um terceiro tivesse posto dentro).
Com --preexistentes (so com autorizacao especifica da Emmily para os arquivos de terceiros): os arquivos que NAO
foram criados pelo motor, em pastas de BM com mes > limite, inalterados desde a foto mais recente (mesmo fileid e
etag). Sem a opcao, eles so aparecem na simulacao.
No Nextcloud o DELETE leva o item para a lixeira do dono (recuperavel pelo prazo da lixeira).

Saida: output/migracao_parceiro_g2/EXCLUSOES_BM_FORA_DO_PERIODO.csv (todas as tentativas, inclusive simulacao)
Uso:   python scripts/377_g2_excluir_bm_fora_do_periodo.py [--rodovia RD-102A] [--executar] [--preexistentes]
"""

from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "scripts"))

from migra_g2.base import agora, cfg, conectar, escrever_csv, gib, num, saida  # noqa: E402

RE_MES = re.compile(r"/(\d{4}-\d{2}) - [^/]+(/|$)")
SQL_TAB = """CREATE TABLE IF NOT EXISTS exclusao (
    em TEXT NOT NULL, rodovia TEXT, tipo TEXT, origem TEXT, caminho TEXT, fileid TEXT, etag TEXT, tamanho INTEGER,
    quick_xor TEXT, resultado TEXT, http INTEGER, motivo TEXT)"""
CAB = ["em", "rodovia", "tipo", "origem", "caminho", "fileid", "etag", "tamanho", "quick_xor", "resultado", "http",
       "motivo"]


def nfc(s):
    return unicodedata.normalize("NFC", s or "")


def mes_do_bm(caminho: str, raiz: str) -> str | None:
    rel = caminho[len(raiz) + 1:]
    if not rel.startswith("02. Boletim de Medição/"):
        return None
    m = RE_MES.search("/" + rel)
    return m.group(1) if m else None


def aspas(etag: str) -> str:
    return etag if etag.startswith('"') else f'"{etag}"'


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rodovia")
    ap.add_argument("--executar", action="store_true")
    ap.add_argument("--preexistentes", action="store_true")
    a = ap.parse_args()
    c = cfg()
    limites = c["destino"].get("bm_ate") or {}
    if a.executar and c["fase"] != "execucao":
        print(f"BLOQUEADO: fase {c['fase']!r}")
        return 1
    con = conectar()
    con.execute("PRAGMA busy_timeout = 900000")      # a foto do monitor (362) grava milhares de linhas de uma vez:
    con.execute(SQL_TAB)                             # espera ate 15 min pelo banco em vez de abortar no meio
    from migra_g2.credencial import parceiro
    from migra_g2.governador import baixar_prioridade_propria, esperar_folga
    print("prioridade:", baixar_prioridade_propria())
    nc = parceiro(c)
    reg = []

    def anotar(go, tipo, origem, cam, fid, et, tam, qx, res, http, motivo):
        linha = (agora(), go, tipo, origem, cam, fid, et, tam, qx, res, http, motivo)
        reg.append(linha)
        if a.executar:
            con.execute("INSERT INTO exclusao VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", linha)
            con.commit()

    def apagar(cam: str, et: str) -> int:
        st, _ = nc._req_ret("DELETE", nc.url_arquivo(cam), {"If-Match": aspas(et)})
        return st

    foto = con.execute("SELECT MAX(id) FROM parceiro_foto WHERE fim IS NOT NULL").fetchone()[0]
    nossos = {r[0] for r in con.execute("SELECT fileid_destino FROM plano WHERE fileid_destino IS NOT NULL")}
    for go, raiz in c["destino"]["arvores"].items():
        if a.rodovia and go != a.rodovia or go not in limites:
            continue
        lim = limites[go]
        # ---- arquivos enviados por nos
        linhas = con.execute(
            "SELECT p.drive_id, p.item_id, p.destino_rel, p.fileid_destino, p.quick_xor_dest, o.tamanho, o.caminho"
            " FROM plano p JOIN origem_item o USING (drive_id, item_id) WHERE p.rodovia=? AND p.categoria='BM'"
            " AND p.estado='VALIDADO'", (go,)).fetchall()
        alvos = [r for r in linhas if (mes_do_bm(nfc(r[2]), raiz) or "") > lim]
        print(f"{go}: limite {lim} | enviados por esta migracao apos o limite: {num(len(alvos))} "
              f"({gib(sum(r[5] or 0 for r in alvos))} GiB)")
        for d, i, cam, fid, qx, tam, orig in alvos:
            cam = nfc(cam)
            motivo = f"BM de {mes_do_bm(cam, raiz)} > limite {lim}"
            if not a.executar:
                anotar(go, "ARQUIVO_NOSSO", orig, cam, fid, None, tam, qx, "SIMULADO", None, motivo)
                continue
            esperar_folga(c.get("recursos"))
            e = nc.estado(cam)
            if e is None:
                anotar(go, "ARQUIVO_NOSSO", orig, cam, fid, None, tam, qx, "JA_AUSENTE", None, motivo)
                con.execute("UPDATE plano SET estado='EXCLUIDO_CORTE_BM', erro=?, atualizado_em=? WHERE drive_id=? AND"
                            " item_id=? AND destino_rel IS ?", ("ja ausente no PARCEIRO: " + motivo, agora(), d, i, cam))
                con.commit()
                continue
            if e.eh_pasta or e.fileid != fid or e.tamanho != tam:
                anotar(go, "ARQUIVO_NOSSO", orig, cam, e.fileid, e.etag, e.tamanho, qx, "PRESERVADO_DIVERGE", None,
                       motivo + f"; no PARCEIRO fileid {e.fileid} tamanho {e.tamanho} (esperado {fid}/{tam}): nao e o nosso")
                continue
            # tira do monitor do executor ANTES de apagar (senao ele pausaria por 'arquivo nosso sumiu')
            con.execute("UPDATE plano SET estado='EXCLUINDO', atualizado_em=? WHERE drive_id=? AND item_id=? AND"
                        " destino_rel IS ?", (agora(), d, i, cam))
            con.commit()
            st = apagar(cam, e.etag)
            ok = st in (200, 204) and nc.estado(cam) is None
            con.execute("UPDATE plano SET estado=?, erro=?, atualizado_em=? WHERE drive_id=? AND item_id=? AND"
                        " destino_rel IS ?", ("EXCLUIDO_CORTE_BM" if ok else "VALIDADO",
                                              (motivo if ok else None), agora(), d, i, cam))
            con.commit()
            anotar(go, "ARQUIVO_NOSSO", orig, cam, fid, e.etag, tam, qx, "EXCLUIDO" if ok else f"NAO_EXCLUIDO",
                   st, motivo + ("" if ok else f"; DELETE respondeu {st} (412 = alterado no meio)"))
        # ---- arquivos preexistentes (terceiros)
        pre = [r for r in con.execute("SELECT caminho, fileid, etag, tamanho FROM parceiro_item WHERE foto_id=? AND"
                                      " eh_pasta=0 AND caminho LIKE ?", (foto, raiz + "/%"))
               if r[1] not in nossos and (mes_do_bm(nfc(r[0]), raiz) or "") > lim]
        if pre:
            print(f"{go}: preexistentes (terceiros) apos o limite: {num(len(pre))} ({gib(sum(r[3] or 0 for r in pre))}"
                  f" GiB) -> {'EXCLUIR' if a.preexistentes and a.executar else 'so registrados'}")
        for cam, fid, et, tam in pre:
            cam = nfc(cam)
            motivo = f"BM de {mes_do_bm(cam, raiz)} > limite {lim} (arquivo preexistente, nao enviado por esta migracao)"
            if not (a.executar and a.preexistentes):
                anotar(go, "ARQUIVO_PREEXISTENTE", "", cam, fid, et, tam, None, "SIMULADO", None, motivo)
                continue
            esperar_folga(c.get("recursos"))
            e = nc.estado(cam)
            if e is None or e.eh_pasta or e.fileid != fid or e.etag != et:
                anotar(go, "ARQUIVO_PREEXISTENTE", "", cam, fid, et, tam, None, "PRESERVADO_ALTERADO", None,
                       motivo + "; mudou desde a foto (ou sumiu): nao apagado")
                continue
            st = apagar(cam, e.etag)
            ok = st in (200, 204) and nc.estado(cam) is None
            anotar(go, "ARQUIVO_PREEXISTENTE", "", cam, fid, et, tam, None, "EXCLUIDO" if ok else "NAO_EXCLUIDO", st,
                   motivo)
        # ---- pastas de mes fora do limite: NAO sao apagadas (29/10/2026). O Nextcloud recusa DELETE condicional em
        # pasta (412) e um DELETE de pasta sem condicao apaga tudo o que estiver dentro, inclusive algo que um
        # terceiro acabou de colocar. Pasta vazia nao guarda BM: fica registrada para decisao humana.
        criadas = sorted(nfc(r[0]) for r in con.execute(
            "SELECT caminho FROM pasta_criada WHERE resultado='CRIADA' AND caminho LIKE ?", (raiz + "/%",))
            if (mes_do_bm(nfc(r[0]), raiz) or "") > lim)
        for cam in criadas:
            anotar(go, "PASTA_NOSSA", "", cam, None, None, None, None, "MANTIDA", None,
                   "pasta de mes fora do limite criada por esta migracao; mantida (sem DELETE de pasta): decisao humana")

    out = saida(c) / ("EXCLUSOES_BM_FORA_DO_PERIODO.csv" if a.executar else "EXCLUSOES_BM_FORA_DO_PERIODO_SIMULACAO.csv")
    if a.executar and out.exists():                      # nunca sobrescreve o historico de execucoes
        out = out.with_name(out.stem + f"_{agora()[:19].replace(':', '')}.csv")
    escrever_csv(out, CAB, reg)
    from collections import Counter
    print(dict(Counter((r[1], r[2], r[9]) for r in reg)))
    print(f"-> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
