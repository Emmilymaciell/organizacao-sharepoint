"""
333_migra_planejar - matriz origem -> destino da migracao ao PARCEIRO INFRA.

Le origem_item (331) e destino_item (332) de data/migracao_parceiro.db, aplica
as regras de migra_parceiro/regras.py a CADA arquivo e grava plano_item.
Nenhum arquivo fica sem situacao: migrar, fora do escopo ou pendente (com a
decisao que o bloqueia).

Resolucao do caminho real: a pasta final (nomes da EAP) e casada, nivel a
nivel, com as pastas que JA existem no PARCEIRO - nome exato ou mesma chave
(ex.: "01. Coordenação Programa" -> "01.Coordenação Programa"). O que nao existe vira
"pasta a criar". Nada no destino e renomeado.

Somente leitura no SharePoint e no PARCEIRO: so le o banco local e escreve
relatorios em output/migracao_parceiro/.

Saidas:
  matriz_preliminar.csv     - 1 linha por arquivo (origem, destino, nome final, regra, estado)
  matriz_por_pasta.csv      - agregado por pasta de origem -> pasta de destino
  pastas_a_criar.csv        - pastas ausentes no PARCEIRO exigidas pelo plano (com nº de arquivos)
  decisoes_pendentes.csv    - cada decisao aberta: arquivos e bytes que ela bloqueia
  conflitos.csv             - colisoes de caminho final, arquivo ja existente no destino, nome invalido
  duplicatas_origem.csv     - mesmo QuickXorHash+tamanho em caminhos diferentes (so relatorio)
"""

from __future__ import annotations

import sys
from collections import Counter, defaultdict
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "scripts"))

from migra_parceiro.base import (abrir_execucao, agora, cfg, conectar,  # noqa: E402
                             escrever_csv, fechar_execucao, gib, num, saida)
from migra_parceiro.nomes import chave, colisoes, problemas_segmento  # noqa: E402
from migra_parceiro.regras import DECISOES, classificar, nome_final  # noqa: E402


class Resolvedor:
    """Casa um caminho de pastas (nomes EAP) com a arvore real do PARCEIRO."""

    def __init__(self, pastas_reais: set[str]):
        self.filhos: dict[str, dict[str, str]] = defaultdict(dict)
        for p in pastas_reais:
            pai, _, nome = p.rpartition("/")
            self.filhos[pai].setdefault(chave(nome), nome)
        self.cache: dict[str, tuple[str, int]] = {}

    def resolver(self, caminho_eap: str) -> tuple[str, int]:
        """-> (caminho_real, quantos niveis finais precisam ser criados)."""
        if caminho_eap in self.cache:
            return self.cache[caminho_eap]
        real, criar = "", 0
        for seg in caminho_eap.split("/"):
            existente = None if criar else self.filhos.get(real, {}).get(chave(seg))
            if existente:
                real = f"{real}/{existente}" if real else existente
            else:
                real = f"{real}/{seg}" if real else seg
                criar += 1
        self.cache[caminho_eap] = (real, criar)
        return real, criar


def main() -> int:
    c = cfg()
    out = saida(c)
    con = conectar()
    arqs = con.execute("SELECT * FROM origem_item WHERE eh_pasta=0 AND ausente_desde IS NULL "
                       "ORDER BY caminho_rel").fetchall()
    if not arqs:
        print("Origem vazia: rode 331_migra_inventario_origem.py")
        return 1
    reais = {r["caminho"] for r in con.execute("SELECT caminho FROM destino_item WHERE eh_pasta=1")}
    arquivos_destino = {r["caminho"].casefold(): r for r in
                        con.execute("SELECT * FROM destino_item WHERE eh_pasta=0")}
    if not reais:
        print("Destino nao inventariado: rode 332_migra_inventario_destino.py")
        return 1

    # TRAVA: o planejamento reescreve plano_item inteiro. Depois que qualquer lote saiu de
    # AGUARDANDO_APROVACAO, rodar de novo apagaria o historico da migracao. Decisoes posteriores
    # vao por scripts dedicados (scripts dedicados), que mexem so nas linhas pendentes.
    em_uso = con.execute("SELECT COUNT(*) FROM plano_item WHERE lote IS NOT NULL OR estado NOT IN "
                         "('AGUARDANDO_APROVACAO','PENDENTE_DE_DECISAO','FORA_DO_ESCOPO','CONFLITO')").fetchone()[0]
    if em_uso:
        print(f"BLOQUEADO: {em_uso} itens ja estao em lotes/transferencia. O 333 nao pode reescrever o plano.")
        return 1
    ex_id = abrir_execucao(con, Path(__file__).name, {"arquivos": len(arqs)})
    res = Resolvedor(reais)
    quando = agora()
    linhas, plano = [], []
    for a in arqs:
        cl = classificar(a["caminho_rel"])
        nome, mudou = nome_final(a["nome"])
        destino_real, criar = (res.resolver(cl.destino_eap) if cl.destino_eap else ("", 0))
        if cl.escopo == "fora":
            estado = "FORA_DO_ESCOPO"
        elif cl.escopo == "pendente":
            estado = "PENDENTE_DE_DECISAO"
        else:
            estado = "AGUARDANDO_APROVACAO"
        obs = "; ".join(x for x in (cl.observacao, "nome normalizado para NFC" if mudou else "") if x)
        linhas.append({
            "item_id": a["item_id"], "origem": a["caminho_rel"], "bytes": a["tamanho"] or 0,
            "quick_xor_hash": a["quick_xor_hash"] or "", "modificado_em": a["modificado_em"],
            "estado": estado, "escopo": cl.escopo, "regra": cl.regra,
            "decisoes": ",".join(cl.decisoes), "destino_eap": cl.destino_eap or "",
            "destino_real": destino_real, "niveis_a_criar": criar, "nome_final": nome,
            "renomeia_arquivo": int(mudou), "conflito": "", "observacao": obs,
        })

    # ---- conflitos -------------------------------------------------------
    finais = [f"{l['destino_real']}/{l['nome_final']}" for l in linhas if l["destino_real"]]
    col = colisoes(finais)
    em_colisao = {p for grupo in col.values() for p in grupo}
    conflitos = []
    for l in linhas:
        if not l["destino_real"]:
            continue
        final = f"{l['destino_real']}/{l['nome_final']}"
        motivos = []
        if final in em_colisao and "{" not in final:
            motivos.append("colisao de caminho final")
        ja = arquivos_destino.get(final.casefold())
        if ja:
            motivos.append(f"ja existe no PARCEIRO ({ja['tamanho']} B; origem {l['bytes']} B)")
        for seg in final.split("/"):
            if "{" in seg:
                continue
            for pr in problemas_segmento(seg):
                if pr != "unicode nao normalizado (NFC)":
                    motivos.append(f"{seg[:40]}: {pr}")
        if motivos:
            l["conflito"] = " | ".join(motivos)
            if l["estado"] == "AGUARDANDO_APROVACAO":
                l["estado"] = "CONFLITO"
            conflitos.append((l["item_id"], l["origem"], final, l["conflito"]))

    # ---- persistir plano -------------------------------------------------
    con.execute("DELETE FROM plano_item")
    con.executemany(
        "INSERT INTO plano_item (item_id, estado, escopo, regra, categoria, destino_pasta, nome_final, "
        "conflito, atualizado_em, observacao) VALUES (?,?,?,?,?,?,?,?,?,?)",
        [(l["item_id"], l["estado"], l["escopo"], l["regra"], l["decisoes"], l["destino_real"],
          l["nome_final"], l["conflito"] or None, quando, l["observacao"] or None) for l in linhas])
    con.executemany(
        "INSERT INTO evento (item_id, quando, de_estado, para_estado, detalhe, execucao_id) "
        "VALUES (?,?,?,?,?,?)",
        [(l["item_id"], quando, "INVENTARIADO", l["estado"], l["regra"], ex_id) for l in linhas])
    con.commit()

    # ---- relatorios ------------------------------------------------------
    cab = list(linhas[0])
    escrever_csv(out / "matriz_preliminar.csv", cab, ([l[k] for k in cab] for l in linhas))

    por_pasta = defaultdict(lambda: [0, 0])
    for l in linhas:
        k = ("/".join(l["origem"].split("/")[:-1]), l["destino_real"], l["estado"], l["regra"], l["decisoes"])
        por_pasta[k][0] += 1
        por_pasta[k][1] += l["bytes"]
    escrever_csv(out / "matriz_por_pasta.csv",
                 ["pasta_origem", "pasta_destino", "estado", "regra", "decisoes", "arquivos", "bytes"],
                 (list(k) + v for k, v in sorted(por_pasta.items())))

    # pasta -> [arquivos abaixo, arquivos abaixo ainda pendentes de decisao]
    criar = defaultdict(lambda: [0, 0])
    for l in linhas:
        if l["niveis_a_criar"] and l["escopo"] != "fora":
            segs = l["destino_real"].split("/")
            for i in range(len(segs) - l["niveis_a_criar"] + 1, len(segs) + 1):
                criar["/".join(segs[:i])][0] += 1
                criar["/".join(segs[:i])][1] += l["escopo"] == "pendente"
    # so pode ser criada sem decisao a pasta sem arquivo pendente abaixo dela
    escrever_csv(out / "pastas_a_criar.csv",
                 ["pasta", "arquivos_abaixo", "pendentes_abaixo", "depende_de_decisao"],
                 ((p, n, pd, int(pd == n)) for p, (n, pd) in sorted(criar.items())))

    dec = defaultdict(lambda: [0, 0])
    for l in linhas:
        for d in filter(None, l["decisoes"].split(",")):
            dec[d][0] += 1
            dec[d][1] += l["bytes"]
    escrever_csv(out / "decisoes_pendentes.csv", ["codigo", "pergunta", "arquivos", "bytes", "GiB"],
                 ((d, DECISOES.get(d, ""), n, b, gib(b)) for d, (n, b) in sorted(dec.items())))
    escrever_csv(out / "conflitos.csv", ["item_id", "origem", "destino_final", "motivo"], conflitos)

    grupos = defaultdict(list)
    for a in arqs:
        if a["quick_xor_hash"]:
            grupos[(a["quick_xor_hash"], a["tamanho"])].append(a["caminho_rel"])
    dups = [(h, t, len(v), " | ".join(v)) for (h, t), v in grupos.items() if len(v) > 1]
    escrever_csv(out / "duplicatas_origem.csv", ["quick_xor_hash", "bytes", "copias", "caminhos"],
                 sorted(dups, key=lambda x: -x[1] * (x[2] - 1)))

    # ---- fora da pasta 5: os unicos movidos em 09/10 (decisao da Emmily, 19/10, secao 10.6) ----
    # Nao estao na origem; ficam registrados para nao serem confundidos com arquivo perdido.
    log_mov = RAIZ / "logs" / "movimentacao_parceiro.csv"
    fora_p5 = []
    if log_mov.exists():
        import csv
        movidos, revertidos = {}, set()
        with open(log_mov, encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f, delimiter=";"):
                if r["resultado"] != "OK":
                    continue
                if r["acao"] == "mover":
                    movidos[r["item_id"]] = r
                elif r["acao"] == "reverter":
                    revertidos.add(r["item_id"])
        fora_p5 = [(i, r["origem"], r["destino"], r["bytes"]) for i, r in movidos.items() if i not in revertidos]
    escrever_csv(out / "fora_do_escopo_movidos_0910.csv",
                 ["item_id", "caminho_na_pasta5_ate_0910", "caminho_atual", "bytes"], fora_p5)

    est = Counter()
    est_b = Counter()
    for l in linhas:
        est[l["estado"]] += 1
        est_b[l["estado"]] += l["bytes"]
    resumo = {"arquivos": len(linhas), "por_estado": dict(est),
              "pastas_a_criar": len(criar),
              "pastas_a_criar_sem_decisao": sum(pd < n for n, pd in criar.values()),
              "conflitos": len(conflitos), "colisoes": len(col), "decisoes": {d: v[0] for d, v in dec.items()},
              "fora_da_pasta5_movidos_0910": len(fora_p5)}
    fechar_execucao(con, ex_id, "concluida", resumo)

    total_b = sum(l["bytes"] for l in linhas)
    print(f"Plano: {num(len(linhas))} arquivos · {gib(total_b)} GiB")
    for e, n in est.most_common():
        print(f"  {e:24} {num(n):>7} arq · {gib(est_b[e]):>8} GiB")
    print(f"Pastas a criar: {num(len(criar))} ({num(resumo['pastas_a_criar_sem_decisao'])} sem depender de decisao)")
    print(f"Conflitos: {num(len(conflitos))} · colisoes de caminho: {num(len(col))}")
    print("Decisoes:", {d: f"{num(n)} arq/{gib(b)} GiB" for d, (n, b) in sorted(dec.items())})
    print(f"Fora do escopo (movidos da pasta 5 em 09/10, nao migrar): {num(len(fora_p5))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
