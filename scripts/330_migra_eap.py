"""
330_migra_eap - le a EAP oficial (EAP_Repositorio.xlsx), grava a arvore em
data/migracao_parceiro.db (eap_no) e, se o destino ja foi inventariado
(332_migra_inventario_destino.py), cruza a EAP com as pastas reais do PARCEIRO.

Saidas em output/migracao_parceiro/:
  eap_estrutura.csv     - arvore oficial expandida (principal + consorciadas)
  eap_estrutura.json    - a mesma arvore, aninhada
  eap_x_destino.csv     - cada no fixo da EAP: existe | equivalente | semelhante | ausente
  eap_divergencias.txt  - Fluxograma x lista discordantes

Nao propoe renomear nada no destino: 'equivalente' = reusar a pasta real.
A EAP de consorciadas e lida e registrada, mas NAO entra no cruzamento
(e proposta para validacao).

Uso:
    python scripts/330_migra_eap.py --eap "C:\\caminho\\exemplo\\Downloads\\EAP_Repositorio.xlsx"
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "scripts"))

from migra_parceiro.base import cfg, conectar, escrever_csv, saida  # noqa: E402
from migra_parceiro.eap import cruzar, ler_eap  # noqa: E402


def aninhar(no) -> dict:
    return {"nome": no.nome, "codigo": no.codigo, "modelo": no.modelo,
            "descricao": no.descricao, "filhos": [aninhar(f) for f in no.filhos]}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--eap", required=True, help="caminho do EAP_Repositorio.xlsx")
    args = ap.parse_args()

    c = cfg()
    out = saida(c)
    nos, div = ler_eap(args.eap)

    con = conectar()
    con.execute("DELETE FROM eap_no")
    con.executemany(
        "INSERT INTO eap_no (arvore, caminho, nome, nivel, pai, modelo, codigo, descricao, ordem) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        [(n.arvore, n.caminho, n.nome, n.nivel, n.pai, int(n.modelo), n.codigo, n.descricao, n.ordem)
         for n in nos])
    con.commit()

    escrever_csv(out / "eap_estrutura.csv",
                 ["arvore", "ordem", "codigo", "nivel", "modelo", "caminho", "descricao"],
                 ((n.arvore, n.ordem, n.codigo or "", n.nivel, int(n.modelo), n.caminho, n.descricao)
                  for n in nos))
    raizes = {a: [aninhar(n) for n in nos if n.arvore == a and n.nivel == 0]
              for a in ("principal", "consorciadas")}
    (out / "eap_estrutura.json").write_text(json.dumps(raizes, ensure_ascii=False, indent=1),
                                            encoding="utf-8")
    (out / "eap_divergencias.txt").write_text("\n".join(div) or "nenhuma", encoding="utf-8")

    por_arvore = Counter(n.arvore for n in nos)
    print(f"EAP: {dict(por_arvore)} nos · modelos: {sum(n.modelo for n in nos)} · "
          f"divergencias: {len(div)}")
    for d in div:
        print("  !", d)

    pastas = {r["caminho"] for r in con.execute("SELECT caminho FROM destino_item WHERE eh_pasta=1")}
    if not pastas:
        print("Destino nao inventariado: rode 332_migra_inventario_destino.py para o cruzamento.")
        return 0
    cruz = cruzar(nos, pastas)
    con.execute("DELETE FROM pasta_destino")
    con.executemany(
        "INSERT INTO pasta_destino (caminho_eap, caminho_real, situacao, acao, observacao) "
        "VALUES (?,?,?,?,?)",
        [(l["caminho_eap"], l["caminho_real"], l["situacao"], l["acao"], l["observacao"])
         for l in cruz if l["situacao"] not in ("modelo", "sob_modelo")])
    con.commit()
    cab = list(cruz[0])
    escrever_csv(out / "eap_x_destino.csv", cab, ([l[k] for k in cab] for l in cruz))
    print("EAP x destino:", dict(Counter(l["situacao"] for l in cruz)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
