"""
40_versoes - E3(b): quanto da cota e HISTORICO DE VERSOES.

O `size` que o Graph devolve num arquivo e so a versao corrente. O
SharePoint, porem, cobra na cota **todas** as versoes. E por isso que a
cota do site (1.384 GB) e maior que a soma dos arquivos vivos.

Este script mede isso com `GET /drives/{d}/items/{i}/versions`.

Consultar 139 mil arquivos levaria horas. A medicao e ESTRATIFICADA:
  - arquivos grandes: medidos 100% (concentram quase todo o espaco)
  - faixas menores: amostra aleatoria, e o resultado e extrapolado
    pela razao medida na propria faixa.

O relatorio separa o que foi MEDIDO do que foi ESTIMADO. Nenhuma versao e
apagada aqui — so GET.

Uso:
    python scripts\\40_versoes.py                  # medicao padrao
    python scripts\\40_versoes.py --amostra-faixa 600
    python scripts\\40_versoes.py --limite 500     # teste rapido
    python scripts\\40_versoes.py --so-relatorio
"""

from __future__ import annotations

import argparse
import random
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config_util import caminho_banco, exigir_fase_analise, ler_config  # noqa: E402
from graph_util import ErroGraph, Graph  # noqa: E402

RAIZ_REPO = Path(__file__).resolve().parents[1]
ESQUEMA_VERSOES = RAIZ_REPO / "sql" / "02_versoes.sql"

MB = 1024**2
GB = 1024**3

# Faixas de tamanho. O 'todos' das maiores nao e opcional: sao elas que
# explicam o volume, e estimar onde da para medir seria trocar numero por chute.
ESTRATOS = [
    ("A  >= 200 MB", 200 * MB, None,      None),   # None = medir todos
    ("B  50-200 MB",  50 * MB, 200 * MB,  None),
    ("C  10-50 MB",   10 * MB,  50 * MB,  "amostra"),
    ("D  1-10 MB",     1 * MB,  10 * MB,  "amostra"),
    ("E  < 1 MB",            0, 1 * MB,   "amostra"),
]

try:
    sys.stdout.reconfigure(errors="replace")
except AttributeError:
    pass


def agora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ptbr(t: str) -> str:
    return t.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def num(n, casas: int = 0) -> str:
    return ptbr(f"{n:,.{casas}f}")


def tam(n: int | float) -> str:
    n = n or 0
    for unidade, divisor in (("TB", 1024**4), ("GB", GB), ("MB", MB), ("KB", 1024)):
        if abs(n) >= divisor:
            return f"{num(n / divisor, 1)} {unidade}"
    return f"{num(n)} B"


# ----------------------------------------------------------------------

def selecionar(con: sqlite3.Connection, amostra_faixa: int, semente: int,
               so_faixas: str | None = None) -> list[tuple]:
    """Escolhe quais arquivos medir, por estrato. Devolve (estrato, drive, item, tamanho).

    `so_faixas` restringe as faixas trabalhadas (ex.: 'C' ou 'C,D'), para
    aprofundar uma faixa cuja extrapolacao ficou incerta sem remedir o resto.
    """
    aleatorio = random.Random(semente)
    escolhidos: list[tuple] = []
    letras = ({f.strip().upper() for f in so_faixas.split(",")}
              if so_faixas else None)

    for rotulo, minimo, maximo, modo in ESTRATOS:
        if letras and rotulo.split()[0] not in letras:
            continue
        cond = "tamanho >= ?" + (" AND tamanho < ?" if maximo else "")
        args = [minimo] + ([maximo] if maximo else [])
        linhas = con.execute(
            f"""SELECT drive_id, item_id, tamanho FROM item_nuvem
                 WHERE eh_pasta=0 AND deletado=0 AND {cond}
                 ORDER BY tamanho DESC""", args).fetchall()

        total = len(linhas)
        bytes_total = sum(l[2] or 0 for l in linhas)

        if modo == "amostra" and total > amostra_faixa:
            selecao = aleatorio.sample(linhas, amostra_faixa)
        else:
            selecao = linhas

        con.execute(
            """INSERT INTO versao_estrato (estrato, min_bytes, max_bytes,
                   arquivos_total, bytes_total, arquivos_medidos, bytes_medidos,
                   bytes_antigas_medidos, atualizado_em)
               VALUES (?,?,?,?,?,0,0,0,?)
               ON CONFLICT(estrato) DO UPDATE SET
                   arquivos_total=excluded.arquivos_total,
                   bytes_total=excluded.bytes_total,
                   atualizado_em=excluded.atualizado_em""",
            (rotulo, minimo, maximo, total, bytes_total, agora()))

        escolhidos += [(rotulo, d, i, t) for d, i, t in selecao]
        print(f"    {rotulo:<14} {num(total):>8} arquivos · {tam(bytes_total):>9}"
              f"   -> medir {num(len(selecao))}")

    con.commit()
    return escolhidos


def medir(g: Graph, con: sqlite3.Connection, alvos: list[tuple], limite: int | None) -> int:
    ja = {(d, i) for d, i in con.execute(
        "SELECT drive_id, item_id FROM versao_resumo WHERE erro IS NULL")}
    pendentes = [a for a in alvos if (a[1], a[2]) not in ja]
    if ja:
        print(f"    {num(len(ja))} ja medidos antes — pulando (retomada)")
    if limite:
        pendentes = pendentes[:limite]
    print(f"    medindo {num(len(pendentes))} arquivos...")

    t0 = time.time()
    feitos = falhas = 0

    for n, (estrato, drive_id, item_id, tamanho_vivo) in enumerate(pendentes, 1):
        try:
            resposta = g.get(f"/drives/{drive_id}/items/{item_id}/versions")
            versoes = resposta.get("value", [])
            total = sum(v.get("size") or 0 for v in versoes)
            # A versao corrente e a mais recente; o resto e historico.
            # O Graph identifica as versoes como '1.0', '2.0', ... — nao ha
            # id 'current'; a atual e a de data mais recente.
            corrente = (max(versoes, key=lambda v: v.get("lastModifiedDateTime") or "")
                        if versoes else None)
            atual = tamanho_vivo or (corrente.get("size") or 0 if corrente else 0)

            con.executemany(
                """INSERT INTO versao_item (drive_id, item_id, versao_id, eh_atual,
                       tamanho, modificado_em, modificado_por, visto_em)
                   VALUES (?,?,?,?,?,?,?,?)
                   ON CONFLICT(drive_id, item_id, versao_id) DO UPDATE SET
                       tamanho=excluded.tamanho, visto_em=excluded.visto_em""",
                [(drive_id, item_id, v.get("id"),
                  1 if corrente is not None and v.get("id") == corrente.get("id") else 0,
                  v.get("size"), v.get("lastModifiedDateTime"),
                  ((v.get("lastModifiedBy") or {}).get("user") or {}).get("displayName"),
                  agora()) for v in versoes])

            con.execute(
                """INSERT INTO versao_resumo (drive_id, item_id, estrato, n_versoes,
                       bytes_total, bytes_atual, bytes_antigas, consultado_em, erro)
                   VALUES (?,?,?,?,?,?,?,?,NULL)
                   ON CONFLICT(drive_id, item_id) DO UPDATE SET
                       estrato=excluded.estrato, n_versoes=excluded.n_versoes,
                       bytes_total=excluded.bytes_total, bytes_atual=excluded.bytes_atual,
                       bytes_antigas=excluded.bytes_antigas,
                       consultado_em=excluded.consultado_em, erro=NULL""",
                (drive_id, item_id, estrato, len(versoes), total, atual,
                 max(total - atual, 0), agora()))
            feitos += 1

        except ErroGraph as exc:                       # REGRA 5
            con.execute(
                """INSERT INTO versao_resumo (drive_id, item_id, estrato, consultado_em, erro)
                   VALUES (?,?,?,?,?)
                   ON CONFLICT(drive_id, item_id) DO UPDATE SET
                       erro=excluded.erro, consultado_em=excluded.consultado_em""",
                (drive_id, item_id, estrato, agora(), f"{exc.status} {exc.codigo}"))
            falhas += 1

        if n % 200 == 0:
            con.commit()
            taxa = n / max(time.time() - t0, 0.001)
            resta = (len(pendentes) - n) / max(taxa, 0.001)
            print(f"      {num(n)}/{num(len(pendentes))} · {num(taxa,1)}/s · "
                  f"faltam ~{resta/60:.0f} min · falhas {falhas}")

    con.commit()
    print(f"    medidos {num(feitos)} · falhas {num(falhas)} · "
          f"{(time.time()-t0)/60:.1f} min")
    return feitos


def consolidar(con: sqlite3.Connection) -> None:
    """Atualiza o que foi medido por estrato (base da extrapolacao)."""
    for (estrato,) in con.execute("SELECT estrato FROM versao_estrato").fetchall():
        linha = con.execute(
            """SELECT COUNT(*), COALESCE(SUM(bytes_atual),0), COALESCE(SUM(bytes_antigas),0)
                 FROM versao_resumo WHERE estrato=? AND erro IS NULL""",
            (estrato,)).fetchone()
        con.execute(
            """UPDATE versao_estrato SET arquivos_medidos=?, bytes_medidos=?,
                   bytes_antigas_medidos=?, atualizado_em=? WHERE estrato=?""",
            (linha[0], linha[1], linha[2], agora(), estrato))
    con.commit()


def relatorio(con: sqlite3.Connection) -> dict:
    print()
    print("=" * 78)
    print("HISTORICO DE VERSOES — por faixa de tamanho")
    print("=" * 78)
    print(f"  {'faixa':<14} {'arquivos':>9} {'vivos':>10} {'medidos':>8} "
          f"{'antigas(medido)':>16} {'antigas(total)':>15}")

    total_medido = total_estimado = 0
    linhas_rel = []

    for (estrato, arq_total, bytes_total, arq_med, bytes_med,
         antigas_med) in con.execute(
            """SELECT estrato, arquivos_total, bytes_total, arquivos_medidos,
                      bytes_medidos, bytes_antigas_medidos
                 FROM versao_estrato ORDER BY estrato"""):
        if not arq_med:
            print(f"  {estrato:<14} {num(arq_total):>9} {tam(bytes_total):>10} "
                  f"{'0':>8} {'(nao medido)':>16} {'?':>15}")
            continue

        completo = arq_med >= arq_total
        if completo:
            antigas_total = antigas_med
        else:
            razao = antigas_med / bytes_med if bytes_med else 0
            antigas_total = razao * bytes_total

        total_medido += antigas_med
        total_estimado += antigas_total
        marca = "" if completo else " ~"
        print(f"  {estrato:<14} {num(arq_total):>9} {tam(bytes_total):>10} "
              f"{num(arq_med):>8} {tam(antigas_med):>16} {tam(antigas_total)+marca:>15}")
        linhas_rel.append({
            "estrato": estrato, "arquivos": arq_total, "bytes_vivos": bytes_total,
            "medidos": arq_med, "antigas_medido": antigas_med,
            "antigas_total": antigas_total, "completo": completo,
        })

    print("-" * 78)
    print(f"  {'TOTAL':<14} {'':>9} {'':>10} {'':>8} "
          f"{tam(total_medido):>16} {tam(total_estimado):>15}")
    print()
    print("  ~ = faixa amostrada; o total e extrapolado pela razao medida na faixa.")

    quota = con.execute("SELECT MAX(quota_usada), MAX(quota_lixeira) FROM biblioteca").fetchone()
    vivos = con.execute(
        "SELECT COALESCE(SUM(tamanho),0) FROM item_nuvem WHERE eh_pasta=0 AND deletado=0"
    ).fetchone()[0]
    folga = (quota[0] or 0) - vivos

    print()
    print("=" * 78)
    print("FECHAMENTO DA COTA")
    print("=" * 78)
    print(f"  cota usada do site ........... {tam(quota[0])}")
    print(f"  (-) arquivos vivos ........... {tam(vivos)}")
    print(f"  (=) folga a explicar ......... {tam(folga)}")
    print()
    print(f"  versoes antigas (medido+est.)  {tam(total_estimado)}")
    print(f"  lixeira (quota.deleted) ...... {tam(quota[1])}")
    resto = folga - total_estimado - (quota[1] or 0)
    print(f"  nao explicado ................ {tam(resto)}")
    if folga:
        print(f"  cobertura da explicacao ...... "
              f"{ptbr(f'{(total_estimado + (quota[1] or 0))*100/folga:.0f}')}%")

    return {"antigas": total_estimado, "antigas_medido": total_medido,
            "folga": folga, "lixeira": quota[1] or 0, "vivos": vivos,
            "quota": quota[0] or 0, "estratos": linhas_rel}


def main() -> int:
    ap = argparse.ArgumentParser(description="E3(b) — historico de versoes")
    ap.add_argument("--amostra-faixa", type=int, default=800,
                    help="arquivos amostrados por faixa pequena (padrao 800)")
    ap.add_argument("--limite", type=int, help="para apos N consultas (teste)")
    ap.add_argument("--semente", type=int, default=42, help="semente da amostra")
    ap.add_argument("--faixas", help="so estas faixas, ex.: 'C' ou 'C,D'")
    ap.add_argument("--so-relatorio", action="store_true")
    args = ap.parse_args()

    cfg = ler_config()
    exigir_fase_analise(cfg)                        # REGRA 1

    con = sqlite3.connect(caminho_banco(cfg))
    con.executescript(ESQUEMA_VERSOES.read_text(encoding="utf-8"))
    con.commit()

    try:
        if args.so_relatorio:
            consolidar(con)
            relatorio(con)
            return 0

        total = con.execute(
            "SELECT COUNT(*) FROM item_nuvem WHERE eh_pasta=0 AND deletado=0").fetchone()[0]
        if not total:
            print("Catalogo vazio. Rodar antes: python scripts\\20_inventario.py")
            return 1

        print("=" * 78)
        print("E3(b) — HISTORICO DE VERSOES (somente GET, nada e apagado)")
        print("=" * 78)
        print(f"  {num(total)} arquivos vivos no catalogo")
        print()
        alvos = selecionar(con, args.amostra_faixa, args.semente, args.faixas)
        print()
        medir(Graph(), con, alvos, args.limite)
        consolidar(con)
        relatorio(con)
        return 0
    except KeyboardInterrupt:
        con.commit()
        print("\ninterrompido — progresso salvo, da para retomar")
        consolidar(con)
        return 130
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())
