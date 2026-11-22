"""
210_mover_lixeira - E5: move para a LIXEIRA do SharePoint as pastas
autorizadas pela Emmily em 30/09/2026.

ESTE SCRIPT APAGA. Diferente de todos os outros do projeto.

O que ele faz: DELETE /drives/{id}/items/{id} — o item vai para a LIXEIRA
do site, de onde pode ser restaurado (retencao tipica de 93 dias). Nao e
exclusao definitiva.

TRAVAS:
  - so apaga caminho que esteja na lista fechada (as duas fontes abaixo);
  - RECUSA qualquer caminho de RD102A (nao foi copiada para o OneDrive);
  - confere que a pasta existe e mede antes de apagar;
  - grava log com item_id, caminho, tamanho e resultado — e e esse log que
    permite localizar e restaurar cada item depois;
  - --simular mostra tudo sem apagar nada.

AUTORIZACAO: Emmily Maciel, nesta conversa, 30/09/2026:
  "mova pra lixeira no sharepoint todas do compartilhamento interno (da
   regra que vc disse) e de projeto permitidas menos da 102a"

Uso:
    python scripts\\210_mover_lixeira.py --simular
    python scripts\\210_mover_lixeira.py --frente prestacoes
    python scripts\\210_mover_lixeira.py --frente projetos
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config_util import caminho_banco, ler_config  # noqa: E402
from graph_util import Graph, GRAPH                # noqa: E402

import sqlite3

try:
    sys.stdout.reconfigure(errors="replace")
except AttributeError:
    pass

GiB, MiB = 1024**3, 1024**2
BRT = timezone(timedelta(hours=-3))
RAIZ = Path(__file__).resolve().parents[1]
LOG = RAIZ / "logs" / "lixeira.csv"

BASE_INT = "/2. Compartilhado interno"
PRESTACOES = [
    f"{BASE_INT}/04_Resposta Prestacao de Contas - Trimestral - Programa/OUT A DEZ - 2025",
    f"{BASE_INT}/04_Resposta Prestacao de Contas - Trimestral - Programa/JAN A MAR - 2026",
    f"{BASE_INT}/04_Resposta Prestacao de Contas - Trimestral - Programa/ABR A JUN - 2026",
    f"{BASE_INT}/05_Resposta Prestação de Contas  - Mensal - Programa/01. ABRIL 2026",
    f"{BASE_INT}/05_Resposta Prestação de Contas  - Mensal - Programa/02. MAIO 2026",
    f"{BASE_INT}/05_Resposta Prestação de Contas  - Mensal - Programa/03. JUNHO 2026",
    f"{BASE_INT}/06_Resposta Prestação de Contas Anual - Programa/2025/1. Diário de Obras",
    f"{BASE_INT}/07_Resposta Prestação de Contas - Obras/1º TRIMESTRE",
    f"{BASE_INT}/07_Resposta Prestação de Contas - Obras/2º TRIMESTRE",
]

BLOQUEADO = "RD102A"      # nao copiada para o OneDrive — nunca apagar

# A RD102A foi copiada para o Google Drive em 01/10/2026, MENOS os dois
# videos de trafego. Eles vivem todos nesta pasta, que fica retida ate a
# copia deles terminar. Autorizacao da Emmily, nesta conversa:
#   "2. pode apagar as pastas que já tem copia ok"
PASTA_DOS_VIDEOS = ("/3. Desenvolvimento/G2 -  Engenharia/RD102A/4_Projeto Executivo"
                    "/1. Fase de Estudos/ESTUDOS DE TRÁFEGO/REV 01 - 27 02 26")


def ptbr(t): return t.replace(",", "\x00").replace(".", ",").replace("\x00", ".")
def num(n, c=0): return ptbr(f"{n:,.{c}f}")
def tam(b): return f"{num(b/GiB,2)} GiB" if b >= GiB else f"{num(b/MiB,0)} MB"


def apagar(g: Graph, drive_id: str, item_id: str) -> None:
    """DELETE no Graph -> o item vai para a lixeira do site."""
    url = f"{GRAPH}/drives/{drive_id}/items/{item_id}"
    req = urllib.request.Request(url, method="DELETE",
                                 headers={"Authorization": f"Bearer {g.token()}"})
    with urllib.request.urlopen(req, timeout=120) as r:
        if r.status not in (200, 204):
            raise RuntimeError(f"HTTP {r.status}")


def registrar(linha: dict) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    novo = not LOG.exists()
    with LOG.open("a", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, delimiter=";", fieldnames=[
            "quando", "frente", "caminho", "item_id", "arquivos", "bytes",
            "resultado", "detalhe"])
        if novo:
            w.writeheader()
        w.writerow(linha)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--frente",
                    choices=["prestacoes", "projetos", "rd102a", "tudo"],
                    default="tudo")
    ap.add_argument("--simular", action="store_true")
    args = ap.parse_args()

    cfg = ler_config()
    con = sqlite3.connect(f"file:{caminho_banco(cfg)}?mode=ro", uri=True)

    # ---- monta a lista fechada ---------------------------------------
    alvos = []
    if args.frente in ("prestacoes", "tudo"):
        alvos += [("prestacoes", c) for c in PRESTACOES]
    if args.frente in ("projetos", "tudo"):
        with (Path(cfg["saida"]) / "LISTA_PARA_APAGAR.csv").open(
                encoding="utf-8-sig") as fh:
            for r in csv.DictReader(fh, delimiter=";"):
                if r["rodovia"] == BLOQUEADO:
                    continue
                alvos.append(("projetos", r["caminho_completo"]))

    if args.frente == "rd102a":
        with (Path(cfg["saida"]) / "RD102A_APAGAR_AGORA.csv").open(
                encoding="utf-8-sig") as fh:
            for r in csv.DictReader(fh, delimiter=";"):
                alvos.append(("rd102a", r["caminho_completo"]))

    # ---- TRAVAS ------------------------------------------------------
    if args.frente == "rd102a":
        # aqui a 102A e permitida, mas a pasta dos videos nao: eles ainda
        # nao foram copiados para o Google Drive.
        perigo = [c for _, c in alvos
                  if c == PASTA_DOS_VIDEOS or c.startswith(PASTA_DOS_VIDEOS + "/")
                  or PASTA_DOS_VIDEOS.startswith(c + "/") or c == "/"]
        rotulo = "a pasta dos videos (ainda nao copiados)"
    else:
        perigo = [c for _, c in alvos if BLOQUEADO in c]
        rotulo = BLOQUEADO
    if perigo:
        print(f"ABORTADO: {len(perigo)} caminho(s) tocam {rotulo}.")
        for c in perigo[:5]:
            print("   ", c)
        return 1

    g = Graph()
    sid = g.site_id()
    did = [d for d in g.bibliotecas(sid) if d["name"] == "Programa"][0]["id"]
    agora = datetime.now(BRT)

    print("=" * 74)
    print(f"{'SIMULACAO' if args.simular else 'MOVER PARA A LIXEIRA'} — "
          f"{agora:%d/%m/%Y %H:%M}")
    print("=" * 74)
    print(f"  {len(alvos)} pasta(s) na lista · RD102A bloqueada")
    print()

    ok = erro = ausente = 0
    bytes_ok = arq_ok = 0
    for i, (frente, caminho) in enumerate(alvos, 1):
        a, b = con.execute(
            "SELECT COUNT(*), COALESCE(SUM(tamanho),0) FROM item_nuvem"
            " WHERE eh_pasta=0 AND deletado=0 AND (caminho=? OR caminho LIKE ?)",
            (caminho, caminho + "/%")).fetchone()
        curto = caminho if len(caminho) < 64 else "…" + caminho[-61:]

        try:
            enc = urllib.parse.quote(caminho, safe="/:$&=,@!*'()+~")
            item = g.get(f"/drives/{did}/root:{enc}")
        except Exception as exc:
            print(f"  [{i:>3}/{len(alvos)}] NAO ENCONTRADA  {curto}")
            registrar({"quando": agora.isoformat(), "frente": frente,
                       "caminho": caminho, "item_id": "", "arquivos": a,
                       "bytes": b, "resultado": "NAO_ENCONTRADA",
                       "detalhe": str(exc)[:200]})
            ausente += 1
            continue

        if args.simular:
            print(f"  [{i:>3}/{len(alvos)}] [simular] {curto}")
            print(f"            {a} arq · {tam(b)} · id={item['id']}")
            ok += 1; bytes_ok += b; arq_ok += a
            continue

        try:
            apagar(g, did, item["id"])
            print(f"  [{i:>3}/{len(alvos)}] lixeira  {curto}  ({a} arq · {tam(b)})")
            registrar({"quando": datetime.now(BRT).isoformat(), "frente": frente,
                       "caminho": caminho, "item_id": item["id"], "arquivos": a,
                       "bytes": b, "resultado": "OK", "detalhe": ""})
            ok += 1; bytes_ok += b; arq_ok += a
        except Exception as exc:
            print(f"  [{i:>3}/{len(alvos)}] ERRO     {curto} -> {exc}")
            registrar({"quando": datetime.now(BRT).isoformat(), "frente": frente,
                       "caminho": caminho, "item_id": item["id"], "arquivos": a,
                       "bytes": b, "resultado": "ERRO", "detalhe": str(exc)[:200]})
            erro += 1

    print()
    print("=" * 74)
    print(f"  {'simuladas' if args.simular else 'movidas para a lixeira'}: {ok}"
          f"   nao encontradas: {ausente}   erros: {erro}")
    print(f"  arquivos: {num(arq_ok)} · espaco: {tam(bytes_ok)}")
    if not args.simular:
        print()
        print(f"  log (permite restaurar): {LOG}")
        print("  Os itens estao na LIXEIRA do site e podem ser restaurados.")
        print("  A cota so baixa depois que a lixeira for esvaziada.")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    sys.exit(main())
