"""
343_migra_decisao_corresp - aplica a DECISAO da Emmily (19/10/2026) sobre as 12 correspondencias:
`05. Produtos/00. Correspondências` (Carta no 05/2026 a Órgão Contratante, Notificacao 3/2026, RD-104)
-> `02. Programa/01. Correspondências`, com a arvore original preservada.

Mexe SO nas linhas PENDENTE_DE_DECISAO da regra R07; passa para AGUARDANDO_APROVACAO.
NAO cria nem aprova lote (isso e o 335, com aprovacao propria). Sem --aplicar, so simula.

Uso:
    python scripts/343_migra_decisao_corresp.py                      # simula
    python scripts/343_migra_decisao_corresp.py --aplicar --texto-arquivo aprovacao.txt
"""

from __future__ import annotations

import argparse
import sys
import unicodedata
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "scripts"))

from migra_parceiro.base import agora, conectar  # noqa: E402

DESTINO = "02. Programa/01. Correspondências"


def k(s: str) -> str:
    return unicodedata.normalize("NFC", s).casefold()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--aplicar", action="store_true")
    ap.add_argument("--texto-arquivo")
    a = ap.parse_args()
    con = conectar()
    itens = con.execute("SELECT p.item_id, p.estado, p.lote, p.destino_pasta, p.nome_final, o.caminho_rel "
                        "FROM plano_item p JOIN origem_item o USING(item_id) "
                        "WHERE p.regra='R07_produtos_correspondencias'").fetchall()
    if len(itens) != 12 or any(i["estado"] != "PENDENTE_DE_DECISAO" or i["lote"] for i in itens):
        print(f"ERRO: esperado 12 itens PENDENTE_DE_DECISAO sem lote; encontrado {len(itens)}: "
              f"{sorted({(i['estado'], i['lote']) for i in itens})}")
        return 1
    # o destino ja vem calculado pela regra R07 (sem pendencia de nome); confere e checa colisoes
    finais = {k(f"{d}/{n}") for d, n in con.execute(
        "SELECT destino_pasta, nome_final FROM plano_item WHERE escopo='migrar' AND regra!='R07_produtos_correspondencias'")}
    col = [i for i in itens if k(f"{i['destino_pasta']}/{i['nome_final']}") in finais]
    fora = [i for i in itens if not i["destino_pasta"].startswith(DESTINO)]
    existentes = {k(r[0]) for r in con.execute("SELECT caminho FROM destino_item WHERE eh_pasta=0")}
    ja = [i for i in itens if k(f"{i['destino_pasta']}/{i['nome_final']}") in existentes]
    for i in itens:
        print(f"  {i['caminho_rel'][-70:]:70} -> {i['destino_pasta']}/{i['nome_final'][:45]}")
    print(f"colisoes com o plano: {len(col)} | fora de {DESTINO}: {len(fora)} | ja no PARCEIRO (inventario 19/10): {len(ja)}")
    if col or fora or ja:
        print("BLOQUEADO: ha conflito; nada gravado.")
        return 1
    if not a.aplicar:
        print("SIMULACAO: nada gravado.")
        return 0
    if not a.texto_arquivo:
        print("ERRO: --aplicar exige --texto-arquivo")
        return 1
    texto = Path(a.texto_arquivo).read_text(encoding="utf-8").strip()
    quando = agora()
    con.executemany("UPDATE plano_item SET estado='AGUARDANDO_APROVACAO', escopo='migrar', categoria=NULL, "
                    "atualizado_em=?, observacao=? WHERE item_id=? AND estado='PENDENTE_DE_DECISAO'",
                    [(quando, f"destino aprovado pela Emmily em {quando}", i["item_id"]) for i in itens])
    con.executemany("INSERT INTO evento (item_id, quando, de_estado, para_estado, detalhe) VALUES (?,?,?,?,?)",
                    [(i["item_id"], quando, "PENDENTE_DE_DECISAO", "AGUARDANDO_APROVACAO",
                      f"decisao correspondencias: {texto[:400]}") for i in itens])
    con.commit()
    print(f"Aplicado: 12 itens AGUARDANDO_APROVACAO (nenhum lote criado).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
