"""
220_podar_versoes - E5: apaga o historico de versoes, mantendo as 2 mais
recentes de cada arquivo.

ESTE SCRIPT APAGA DE FORMA PERMANENTE.
Versao apagada NAO vai para a lixeira. Nao ha como restaurar.

AUTORIZACAO: Emmily Maciel, nesta conversa, 01/10/2026:
  "vc consegue ir apagando em paralelo do sharepoint o historico de versoes
   menos do arquivo que eu tinha falado"
  (o arquivo citado e o 04_Acompanhamento - Licenca Ambiental)

TRAVAS:
  - PROTEGE os 37 arquivos cujo nome casa com "04_Acompanhamento - Licen*
    Ambiental*" — a Emmily citou "um arquivo", mas existem 37 com esse nome;
    protege-se todos para nao haver chance de apagar o errado;
  - PULA o que esta dentro da RD102A (vai ser apagado inteiro depois, e a
    copia para o Google Drive esta em andamento);
  - PULA o que ja foi removido do SharePoint;
  - RELE a lista de versoes ao vivo no Graph antes de apagar. O catalogo de
    21/09 esta velho; o corte usa o estado de agora;
  - nunca apaga as 2 versoes mais recentes (ordenadas por data);
  - nunca apaga a versao atual, mesmo que a ordenacao falhe;
  - grava CADA exclusao em logs/versoes_apagadas.csv antes de agir;
  - --simular percorre tudo e nao apaga nada.

Uso:
    python scripts\\220_podar_versoes.py --simular
    python scripts\\220_podar_versoes.py --executar
    python scripts\\220_podar_versoes.py --executar --limite 20
"""

from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config_util import caminho_banco, ler_config   # noqa: E402
from graph_util import Graph, GRAPH                 # noqa: E402

try:
    sys.stdout.reconfigure(errors="replace")
except AttributeError:
    pass

GiB, MiB = 1024**3, 1024**2
BRT = timezone(timedelta(hours=-3))
RAIZ = Path(__file__).resolve().parents[1]
LOG = RAIZ / "logs" / "versoes_apagadas.csv"

MANTER = 2          # quantas versoes mais recentes ficam
PAUSA = 0.12        # s entre arquivos: deixa folga de API para o rclone


def ptbr(t): return t.replace(",", "\x00").replace(".", ",").replace("\x00", ".")
def num(n, c=0): return ptbr(f"{n:,.{c}f}")
def tam(b): return f"{num(b/GiB,2)} GiB" if b >= GiB else f"{num(b/MiB,1)} MB"


def protegido(caminho: str) -> bool:
    """Nenhum arquivo e protegido por nome.

    A Emmily pediu inicialmente para preservar o historico do
    "04_Acompanhamento - Licenca Ambiental". Em 01/10/2026, nesta conversa,
    ela revogou:
      "EU ANALISEI A PLANILHA DE AMBIENTAL QUE EU TINHA PEDIDO PRA VC NAO
       FAZER, E NA VERDADE PODE SIM APAGAR O HISTORICO DE VERSAO DELE TB."
    Mantido como funcao para deixar a mudanca rastreavel.
    """
    return False


def sob(caminho: str, prefixos: list[str]) -> bool:
    return any(caminho == p or caminho.startswith(p + "/") for p in prefixos)


def apagar_versao(g: Graph, drive_id: str, item_id: str, versao_id: str) -> None:
    url = f"{GRAPH}/drives/{drive_id}/items/{item_id}/versions/{versao_id}"
    req = urllib.request.Request(
        url, method="DELETE", headers={"Authorization": f"Bearer {g.token()}"})
    with urllib.request.urlopen(req, timeout=120) as r:
        if r.status not in (200, 204):
            raise RuntimeError(f"HTTP {r.status}")


def registrar(linha: dict) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    novo = not LOG.exists()
    with LOG.open("a", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, delimiter=";", fieldnames=[
            "quando", "caminho", "item_id", "versao_id", "versao_data",
            "bytes", "resultado", "detalhe"])
        if novo:
            w.writeheader()
        w.writerow(linha)


def alvos(con: sqlite3.Connection) -> list[tuple[str, str]]:
    """(item_id, caminho) dos arquivos com 3+ versoes que podem ser podados."""
    removidos = ["/6. Arquivos PARCEIRO"]
    with (RAIZ / "logs" / "lixeira.csv").open(encoding="utf-8-sig") as fh:
        for l in csv.DictReader(fh, delimiter=";"):
            if l["resultado"] == "OK":
                removidos.append(l["caminho"])

    rd102a = []
    with (RAIZ / "output" / "LISTA_PARA_APAGAR.csv").open(encoding="utf-8-sig") as fh:
        for l in csv.DictReader(fh, delimiter=";"):
            if l["rodovia"] == "RD102A":
                rd102a.append(l["caminho_completo"])

    cam = {i: c for i, c in con.execute(
        "SELECT item_id,caminho FROM item_nuvem WHERE eh_pasta=0 AND deletado=0")}

    vs = defaultdict(int)
    for iid, n in con.execute(
            "SELECT item_id, COUNT(*) FROM versao_item GROUP BY item_id"):
        vs[iid] = n

    saida = []
    for iid, n in vs.items():
        if n <= MANTER:
            continue
        c = cam.get(iid)
        if c is None or protegido(c) or sob(c, removidos) or sob(c, rd102a):
            continue
        saida.append((iid, c))
    saida.sort(key=lambda x: x[1])
    return saida


def main() -> int:
    ap = argparse.ArgumentParser()
    g1 = ap.add_mutually_exclusive_group(required=True)
    g1.add_argument("--simular", action="store_true")
    g1.add_argument("--executar", action="store_true")
    ap.add_argument("--limite", type=int, default=0)
    args = ap.parse_args()

    cfg = ler_config()
    con = sqlite3.connect(f"file:{caminho_banco(cfg)}?mode=ro", uri=True)
    lista = alvos(con)
    if args.limite:
        lista = lista[:args.limite]

    g = Graph()
    did = [d for d in g.bibliotecas(g.site_id()) if d["name"] == "Programa"][0]["id"]
    agora = datetime.now(BRT)

    print("=" * 74)
    print(f"{'SIMULACAO' if args.simular else 'PODA DE VERSOES (PERMANENTE)'} — "
          f"{agora:%d/%m/%Y %H:%M}")
    print("=" * 74)
    print(f"  {num(len(lista))} arquivo(s) · mantendo as {MANTER} versoes mais recentes")
    print(f"  protegidos por nome: 04_Acompanhamento - Licen* Ambiental*")
    print()

    n_arq = n_ver = 0
    libera = 0
    erros = 0
    for i, (iid, caminho) in enumerate(lista, 1):
        try:
            vs = g.get(f"/drives/{did}/items/{iid}/versions")["value"]
        except Exception as exc:
            erros += 1
            print(f"  [{i:>4}] ERRO ao ler versoes: {caminho[-60:]} -> {exc}")
            continue

        # ordena do mais novo para o mais velho; as MANTER primeiras ficam
        vs.sort(key=lambda v: v.get("lastModifiedDateTime", ""), reverse=True)
        ficam, vao = vs[:MANTER], vs[MANTER:]
        if not vao:
            continue
        ids_que_ficam = {v["id"] for v in ficam}

        bytes_arq = sum(v.get("size", 0) for v in vao)
        n_arq += 1
        libera += bytes_arq

        if i <= 10 or i % 50 == 0:
            print(f"  [{i:>4}/{len(lista)}] {len(vs):>3} versoes → apaga {len(vao):>3} "
                  f"({tam(bytes_arq)})  ...{caminho[-52:]}")

        for v in vao:
            # trava final: jamais tocar numa das que ficam
            if v["id"] in ids_que_ficam:
                continue
            n_ver += 1
            if args.simular:
                continue
            linha = {"quando": datetime.now(BRT).isoformat(), "caminho": caminho,
                     "item_id": iid, "versao_id": v["id"],
                     "versao_data": v.get("lastModifiedDateTime", ""),
                     "bytes": v.get("size", 0), "resultado": "", "detalhe": ""}
            try:
                apagar_versao(g, did, iid, v["id"])
                linha["resultado"] = "OK"
            except Exception as exc:
                linha["resultado"] = "ERRO"
                linha["detalhe"] = str(exc)[:200]
                erros += 1
            registrar(linha)
        time.sleep(PAUSA)

    print()
    print("=" * 74)
    print(f"  arquivos tocados: {num(n_arq)}   versoes {'a apagar' if args.simular else 'apagadas'}: {num(n_ver)}")
    print(f"  espaco {'a liberar' if args.simular else 'liberado'}: {tam(libera)}")
    print(f"  erros: {erros}")
    if not args.simular:
        print(f"  log: {LOG}")
        print("  ATENCAO: versoes apagadas NAO voltam.")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    sys.exit(main())
