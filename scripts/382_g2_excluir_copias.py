"""
382_g2_excluir_copias - exclui as COPIAS comprovadas que ficaram fora da EAP na reorganizacao (379: DESTINO_OCUPADO),
mantendo o documento que esta no lugar certo. Instrucao da Emmily, 30/10/2026 (~15:45): "OQ FOR COMPROVADO COPIA PODE
APAGAR E MANTENHA O DOCUMENTO DO LUGAR CERTO E APAGUE AS COPIAS".

Comprovado = mesmo tamanho E mesmo QuickXor (conteudo) entre a copia fora do lugar e o documento no lugar da EAP.
O hash vem do banco (parceiro_hash por fileid, ou quick_xor_dest do plano para o que a migracao enviou); o que faltar e
lido do PARCEIRO por streaming (sem gravar em disco). Antes do DELETE: as duas pontas relidas (PROPFIND); a do lugar certo
tem de continuar la; DELETE da copia com If-Match: <etag>. Vai para a lixeira do Nextcloud.
Saida: output/migracao_parceiro_g2/COPIAS_EXCLUIDAS_G2.csv; tabela exclusao (tipo COPIA_FORA_DA_EAP).
Uso:   python scripts/382_g2_excluir_copias.py [--executar]
"""

from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "scripts"))

from migra_g2.base import agora, cfg, conectar, escrever_csv, saida  # noqa: E402
from migra_g2.trava import exigir_fase_execucao  # noqa: E402


def nfc(s):
    return unicodedata.normalize("NFC", s or "")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--executar", action="store_true")
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    c = cfg()
    if a.executar:
        exigir_fase_execucao(c, '382_g2_excluir_copias')   # PD-18
    con = conectar()
    con.execute("PRAGMA busy_timeout = 900000")
    from migra_g2.credencial import parceiro
    from migra_g2.governador import baixar_prioridade_propria
    from migra_parceiro.quickxor import QuickXorHash
    baixar_prioridade_propria()
    nc = parceiro(c)
    hashes = {}
    for fid, qx in con.execute("SELECT fileid, quick_xor FROM parceiro_hash"):
        hashes[fid] = qx
    for fid, qx in con.execute("SELECT fileid_destino, quick_xor_dest FROM plano WHERE fileid_destino IS NOT NULL"
                               " AND quick_xor_dest IS NOT NULL"):
        hashes.setdefault(fid, qx)

    def hash_de(e) -> str:
        if e.fileid in hashes:
            return hashes[e.fileid]
        q = QuickXorHash()
        for b in nc.ler_stream(e.caminho):
            q.update(b)
        hashes[e.fileid] = q.base64()
        return hashes[e.fileid]

    # so os que continuam fora do lugar (os corrigidos na 2a passada ja tem _CORRIGIDO)
    linhas = con.execute("SELECT rodovia, arquivo, de, para, fileid, motivo FROM movimentacao WHERE"
                         " resultado='DESTINO_OCUPADO'").fetchall()
    vistos, reg = set(), []
    for go, nome, de, para, fid, mot in linhas:
        if fid in vistos:
            continue
        vistos.add(fid)
        raiz = nfc(c["destino"]["arvores"][go])
        copia, certo = f"{raiz}/{de}/{nome}", f"{raiz}/{para}/{nome}"
        ec, ok_ = nc.estado(copia), nc.estado(certo)
        base = [agora(), go, nome, de, para, fid]
        if ec is None or ec.fileid != fid:
            reg.append(base + [None, None, "JA_AUSENTE_OU_OUTRO", None, "copia nao esta mais la"])
            continue
        if ok_ is None or ok_.eh_pasta:
            reg.append(base + [ec.tamanho, None, "MANTIDA_SEM_ORIGINAL", None, "lugar da EAP vazio: copia mantida"])
            continue
        if ec.tamanho != ok_.tamanho:
            reg.append(base + [ec.tamanho, None, "MANTIDA_DIFERENTE", None, f"tamanhos diferentes ({ec.tamanho} x "
                               f"{ok_.tamanho}): nao e copia"])
            continue
        try:
            h1, h2 = hash_de(ec), hash_de(ok_)
        except Exception as ex:  # noqa: BLE001
            reg.append(base + [ec.tamanho, None, "MANTIDA_SEM_HASH", None, f"falha ao ler: {type(ex).__name__}"])
            continue
        if h1 != h2:
            reg.append(base + [ec.tamanho, h1, "MANTIDA_DIFERENTE", None, f"QuickXor diferente ({h1} x {h2})"])
            continue
        motivo = (f"copia comprovada (mesmo tamanho {ec.tamanho} B e QuickXor {h1}) do documento no lugar da EAP "
                  f"{para}/{nome} (fileid {ok_.fileid}, mantido); instrucao da Emmily 30/10/2026")
        if not a.executar:
            reg.append(base + [ec.tamanho, h1, "SIMULADO", None, motivo])
            continue
        st, _ = nc._req_ret("DELETE", nc.url_arquivo(copia), {"If-Match": f'"{ec.etag}"'})
        ok = st in (200, 204) and nc.estado(copia) is None and nc.estado(certo) is not None
        reg.append(base + [ec.tamanho, h1, "EXCLUIDO" if ok else "NAO_EXCLUIDO", st, motivo])
        con.execute("INSERT INTO exclusao VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (agora(), go, "COPIA_FORA_DA_EAP", "", copia, fid, ec.etag, ec.tamanho, h1, reg[-1][8], st, motivo))
        con.commit()
    cab = ["em", "rodovia", "arquivo", "pasta_da_copia", "pasta_do_documento_mantido", "fileid", "tamanho",
           "quick_xor", "resultado", "http", "motivo"]
    escrever_csv(saida(c) / ("COPIAS_EXCLUIDAS_G2" + ("" if a.executar else "_SIMULACAO") + ".csv"), cab, reg)
    print(dict(Counter((r[1], r[8]) for r in reg)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
