"""
80_pastas - consolidacao executiva por pasta principal.

Somente leitura: le o SQLite ja montado. Nao chama a API, nao apaga,
nao move, nao decide.

Produz:
    output/ANALISE_6_PASTAS.md
    output/analise_6_pastas.csv
    data/consolidado.json      (numeros para os graficos e o resumo)

Cuidado central: distinguir sempre
    OCUPADO  = bytes dos arquivos que participam de algum grupo duplicado
    RECUPERAVEL = bytes das copias EXCEDENTES (mantendo 1 de cada grupo)
3 copias de 10 GB ocupam 30 GB; o recuperavel e 20 GB.

Uso:
    python scripts\\80_pastas.py
"""

from __future__ import annotations

import csv
import json
import re
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config_util import caminho_banco, exigir_fase_analise, ler_config  # noqa: E402

try:
    sys.stdout.reconfigure(errors="replace")
except AttributeError:
    pass

GiB = 1024**3
GB = 1000**3
BRT = timezone(timedelta(hours=-3))

PASTAS = ["1. Gestão do Contrato", "2. Compartilhado interno", "3. Desenvolvimento",
          "4. Produtos", "5. Compartilhado PARCEIRO", "6. Arquivos PARCEIRO"]

MARCA_JUR = ("mp-estado", "mp_estado", "resposta mp", "presta", "ministerio",
             "ministério", "auditoria", "tcu", "tce")
RE_REV = re.compile(r"\bREV\s*-?\s*\d+", re.I)

PESADAS = (".zip", ".rar", ".7z", ".dav", ".mp4", ".mov", ".avi",
           ".iso", ".bak", ".pbix")
COMPACTADOS = (".zip", ".rar", ".7z")
VIDEOS = (".mp4", ".mov", ".avi", ".dav", ".mkv", ".wmv")
PADROES_SUP = ["backup", "superado", "superada", "obsoleto", "antigo", "antiga",
               "old", "lixeira", "descartado", "versoes anteriores", "nao usar",
               "rascunho", "temp", "temporario", "temporário", "copia", "cópia"]


def ptbr(t: str) -> str:
    return t.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def num(n, c: int = 0) -> str:
    return ptbr(f"{n:,.{c}f}")


def gib(b) -> str:
    return f"{num((b or 0) / GiB, 1)} GiB"


def gb(b) -> str:
    return f"{num((b or 0) / GB, 1)} GB"


def pct(p, t, c: int = 1) -> str:
    return ptbr(f"{(p or 0) * 100 / t:.{c}f}") + "%" if t else "-"


# ----------------------------------------------------------------------

def classificar(cam: str, cam_origem: str) -> str:
    """Classe da copia EXCEDENTE, mutuamente exclusiva."""
    b = (cam or "").lower()
    if any(m in b for m in MARCA_JUR):
        return "juridico"
    if b.startswith("/6. arquivos parceiro"):
        return "pasta6"
    if b.startswith("/5. compartilhado parceiro"):
        return "pasta5"
    if RE_REV.search(cam or "") and RE_REV.search(cam_origem or ""):
        return "revisoes"
    if b.startswith("/3. desenvolvimento"):
        return "desenvolvimento"
    return "outras"


def carregar(con: sqlite3.Connection) -> dict:
    con.row_factory = None
    d: dict = {}
    d["quota"], d["lixeira"], d["raiz"] = con.execute(
        "SELECT MAX(quota_usada), MAX(quota_lixeira), MAX(raiz_tamanho)"
        " FROM biblioteca").fetchone()
    d["arquivos"], d["vivos"] = con.execute(
        "SELECT COUNT(*), COALESCE(SUM(tamanho),0) FROM item_nuvem"
        " WHERE eh_pasta=0 AND deletado=0").fetchone()
    d["pastas_total"] = con.execute(
        "SELECT COUNT(*) FROM item_nuvem WHERE eh_pasta=1 AND deletado=0").fetchone()[0]

    # ---- grupos duplicados, com a copia de referencia (mais antiga)
    linhas = con.execute("""
        SELECT quick_xor_hash, item_id, caminho, tamanho, nivel1, nivel2,
               criado_em, modificado_em, criado_por
          FROM item_nuvem
         WHERE eh_pasta=0 AND deletado=0 AND quick_xor_hash IN (
            SELECT quick_xor_hash FROM item_nuvem
             WHERE eh_pasta=0 AND deletado=0 AND quick_xor_hash IS NOT NULL
               AND tamanho>=102400
             GROUP BY quick_xor_hash HAVING COUNT(*)>1)
         ORDER BY quick_xor_hash, COALESCE(criado_em,'9999')""").fetchall()
    por_hash: dict = {}
    for r in linhas:
        por_hash.setdefault(r[0], []).append(r)
    d["por_hash"] = por_hash

    # excedentes: item_id -> (classe, tamanho, caminho, nivel1)
    exc: dict = {}
    ocupa_n1: dict = {}
    classe_tot: dict = {}
    for h, cop in por_hash.items():
        origem = cop[0][2]
        for pos, c in enumerate(cop):
            ocupa_n1[c[4]] = ocupa_n1.get(c[4], 0) + (c[3] or 0)
            if pos == 0:
                continue
            cl = classificar(c[2], origem)
            exc[c[1]] = (cl, c[3] or 0, c[2], c[4])
            classe_tot[cl] = classe_tot.get(cl, 0) + (c[3] or 0)
    d["exc"] = exc
    d["ocupa_n1"] = ocupa_n1
    d["classe_tot"] = classe_tot
    d["dup_grupos"] = len(por_hash)
    d["dup_arquivos"] = sum(len(v) for v in por_hash.values())
    d["dup_ocupado"] = sum(sum(x[3] or 0 for x in v) for v in por_hash.values())
    d["dup_recuperavel"] = sum(classe_tot.values())

    # ---- versoes por pasta de 1o nivel (so o que foi medido)
    d["ver_n1"] = dict(con.execute("""
        SELECT i.nivel1, SUM(v.bytes_antigas) FROM versao_resumo v
          JOIN item_nuvem i ON i.drive_id=v.drive_id AND i.item_id=v.item_id
         WHERE v.erro IS NULL AND v.bytes_antigas>0
         GROUP BY i.nivel1""").fetchall())
    faixas = con.execute("""
        SELECT estrato, arquivos_total, bytes_total, arquivos_medidos,
               bytes_medidos, bytes_antigas_medidos FROM versao_estrato
         ORDER BY estrato""").fetchall()
    d["faixas"] = faixas
    tot = exato = 0
    for est, at, bt, am, bm, ant in faixas:
        if not am:
            continue
        completo = am >= at
        tot += ant if completo else ((ant / bm * bt) if bm else 0)
        if completo:
            exato += ant
    d["ver_total"], d["ver_exato"] = tot, exato
    d["nao_id"] = d["quota"] - d["vivos"] - tot - (d["lixeira"] or 0)
    return d


def por_pasta(con: sqlite3.Connection, d: dict) -> list[dict]:
    saida = []
    for nome in PASTAS:
        arq, bts = con.execute(
            "SELECT COUNT(*), COALESCE(SUM(tamanho),0) FROM item_nuvem"
            " WHERE eh_pasta=0 AND deletado=0 AND nivel1=?", (nome,)).fetchone()
        subp = con.execute(
            "SELECT COUNT(*) FROM item_nuvem WHERE eh_pasta=1 AND deletado=0"
            " AND nivel1=? AND profundidade>1", (nome,)).fetchone()[0]
        dup_arq, dup_oc = con.execute("""
            SELECT COUNT(*), COALESCE(SUM(tamanho),0) FROM item_nuvem
             WHERE eh_pasta=0 AND deletado=0 AND nivel1=?
               AND quick_xor_hash IN (SELECT quick_xor_hash FROM item_nuvem
                    WHERE eh_pasta=0 AND deletado=0 AND quick_xor_hash IS NOT NULL
                      AND tamanho>=102400
                    GROUP BY quick_xor_hash HAVING COUNT(*)>1)""",
            (nome,)).fetchone()
        rec = sum(v[1] for v in d["exc"].values() if v[3] == nome)
        pes = con.execute(
            f"SELECT COUNT(*), COALESCE(SUM(tamanho),0) FROM item_nuvem"
            f" WHERE eh_pasta=0 AND deletado=0 AND nivel1=? AND extensao IN"
            f" ({','.join('?'*len(PESADAS))})", (nome,) + PESADAS).fetchone()
        comp = con.execute(
            f"SELECT COUNT(*), COALESCE(SUM(tamanho),0) FROM item_nuvem"
            f" WHERE eh_pasta=0 AND deletado=0 AND nivel1=? AND extensao IN"
            f" ({','.join('?'*len(COMPACTADOS))})", (nome,) + COMPACTADOS).fetchone()
        vid = con.execute(
            f"SELECT COUNT(*), COALESCE(SUM(tamanho),0) FROM item_nuvem"
            f" WHERE eh_pasta=0 AND deletado=0 AND nivel1=? AND extensao IN"
            f" ({','.join('?'*len(VIDEOS))})", (nome,) + VIDEOS).fetchone()
        cond = " OR ".join(["LOWER(caminho) LIKE ?"] * len(PADROES_SUP))
        sup = con.execute(
            f"SELECT COUNT(*), COALESCE(SUM(tamanho),0) FROM item_nuvem"
            f" WHERE eh_pasta=0 AND deletado=0 AND nivel1=? AND ({cond})",
            (nome,) + tuple(f"%{p}%" for p in PADROES_SUP)).fetchone()
        top_arq = con.execute(
            "SELECT caminho, tamanho, item_id FROM item_nuvem"
            " WHERE eh_pasta=0 AND deletado=0 AND nivel1=?"
            " ORDER BY tamanho DESC LIMIT 10", (nome,)).fetchall()
        saida.append({
            "nome": nome, "arquivos": arq, "subpastas": subp, "bytes": bts,
            "dup_arquivos": dup_arq, "dup_ocupado": dup_oc, "recuperavel": rec,
            "unicos_arq": arq - dup_arq, "unicos_bytes": bts - dup_oc,
            "versoes": d["ver_n1"].get(nome, 0),
            "pesados": pes, "compactados": comp, "videos": vid, "superado": sup,
            "top_arq": top_arq,
        })
    return saida


def subpastas(con: sqlite3.Connection, d: dict, nome: str, limite: int = 15) -> list:
    """Consumo por subpasta de 1o nivel abaixo da pasta principal (= nivel2)."""
    linhas = con.execute("""
        SELECT nivel2, COUNT(*), SUM(tamanho) FROM item_nuvem
         WHERE eh_pasta=0 AND deletado=0 AND nivel1=? AND nivel2 IS NOT NULL
         GROUP BY nivel2 ORDER BY SUM(tamanho) DESC LIMIT ?""",
        (nome, limite)).fetchall()
    saida = []
    for n2, qtd, bts in linhas:
        dupo = con.execute("""
            SELECT COALESCE(SUM(tamanho),0) FROM item_nuvem
             WHERE eh_pasta=0 AND deletado=0 AND nivel1=? AND nivel2=?
               AND quick_xor_hash IN (SELECT quick_xor_hash FROM item_nuvem
                    WHERE eh_pasta=0 AND deletado=0 AND quick_xor_hash IS NOT NULL
                      AND tamanho>=102400
                    GROUP BY quick_xor_hash HAVING COUNT(*)>1)""",
            (nome, n2)).fetchone()[0]
        rec = con.execute("""
            SELECT COALESCE(SUM(v.bytes_antigas),0) FROM versao_resumo v
              JOIN item_nuvem i ON i.drive_id=v.drive_id AND i.item_id=v.item_id
             WHERE v.erro IS NULL AND i.nivel1=? AND i.nivel2=?""",
            (nome, n2)).fetchone()[0]
        exc = sum(x[1] for x in d["exc"].values()
                  if x[3] == nome and f"/{nome}/{n2}/" in (x[2] or "") + "/")
        saida.append((n2, qtd, bts, dupo, exc, rec))
    return saida


def intersecoes(con: sqlite3.Connection, d: dict) -> list:
    cond_sup = "(" + " OR ".join(["LOWER(caminho) LIKE ?"] * len(PADROES_SUP)) + ")"
    args_sup = [f"%{p}%" for p in PADROES_SUP]
    cond_pes = f"extensao IN ({','.join('?'*len(PESADAS))})"

    def q(where, args):
        return con.execute(
            f"SELECT COUNT(*), COALESCE(SUM(tamanho),0) FROM item_nuvem"
            f" WHERE eh_pasta=0 AND deletado=0 AND {where}", args).fetchone()

    sup_ids = set(r[0] for r in con.execute(
        f"SELECT item_id FROM item_nuvem WHERE eh_pasta=0 AND deletado=0"
        f" AND {cond_sup}", args_sup))
    pes_ids = set(r[0] for r in con.execute(
        f"SELECT item_id FROM item_nuvem WHERE eh_pasta=0 AND deletado=0"
        f" AND {cond_pes}", PESADAS))
    tam = dict(con.execute(
        "SELECT item_id, tamanho FROM item_nuvem WHERE eh_pasta=0 AND deletado=0"))

    exc_jur = {k for k, v in d["exc"].items() if v[0] == "juridico"}
    exc_int = {k for k, v in d["exc"].items()
               if v[0] in ("revisoes", "desenvolvimento", "outras")}
    exc_p5 = {k for k, v in d["exc"].items() if v[0] == "pasta5"}
    exc_p6 = {k for k, v in d["exc"].items() if v[0] == "pasta6"}
    p5 = set(r[0] for r in con.execute(
        "SELECT item_id FROM item_nuvem WHERE eh_pasta=0 AND deletado=0"
        " AND nivel1='5. Compartilhado PARCEIRO'"))
    p6 = set(r[0] for r in con.execute(
        "SELECT item_id FROM item_nuvem WHERE eh_pasta=0 AND deletado=0"
        " AND nivel1='6. Arquivos PARCEIRO'"))

    conj = {"Dup. juridicas (exc.)": exc_jur, "Dup. internas (exc.)": exc_int,
            "Dup. pasta 5 (exc.)": exc_p5, "Dup. pasta 6 (exc.)": exc_p6,
            "Material superado": sup_ids, "Arquivos pesados": pes_ids,
            "Pasta 5 (tudo)": p5, "Pasta 6 (tudo)": p6}
    nomes = list(conj)
    matriz = []
    for a in nomes:
        linha = []
        for b in nomes:
            inter = conj[a] & conj[b]
            linha.append(sum(tam.get(i, 0) for i in inter))
        matriz.append((a, len(conj[a]), sum(tam.get(i, 0) for i in conj[a]), linha))
    return nomes, matriz


def main() -> int:
    cfg = ler_config()
    exigir_fase_analise(cfg)
    saida = Path(cfg["saida"])
    con = sqlite3.connect(caminho_banco(cfg))
    agora = datetime.now(BRT)
    try:
        d = carregar(con)
        pastas = por_pasta(con, d)
        Q, V = d["quota"], d["vivos"]

        # ---------- CSV ----------
        with (saida / "analise_6_pastas.csv").open("w", encoding="utf-8-sig",
                                                   newline="") as fh:
            w = csv.writer(fh, delimiter=";")
            w.writerow(["pasta", "arquivos", "subpastas", "bytes", "gb_decimal",
                        "gib", "pct_arquivos_vivos", "pct_quota",
                        "arquivos_duplicados", "pct_arquivos_duplicados",
                        "bytes_ocupados_por_duplicatas",
                        "bytes_recuperaveis_excedentes",
                        "pct_da_pasta_recuperavel", "arquivos_unicos",
                        "bytes_unicos", "versoes_medidas_bytes",
                        "pesados_arq", "pesados_bytes", "zip_rar_7z_arq",
                        "zip_rar_7z_bytes", "videos_arq", "videos_bytes",
                        "superado_arq", "superado_bytes"])
            for p in pastas:
                w.writerow([
                    p["nome"], p["arquivos"], p["subpastas"], p["bytes"],
                    ptbr(f"{p['bytes']/GB:.2f}"), ptbr(f"{p['bytes']/GiB:.2f}"),
                    ptbr(f"{p['bytes']*100/V:.2f}"), ptbr(f"{p['bytes']*100/Q:.2f}"),
                    p["dup_arquivos"],
                    ptbr(f"{p['dup_arquivos']*100/max(p['arquivos'],1):.1f}"),
                    p["dup_ocupado"], p["recuperavel"],
                    ptbr(f"{p['recuperavel']*100/max(p['bytes'],1):.1f}"),
                    p["unicos_arq"], p["unicos_bytes"], p["versoes"],
                    p["pesados"][0], p["pesados"][1], p["compactados"][0],
                    p["compactados"][1], p["videos"][0], p["videos"][1],
                    p["superado"][0], p["superado"][1]])

        # ---------- MD ----------
        L: list[str] = []
        ad = L.append
        ad("# Análise das 6 pastas principais — biblioteca Programa")
        ad("")
        ad(f"**POSIÇÃO DO SHAREPOINT EM: {agora:%d/%m/%Y %H:%M}** (UTC−3)")
        ad("")
        ad("> Somente leitura. Nada foi excluído, movido ou alterado.")
        ad("")
        ad("**Distinção que percorre todo o documento:**")
        ad("")
        ad("| Termo | Significado |")
        ad("|---|---|")
        ad("| **Ocupado por duplicatas** | bytes de *todos* os arquivos que "
           "participam de algum grupo duplicado |")
        ad("| **Recuperável** | bytes das cópias **excedentes** — mantendo 1 de "
           "cada grupo |")
        ad("")
        ad("3 cópias idênticas de 10 GB **ocupam 30 GB**; o **recuperável é 20 GB**.")
        ad("")
        ad("---")
        ad("")
        ad("## Tabela-mestra")
        ad("")
        ad("| Pasta | Arquivos | Tamanho | % vivos | % quota | Duplicados | "
           "Recuperável | Principal causa |")
        ad("|---|---:|---:|---:|---:|---:|---:|---|")
        causas = {
            "1. Gestão do Contrato": "volume irrelevante",
            "2. Compartilhado interno": "respostas a órgão de controle (cópias)",
            "3. Desenvolvimento": "projetos + anexos repetidos entre revisões",
            "4. Produtos": "entregas mensais",
            "5. Compartilhado PARCEIRO": "área de transferência ao PARCEIRO",
            "6. Arquivos PARCEIRO": "cópia de acervo externo ao Programa",
        }
        for p in pastas:
            ad(f"| `{p['nome']}` | {num(p['arquivos'])} | **{gib(p['bytes'])}** | "
               f"{pct(p['bytes'], V)} | {pct(p['bytes'], Q)} | "
               f"{num(p['dup_arquivos'])} | {gib(p['recuperavel'])} | "
               f"{causas[p['nome']]} |")
        ad(f"| **TOTAL** | **{num(sum(p['arquivos'] for p in pastas))}** | "
           f"**{gib(sum(p['bytes'] for p in pastas))}** | | | "
           f"**{num(sum(p['dup_arquivos'] for p in pastas))}** | "
           f"**{gib(sum(p['recuperavel'] for p in pastas))}** | |")
        ad("")
        ad("## Composição de cada pasta")
        ad("")
        ad("| Pasta | Tamanho | % único | % duplicado | % versões | Possível ação | Quem valida |")
        ad("|---|---:|---:|---:|---:|---|---|")
        acoes = {
            "1. Gestão do Contrato": ("nenhuma — volume desprezível", "—"),
            "2. Compartilhado interno": ("depende de parecer jurídico", "Jurídico"),
            "3. Desenvolvimento": ("rever anexos repetidos entre revisões",
                                   "Coordenação de engenharia"),
            "4. Produtos": ("rever cópias das entregas mensais", "Coordenação"),
            "5. Compartilhado PARCEIRO": ("confirmar recebimento pelo PARCEIRO",
                                      "PARCEIRO / Eliseu + gestão"),
            "6. Arquivos PARCEIRO": ("confirmar se a cópia precisa permanecer",
                                 "PARCEIRO + gestão do contrato"),
        }
        for p in pastas:
            b = p["bytes"] or 1
            ad(f"| `{p['nome']}` | {gib(b)} | {pct(p['unicos_bytes'], b)} | "
               f"{pct(p['dup_ocupado'], b)} | {pct(p['versoes'], b)} | "
               f"{acoes[p['nome']][0]} | {acoes[p['nome']][1]} |")
        ad("")
        ad("> *% versões* usa apenas o histórico **efetivamente medido** para "
           "arquivos daquela pasta; é piso, não teto.")
        ad("")

        # detalhe por pasta
        for p in pastas:
            ad("---")
            ad("")
            ad(f"## {p['nome']}")
            ad("")
            ad("| | |")
            ad("|---|---:|")
            ad(f"| Arquivos | {num(p['arquivos'])} |")
            ad(f"| Subpastas | {num(p['subpastas'])} |")
            ad(f"| Tamanho | **{gib(p['bytes'])}** / {gb(p['bytes'])} / "
               f"{num(p['bytes'])} bytes |")
            ad(f"| % dos arquivos vivos | {pct(p['bytes'], V)} |")
            ad(f"| % da quota | {pct(p['bytes'], Q)} |")
            ad(f"| Arquivos em grupo duplicado | {num(p['dup_arquivos'])} "
               f"({pct(p['dup_arquivos'], max(p['arquivos'],1))} dos arquivos) |")
            ad(f"| **Ocupado** por esses arquivos | {gib(p['dup_ocupado'])} |")
            ad(f"| **Recuperável** (cópias excedentes) | **{gib(p['recuperavel'])}** |")
            ad(f"| % da pasta recuperável por duplicação | "
               f"{pct(p['recuperavel'], max(p['bytes'],1))} |")
            ad(f"| Arquivos únicos | {num(p['unicos_arq'])} — {gib(p['unicos_bytes'])} |")
            ad(f"| Histórico de versões medido | {gib(p['versoes'])} |")
            ad(f"| Arquivos pesados | {num(p['pesados'][0])} — {gib(p['pesados'][1])} |")
            ad(f"| ZIP/RAR/7Z | {num(p['compactados'][0])} — {gib(p['compactados'][1])} |")
            ad(f"| Vídeos | {num(p['videos'][0])} — {gib(p['videos'][1])} |")
            ad(f"| Marcado backup/antigo/superado/cópia/temp | "
               f"{num(p['superado'][0])} — {gib(p['superado'][1])} |")
            ad("")
            subs = subpastas(con, d, p["nome"], 15)
            if subs:
                ad("### TOP 15 subpastas por armazenamento")
                ad("")
                ad("| Subpasta | Arquivos | Tamanho | % da pasta | % do SharePoint | "
                   "Espaço duplicado | Recuperável | Versões |")
                ad("|---|---:|---:|---:|---:|---:|---:|---:|")
                for n2, qtd, bts, dupo, exc, ver in subs:
                    ad(f"| `{n2}` | {num(qtd)} | {gib(bts)} | "
                       f"{pct(bts, max(p['bytes'],1))} | {pct(bts, Q, 2)} | "
                       f"{gib(dupo)} | {gib(exc)} | {gib(ver)} |")
                ad("")
            ad("### TOP 10 maiores arquivos")
            ad("")
            ad("| Arquivo | Tamanho |")
            ad("|---|---:|")
            for cam, bts, iid in p["top_arq"]:
                curto = cam if len(cam) < 86 else "…" + cam[-83:]
                ad(f"| `{curto}` | {gib(bts)} |")
            ad("")
            grupos_p = sorted(
                [(h, cop) for h, cop in d["por_hash"].items()
                 if any(c[4] == p["nome"] for c in cop[1:])],
                key=lambda kv: -(kv[1][0][3] or 0) * (len(kv[1]) - 1))[:10]
            if grupos_p:
                ad("### TOP 10 grupos duplicados com cópia nesta pasta")
                ad("")
                ad("| Arquivo | Cópias | Unitário | Recuperável |")
                ad("|---|---:|---:|---:|")
                for h, cop in grupos_p:
                    unit = cop[0][3] or 0
                    ad(f"| `{cop[0][2].split('/')[-1]}` | {len(cop)} | {gib(unit)} | "
                       f"{gib(unit*(len(cop)-1))} |")
                ad("")

        (saida / "ANALISE_6_PASTAS.md").write_text("\n".join(L), encoding="utf-8")

        # ---------- JSON consolidado ----------
        cons = {
            "timestamp": agora.isoformat(),
            "quota": Q, "vivos": V, "lixeira": d["lixeira"],
            "arquivos": d["arquivos"], "pastas_total": d["pastas_total"],
            "ver_total": d["ver_total"], "ver_exato": d["ver_exato"],
            "nao_id": d["nao_id"], "dup_grupos": d["dup_grupos"],
            "dup_arquivos": d["dup_arquivos"], "dup_ocupado": d["dup_ocupado"],
            "dup_recuperavel": d["dup_recuperavel"],
            "classes": d["classe_tot"],
            "pastas": [{k: v for k, v in p.items() if k != "top_arq"} for p in pastas],
        }
        Path(cfg["banco"]).parent.joinpath("consolidado.json").write_text(
            json.dumps(cons, ensure_ascii=False, indent=2), encoding="utf-8")

        print("=" * 70)
        print(f"ANALISE DAS 6 PASTAS — posicao {agora:%d/%m/%Y %H:%M}")
        print("=" * 70)
        for p in pastas:
            print(f"  {p['nome']:<26} {gib(p['bytes']):>11}  {pct(p['bytes'],V):>6} vivos"
                  f"  dup {gib(p['recuperavel']):>10}")
        print()
        print("  classes de duplicata (mutuamente exclusivas):")
        for k, v in sorted(d["classe_tot"].items(), key=lambda x: -x[1]):
            print(f"    {k:<18} {gib(v)}")
        print(f"    {'TOTAL':<18} {gib(sum(d['classe_tot'].values()))}")
        print()
        print(f"  {saida/'ANALISE_6_PASTAS.md'}")
        print(f"  {saida/'analise_6_pastas.csv'}")
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())
