"""
335_migra_lote - propoe, lista e aprova lotes de transferencia.

Propor e listar so mexem no banco local. APROVAR exige a citacao literal da
autorizacao escrita da Emmily (--texto) e grava quando e o que foi autorizado;
sem aprovacao nenhum arquivo sai da fila (336 so transfere APROVADO).

Uso:
    python scripts/335_migra_lote.py propor PILOTO-01 --piloto --origem "10. Prestação de Contas Anual Programa/%" --max 20
    python scripts/335_migra_lote.py listar PILOTO-01
    python scripts/335_migra_lote.py aprovar PILOTO-01 --texto "<citacao da Emmily>"
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "scripts"))

from migra_parceiro.base import agora, cfg, conectar, escrever_csv, gib, num, saida  # noqa: E402


def propor(con, nome, origens, regras, maximo, piloto, excluir=()) -> int:
    if con.execute("SELECT 1 FROM lote WHERE nome=?", (nome,)).fetchone():
        print(f"ERRO: lote {nome} ja existe")
        return 1
    cond, par = ["p.estado='AGUARDANDO_APROVACAO'", "p.lote IS NULL"], []
    if origens:
        cond.append("(" + " OR ".join("o.caminho_rel LIKE ?" for _ in origens) + ")")
        par += origens
    if regras:
        cond.append("p.regra IN (" + ",".join("?" * len(regras)) + ")")
        par += regras
    for ex in excluir:
        cond.append("o.caminho_rel NOT LIKE ?")
        par.append(ex)
    sql = (f"SELECT p.item_id FROM plano_item p JOIN origem_item o USING(item_id) WHERE {' AND '.join(cond)} "
           f"ORDER BY o.caminho_rel" + (f" LIMIT {int(maximo)}" if maximo else ""))
    ids = [r[0] for r in con.execute(sql, par)]
    if not ids:
        print("Nenhum arquivo AGUARDANDO_APROVACAO casa com o criterio.")
        return 1
    con.executemany("UPDATE plano_item SET lote=?, atualizado_em=? WHERE item_id=?", [(nome, agora(), i) for i in ids])
    criterio = f"origens={origens} regras={regras} max={maximo}"
    con.execute("INSERT INTO lote (nome, criado_em, criterio, piloto) VALUES (?,?,?,?)",
                (nome, agora(), criterio, int(piloto)))
    con.commit()
    return listar(con, nome)


def listar(con, nome) -> int:
    lt = con.execute("SELECT * FROM lote WHERE nome=?", (nome,)).fetchone()
    if not lt:
        print(f"ERRO: lote {nome} nao existe")
        return 1
    itens = con.execute(
        "SELECT o.caminho_rel, o.tamanho, p.destino_pasta, p.nome_final, p.estado, p.regra FROM plano_item p "
        "JOIN origem_item o USING(item_id) WHERE p.lote=? ORDER BY o.caminho_rel", (nome,)).fetchall()
    existentes = {r[0] for r in con.execute("SELECT caminho FROM destino_item WHERE eh_pasta=1")}
    pastas = set()
    for it in itens:
        segs = it["destino_pasta"].split("/")
        for i in range(1, len(segs) + 1):
            p = "/".join(segs[:i])
            if p not in existentes:
                pastas.add(p)
    total = sum(it["tamanho"] or 0 for it in itens)
    con.execute("UPDATE lote SET arquivos=?, bytes=?, pastas_a_criar=? WHERE nome=?",
                (len(itens), total, len(pastas), nome))
    con.commit()
    out = saida(cfg())
    escrever_csv(out / f"lote_{nome}.csv", ["origem", "bytes", "destino_pasta", "nome_final", "estado", "regra"],
                 (tuple(i) for i in itens))
    escrever_csv(out / f"lote_{nome}_pastas_a_criar.csv", ["pasta"], ((p,) for p in sorted(pastas)))
    print(f"Lote {nome}{' (PILOTO)' if lt['piloto'] else ''}: {num(len(itens))} arquivos · {gib(total)} GiB · "
          f"{num(len(pastas))} pastas a criar · aprovado: {lt['aprovado_em'] or 'NAO'}")
    print(f"  criterio: {lt['criterio']}")
    for it in itens[:15]:
        print(f"  {it['caminho_rel'][:70]:70} -> {it['destino_pasta'][-60:]}/{it['nome_final'][:40]}")
    if len(itens) > 15:
        print(f"  ... (+{len(itens) - 15}) lista completa em {out / f'lote_{nome}.csv'}")
    return 0


def aprovar(con, nome, texto) -> int:
    if not texto or len(texto.strip()) < 10:
        print("ERRO: --texto com a citacao literal da autorizacao da Emmily e obrigatorio")
        return 1
    lt = con.execute("SELECT * FROM lote WHERE nome=?", (nome,)).fetchone()
    if not lt:
        print(f"ERRO: lote {nome} nao existe")
        return 1
    quando = agora()
    ids = [r[0] for r in con.execute(
        "SELECT item_id FROM plano_item WHERE lote=? AND estado='AGUARDANDO_APROVACAO'", (nome,))]
    con.executemany("UPDATE plano_item SET estado='APROVADO', aprovado_em=?, atualizado_em=? WHERE item_id=?",
                    [(quando, quando, i) for i in ids])
    con.executemany("INSERT INTO evento (item_id, quando, de_estado, para_estado, detalhe) VALUES (?,?,?,?,?)",
                    [(i, quando, "AGUARDANDO_APROVACAO", "APROVADO", f"lote {nome}") for i in ids])
    con.execute("UPDATE lote SET aprovado_em=?, aprovacao_texto=? WHERE nome=?", (quando, texto.strip(), nome))
    con.commit()
    print(f"Lote {nome} aprovado: {num(len(ids))} arquivos APROVADO em {quando}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("acao", choices=["propor", "listar", "aprovar"])
    ap.add_argument("nome")
    ap.add_argument("--origem", action="append", default=[], help="LIKE sobre o caminho de origem")
    ap.add_argument("--regra", action="append", default=[])
    ap.add_argument("--excluir", action="append", default=[], help="LIKE de caminho de origem a deixar FORA do lote")
    ap.add_argument("--max", type=int, default=0)
    ap.add_argument("--piloto", action="store_true")
    ap.add_argument("--texto", default="")
    ap.add_argument("--texto-arquivo", help="arquivo UTF-8 com a citacao literal (evita problemas de aspas)")
    a = ap.parse_args()
    if a.texto_arquivo:
        a.texto = Path(a.texto_arquivo).read_text(encoding="utf-8")
    con = conectar()
    if a.acao == "propor":
        return propor(con, a.nome, a.origem, a.regra, a.max, a.piloto, a.excluir)
    if a.acao == "listar":
        return listar(con, a.nome)
    return aprovar(con, a.nome, a.texto)


if __name__ == "__main__":
    raise SystemExit(main())
