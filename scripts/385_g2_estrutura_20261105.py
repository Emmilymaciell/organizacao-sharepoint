"""
385_g2_estrutura_20261105 - correcoes estruturais NAO destrutivas autorizadas pela Emmily em 05/11/2026 (prompt "AUDITE E
CORRIJA AS INCONSISTENCIAS ESTRUTURAIS COMPROVADAS"): so MOVE (Overwrite: F), nenhuma exclusao de documento.

  topo102a  RD-102A: arquivos da pasta solta `01. Projetos/02. Executivo/ESTUDOS TOPOGRAFICOS/REV02 - 15 06 26` (fora da
            disciplina, nota 02) cujo conteudo e IDENTICO (QuickXor, plano JA_EXISTIA_EM_OUTRO_DESTINO) a um item da REV 2
            aprovada no SharePoint -> movidos para o destino que a EAP/plano define em
            `01. FASE DE ESTUDOS/ESTUDOS TOPOGRAFICOS/REV02 - 15 06 26/...`. Destino ocupado, conteudo diferente ou sem
            correspondencia: NAO move (fica para decisao).
  sst101    RD-101: subpastas de SST de julho/2026 (CIPA, DDS - CAMPANHAS, DOSSIE, PROGRAMAS) que estao direto no mes ->
            `2026-07 - Julho/02. Documentacao SST/` (no da EAP, como nos demais meses e rodovias).

Antes de cada MOVE: fileid/etag da foto conferidos no PARCEIRO (alteracao de terceiro = nao move); depois: fileid no destino e
origem ausente. Registro: tabela movimentacao + output/migracao_parceiro_g2/ESTRUTURA_20261105_<ACAO>[_SIMULACAO].csv.
Uso: python scripts/385_g2_estrutura_20261105.py --acao topo102a|sst101 [--executar] [--foto N]
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from collections import Counter
from pathlib import Path, PurePosixPath

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "scripts"))

from migra_g2.base import agora, cfg, conectar, escrever_csv, saida  # noqa: E402
from migra_g2.trava import exigir_fase_execucao  # noqa: E402

_spec = importlib.util.spec_from_file_location("d384", RAIZ / "scripts" / "384_g2_decisoes_20261104.py")
d384 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(d384)
nfc = d384.nfc
INSTR = "correcao estrutural autorizada pela Emmily 05/11/2026"


def topo102a(o, foto):
    go = "RD-102A"
    raiz = nfc(o.c["destino"]["arvores"][go])
    leg = raiz + "/01. Projetos/02. Executivo/ESTUDOS TOPOGRÁFICOS/"
    atual = {nfc(c): (f, e) for c, f, e in o.con.execute(
        "SELECT caminho, fileid, etag FROM parceiro_item WHERE foto_id=? AND eh_pasta=0", (foto,))}
    for cor, dest in o.con.execute("SELECT correspondente, destino_rel FROM plano WHERE rodovia=? AND "
                                   "classificacao='JA_EXISTIA_EM_OUTRO_DESTINO' AND correspondente LIKE ?", (go, leg + "%")):
        cor, dest = nfc(cor), nfc(dest)
        base = [agora(), go, cor[len(raiz) + 1:], dest[len(raiz) + 1:]]
        if cor not in atual:
            o.reg.append(base + ["JA_NAO_ESTA_NA_ORIGEM", None])
            continue
        if not dest.startswith(raiz + "/01. Projetos/02. Executivo/01. FASE DE ESTUDOS/ESTUDOS TOPOGRÁFICOS/REV02 - 15 06 26/"):
            o.reg.append(base + ["DESTINO_FORA_DA_REV_APROVADA", None])
            continue
        if dest in atual:
            o.reg.append(base + ["DESTINO_OCUPADO_NAO_MOVIDO", None])
            continue
        if not o.a.executar:
            o.reg.append(base + ["SIMULADO", None])
            continue
        fid = atual[cor][0]
        o.mkcol_cadeia(dest.rsplit("/", 1)[0], raiz)
        r, st = o.mover(cor, dest, False, fid)
        o.reg.append(base + [r, st])
        o.mov_db(go, PurePosixPath(cor).name, str(PurePosixPath(cor[len(raiz) + 1:]).parent),
                 str(PurePosixPath(dest[len(raiz) + 1:]).parent), fid, 0, r, st,
                 f"pasta solta no Executivo -> disciplina 01. FASE DE ESTUDOS (nota 02); conteudo identico (QuickXor) a REV 2 "
                 f"aprovada; {INSTR}")
    return ["em", "rodovia", "de", "para", "resultado", "http"]


def sst101(o, foto):
    go = "RD-101"
    raiz = nfc(o.c["destino"]["arvores"][go])
    mes = raiz + "/08. Segurança do Trabalho/2026-07 - Julho"
    alvo = mes + "/02. Documentação SST"
    subs = [(nfc(c), f) for c, f in o.con.execute("SELECT caminho, fileid FROM parceiro_item WHERE foto_id=? AND eh_pasta=1",
                                                  (foto,))
            if nfc(c).startswith(mes + "/") and "/" not in nfc(c)[len(mes) + 1:]
            and nfc(c).rsplit("/", 1)[1] in ("CIPA", "DDS - CAMPANHAS", "DOSSIÊ", "PROGRAMAS")]
    for cam, fid in sorted(subs):
        dst = alvo + "/" + cam.rsplit("/", 1)[1]
        base = [agora(), go, cam[len(raiz) + 1:], dst[len(raiz) + 1:]]
        if not o.a.executar:
            o.reg.append(base + ["SIMULADO", None])
            continue
        o.mkcol_cadeia(alvo, raiz)
        r, st = o.mover(cam, dst, True, fid)
        o.reg.append(base + [r, st])
        o.mov_db(go, cam.rsplit("/", 1)[1], mes[len(raiz) + 1:], alvo[len(raiz) + 1:], fid, 0,
                 "PASTA_MOVIDA" if r == "MOVIDO" else r, st, f"SST: subpasta para o no '02. Documentação SST' da EAP; {INSTR}")
    return ["em", "rodovia", "de", "para", "resultado", "http"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--acao", required=True, choices=["topo102a", "sst101"])
    ap.add_argument("--executar", action="store_true")
    ap.add_argument("--foto", type=int)
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    c = cfg()
    if a.executar:
        exigir_fase_execucao(c, "385_g2_estrutura_20261105")
    con = conectar()
    con.execute("PRAGMA busy_timeout = 900000")
    foto = a.foto or con.execute("SELECT MAX(id) FROM parceiro_foto WHERE fim IS NOT NULL").fetchone()[0]
    o = d384.Ops(a, c, con)
    cab = {"topo102a": topo102a, "sst101": sst101}[a.acao](o, foto)
    nome = f"ESTRUTURA_20261105_{a.acao.upper()}{'' if a.executar else '_SIMULACAO'}.csv"
    escrever_csv(saida(c) / nome, cab, o.reg)
    print(f"foto {foto} | {a.acao}: {len(o.reg)} |", dict(Counter(r[4] for r in o.reg)), "->", nome)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
