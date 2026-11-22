"""
381_g2_pastas_vazias - remove as pastas VAZIAS (nenhum arquivo em nenhum nivel abaixo) das arvores 04. Engenharia
e lista as pastas "duplicadas" que ainda tem arquivos. Instrucao da Emmily, 30/10/2026 (~14:55): "VERIFIQUE ESSAS
PASTAS VAZIAS E DUPLICADAS ... E AJUSTA" (pastas antigas dos estagiarios esvaziadas pela reorganizacao 379, ex.:
'2025-10-01 a 2025-09-25 - 1º Relatório Semanal', e pastas de mes que ficaram vazias com o corte de BM).

Nunca remove: a raiz 04. Engenharia e os nos FIXOS da EAP (os 39 do manifesto sem [modelo], ex.: RPFO, DUP,
Eventograma e tudo abaixo dele, Documentacao Supervisora) nem qualquer pasta com arquivo dentro.
Com --executar, cada pasta candidata e relida no PARCEIRO (PROPFIND Depth 1, recursivo): so sai se continuar sem nenhum
arquivo. O Nextcloud nao aceita DELETE condicional em pasta: a conferencia e feita imediatamente antes. Removida a
pasta mais alta de cada ramo vazio (as subpastas vazias vao junto). Vai para a lixeira do Nextcloud.

Duplicadas (so lista): pastas irmas com as mesmas datas (em qualquer ordem) ou o mesmo numero de relatorio/NC/mes.
Saida: output/migracao_parceiro_g2/PASTAS_VAZIAS_REMOVIDAS_G2.csv, PASTAS_DUPLICADAS_G2.csv; tabela exclusao.
Uso:   python scripts/381_g2_pastas_vazias.py [--executar] [--foto N]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "scripts"))

from migra_g2.base import agora, cfg, conectar, escrever_csv, num, saida  # noqa: E402
from migra_g2.trava import exigir_fase_execucao  # noqa: E402


DISCIPLINAS_EXECUTIVO = {"01. FASE DE ESTUDOS", "02. VIARIO PRINC", "03. VIARIO SEC", "04. OAE", "04. VOLUMES FINAIS",
                         "05. VOLUMES FINAIS"}


def nfc(s):
    return unicodedata.normalize("NFC", s or "")


def fixos(c) -> tuple[set[str], list]:
    """Nos fixos da EAP (sem [modelo]) e, para os nos com nome fixo abaixo de um [modelo] (ex.: [mes]/02.
    Documentacao Supervisora), a expressao que reconhece cada instancia: tambem nunca removidos."""
    man = json.loads((RAIZ / c["eap"]["manifesto"]).read_text(encoding="utf-8"))
    out, padroes = set(), []
    for r in man["road_trees"]["RD-101"]:
        rel = nfc(r["relative_parceiro_path"]).split("/04. Engenharia", 1)[1].lstrip("/")
        if not rel:
            continue
        if "[" not in rel:
            out.add(rel)
        elif not re.fullmatch(r"\[.*\]", rel.rsplit("/", 1)[-1]):
            partes = [r"[^/]+" if re.fullmatch(r"\[.*\]", x) else re.escape(x) for x in rel.split("/")]
            padroes.append(re.compile("^" + "/".join(partes) + "$"))
    return out, padroes


def assinatura(nome: str) -> str | None:
    datas = sorted(re.findall(r"\d{4}-\d{2}-\d{2}", nome))
    if len(datas) >= 2:
        return "datas:" + "|".join(datas)
    m = re.search(r"(\d+)\s*º\s*Relat", nome) or re.match(r"(NC-\d+)", nome)
    if m:
        return "num:" + m.group(1)
    m = re.match(r"(\d{4}-\d{2}) - ", nome)
    return "mes:" + m.group(1) if m else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--executar", action="store_true")
    ap.add_argument("--foto", type=int)
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    c = cfg()
    if a.executar:
        exigir_fase_execucao(c, '381_g2_pastas_vazias')   # PD-18
    con = conectar()
    con.execute("PRAGMA busy_timeout = 900000")
    foto = a.foto or con.execute("SELECT MAX(id) FROM parceiro_foto WHERE fim IS NOT NULL").fetchone()[0]
    arvores = {go: nfc(r) for go, r in c["destino"]["arvores"].items()}
    fx, padroes = fixos(c)
    pastas, arqs = {}, Counter()
    for cam, eh, go in con.execute("SELECT caminho, eh_pasta, rodovia FROM parceiro_item WHERE foto_id=?", (foto,)):
        cam = nfc(cam)
        g = next((x for x, r in arvores.items() if cam.startswith(r + "/")), None)
        if not g:
            continue
        rel = cam[len(arvores[g]) + 1:]
        if eh:
            pastas[(g, rel)] = cam
        else:                          # conta o arquivo em todas as pastas acima dele
            partes = rel.split("/")[:-1]
            for k in range(1, len(partes) + 1):
                arqs[(g, "/".join(partes[:k]))] += 1

    def protegida(rel: str) -> bool:
        if rel.startswith("01. Projetos/02. Executivo/") and rel.count("/") == 2 and \
                rel.rsplit("/", 1)[1] in DISCIPLINAS_EXECUTIVO:          # nota 02: estrutura do Executivo
            return True
        return rel in fx or rel.startswith("09. Eventograma") or any(p.match(rel) for p in padroes)

    vazias = {k for k in pastas if arqs[k] == 0 and not protegida(k[1])}
    # so a mais alta de cada ramo vazio; e nenhuma que contenha no fixo da EAP abaixo dela
    topo = []
    for g, rel in sorted(vazias, key=lambda k: (k[0], k[1].count("/"), k[1])):
        pai = rel.rsplit("/", 1)[0] if "/" in rel else ""
        if (g, pai) in vazias:
            continue
        if any(f.startswith(rel + "/") for f in fx):
            continue
        topo.append((g, rel))
    print(f"foto {foto}: {num(len(vazias))} pastas vazias removiveis; {num(len(topo))} remocoes (a mais alta de cada ramo)")

    reg = []
    nc = None
    if a.executar:
        from migra_g2.credencial import parceiro
        from migra_g2.governador import baixar_prioridade_propria
        baixar_prioridade_propria()
        nc = parceiro(c)

    def tem_arquivo(cam: str) -> bool | None:
        lst = nc.listar(cam, "1")
        if lst is None:
            return None
        for e in lst[1:]:
            if not e.eh_pasta:
                return True
            if tem_arquivo(e.caminho):
                return True
        return False

    for g, rel in topo:
        cam = pastas[(g, rel)]
        if not a.executar:
            reg.append((agora(), g, rel, "SIMULADO", None, "pasta vazia (nenhum arquivo abaixo)"))
            continue
        t = tem_arquivo(cam)
        if t is None:
            reg.append((agora(), g, rel, "JA_AUSENTE", None, "nao existe mais"))
            continue
        if t:
            reg.append((agora(), g, rel, "MANTIDA_TEM_ARQUIVO", None, "recebeu arquivo depois da foto: mantida"))
            continue
        st, _ = nc._req_ret("DELETE", nc.url_arquivo(cam))
        ok = st in (200, 204) and nc.estado(cam) is None
        reg.append((agora(), g, rel, "PASTA_EXCLUIDA" if ok else "NAO_EXCLUIDA", st, "pasta vazia (nenhum arquivo "
                    "abaixo); instrucao da Emmily 30/10/2026"))
        con.execute("INSERT INTO exclusao VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (reg[-1][0], g, "PASTA_VAZIA", "", cam, None, None, 0, None, reg[-1][3], st, reg[-1][5]))
        con.commit()

    # duplicadas: irmas com a mesma assinatura (datas, numero ou mes) que ainda tem arquivo
    grupos = defaultdict(list)
    removidas = {(r[1], r[2]) for r in reg if r[3] in ("PASTA_EXCLUIDA", "SIMULADO")}
    for (g, rel), cam in pastas.items():
        if any(rel == x or rel.startswith(x + "/") for gg, x in removidas if gg == g):
            continue
        nome = rel.rsplit("/", 1)[-1]
        s = assinatura(nome)
        if s:
            grupos[(g, rel.rsplit("/", 1)[0] if "/" in rel else "", s)].append((nome, arqs[(g, rel)]))
    dup = [(g, pai, s, " | ".join(f"{n} ({q} arq.)" for n, q in sorted(v)))
           for (g, pai, s), v in sorted(grupos.items()) if len(v) > 1]
    out = saida(c)
    suf = "" if a.executar else "_SIMULACAO"
    escrever_csv(out / f"PASTAS_VAZIAS_REMOVIDAS_G2{suf}.csv", ["em", "rodovia", "pasta", "resultado", "http", "motivo"],
                 reg)
    escrever_csv(out / f"PASTAS_DUPLICADAS_G2{suf}.csv", ["rodovia", "pasta_mae", "criterio", "pastas_irmas"], dup)
    print(dict(Counter((r[1], r[3]) for r in reg)), "| grupos duplicados:", len(dup))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
