"""
375_g2_preexistentes - auditoria SO LEITURA dos arquivos que JA ESTAVAM no PARCEIRO (nao criados pelo motor) nas
arvores Engenharia: o que os estagiarios/terceiros subiram. Nada e alterado, movido, renomeado ou apagado
(decisao da Emmily, 29/10/2026): o resultado e so para decisao humana posterior.

Para cada arquivo preexistente da foto mais recente do PARCEIRO (362), dentro da arvore da rodovia:
  - correspondencia com a origem (SharePoint G2, inventario mais recente):
      IDENTICO_A_ORIGEM            QuickXor lido do PARCEIRO = QuickXor da Microsoft (prova de conteudo)
      MESMO_NOME_E_TAMANHO         sem hash lido (orcamento de leitura), nome e tamanho iguais: provavel
      MESMO_NOME_CONTEUDO_DIFERENTE nome igual, tamanho diferente: outra revisao/versao
      SEM_CORRESPONDENCIA          nada na origem com esse conteudo/nome
  - o que a EAP exige para esse documento: destino do plano (366) do item de origem correspondente, e a
    classificacao dele (elegivel, nao aprovado, sem evidencia, fora do escopo);
  - conformidade de nomenclatura, localizacao e revisao; duplicidade dentro do PARCEIRO; problema, evidencia,
    correcao recomendada (NAO executada), prioridade, decisao humana.
Autoria: o Nextcloud so informa o dono do armazenamento (oc:owner-display-name), nao quem subiu. Nada e
atribuido a estagiario: "preexistente" (ja estava na foto-base desta etapa) ou "alteracao externa" (surgiu
depois).

Leitura de hash (para provar conteudo igual) e streaming do PARCEIRO, governada (cede ao projeto prioritario), so
para arquivos com nome+tamanho de algum item da origem e dentro do orcamento --max-gib-hash.

Uso: python scripts/375_g2_preexistentes.py --rodovia RD-101 [--foto-base N] [--max-gib-hash 20]
Saida: output/migracao_parceiro_g2/auditoria_<GO>/10_PREEXISTENTES_PARCEIRO.csv e 10_PREEXISTENTES_RESUMO.json
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "scripts"))

from migra_g2.base import agora, cfg, conectar, escrever_csv, gib, num, saida  # noqa: E402

_spec = importlib.util.spec_from_file_location("auditoria", RAIZ / "scripts" / "371_g2_auditoria.py")
AU = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(AU)

CAB = ["rodovia", "origem_do_arquivo", "nome_encontrado", "pasta_encontrada", "fileid", "etag", "tamanho",
       "modificado", "correspondencia_origem", "origem_caminho", "origem_item_id", "classificacao_eap",
       "nome_recomendado", "pasta_recomendada", "conformidade_nomenclatura", "conformidade_localizacao",
       "conformidade_revisao", "duplicado_no_parceiro", "problema", "evidencia", "correcao_recomendada", "prioridade",
       "decisao_humana"]


def nfc(s):
    return unicodedata.normalize("NFC", s or "")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rodovia", required=True)
    ap.add_argument("--foto-base", type=int, help="foto que define 'preexistente' nesta etapa (padrao: a 1a foto "
                    "completa de hoje ou a mais antiga)")
    ap.add_argument("--max-gib-hash", type=float, default=20.0)
    a = ap.parse_args()
    c = cfg()
    from migra_g2.governador import baixar_prioridade_propria, esperar_folga
    print("prioridade:", baixar_prioridade_propria())
    con = conectar()
    go, raiz = a.rodovia, c["destino"]["arvores"][a.rodovia]
    foto = con.execute("SELECT MAX(id) FROM parceiro_foto WHERE fim IS NOT NULL").fetchone()[0]
    base = a.foto_base or 1
    inv = con.execute("SELECT MAX(id) FROM execucao WHERE script='363_g2_inventario_origem' AND estado='concluida'"
                      ).fetchone()[0]
    nossos = {r[0] for r in con.execute("SELECT fileid_destino FROM plano WHERE fileid_destino IS NOT NULL")}
    na_base = {r[0] for r in con.execute("SELECT fileid FROM parceiro_item WHERE foto_id=? AND eh_pasta=0", (base,))}
    parceiro = [dict(r) for r in con.execute("SELECT * FROM parceiro_item WHERE foto_id=? AND eh_pasta=0 AND caminho LIKE ?",
                                         (foto, raiz + "/%")) if r["fileid"] not in nossos]
    pref = go.replace("-", "")
    origem = [dict(r) for r in con.execute(
        "SELECT * FROM origem_item WHERE eh_pasta=0 AND execucao_id=? AND ((fonte='Programa' AND caminho LIKE ?) OR fonte=?)",
        (inv, f"{pref}/%", f"BM_{pref}"))]
    plano = {(r["drive_id"], r["item_id"]): dict(r) for r in con.execute("SELECT * FROM plano WHERE rodovia=?", (go,))}
    por_hash, por_nome_tam, por_nome = defaultdict(list), defaultdict(list), defaultdict(list)
    for o in origem:
        if o["quick_xor_hash"]:
            por_hash[o["quick_xor_hash"]].append(o)
        por_nome_tam[(nfc(o["nome"]).lower(), o["tamanho"])].append(o)
        por_nome[nfc(o["nome"]).lower()].append(o)
    hashes = {(r["fileid"], r["etag"]): r["quick_xor"] for r in con.execute("SELECT * FROM parceiro_hash")}

    # leitura de hash: so nome+tamanho iguais a um item da origem, ate o orcamento
    falta = [r for r in parceiro if (r["fileid"], r["etag"]) not in hashes
             and (nfc(r["caminho"].rsplit("/", 1)[1]).lower(), r["tamanho"]) in por_nome_tam and (r["tamanho"] or 0) > 0]
    falta.sort(key=lambda r: r["tamanho"])
    orc, lidos = a.max_gib_hash * 2**30, 0
    if falta:
        from migra_g2.credencial import parceiro as cliente
        from migra_parceiro.quickxor import QuickXorHash
        nc = cliente(c)
        print(f"{len(falta)} arquivo(s) com nome+tamanho da origem sem hash; lendo ate {a.max_gib_hash} GiB ...")
        for r in falta:
            if lidos + r["tamanho"] > orc:
                break
            esperar_folga(c.get("recursos"))
            q = QuickXorHash()
            try:
                for b in nc.ler_stream(r["caminho"]):
                    q.update(b)
            except Exception as e:  # noqa: BLE001 (regra 5: registra e segue)
                print(f"  falha ao ler {r['caminho'][-90:]}: {type(e).__name__}")
                continue
            hashes[(r["fileid"], r["etag"])] = q.base64()
            con.execute("INSERT OR REPLACE INTO parceiro_hash (fileid, etag, caminho, tamanho, quick_xor, lido_em)"
                        " VALUES (?,?,?,?,?,?)", (r["fileid"], r["etag"], r["caminho"], r["tamanho"], q.base64(), agora()))
            con.commit()
            lidos += r["tamanho"]

    # estrutura da arvore x EAP (mesma regra da auditoria da RD-103)
    estrutura = {f"{raiz}/{ln[1]}": (ln[2], ln[3]) for ln in AU.auditar_estrutura(con, go, raiz, foto)}
    hash_parceiro = Counter(hashes.get((r["fileid"], r["etag"])) for r in parceiro if hashes.get((r["fileid"], r["etag"])))
    nome_tam_parceiro = Counter((nfc(r["caminho"].rsplit("/", 1)[1]).lower(), r["tamanho"]) for r in parceiro)

    linhas, cont = [], Counter()
    for r in sorted(parceiro, key=lambda x: x["caminho"]):
        cam = nfc(r["caminho"])
        pasta_, nome = cam.rsplit("/", 1)
        h = hashes.get((r["fileid"], r["etag"]))
        cand, corr = [], "SEM_CORRESPONDENCIA"
        if h and por_hash.get(h):
            cand, corr = por_hash[h], "IDENTICO_A_ORIGEM"
        elif por_nome_tam.get((nome.lower(), r["tamanho"])):
            cand = por_nome_tam[(nome.lower(), r["tamanho"])]
            corr = "MESMO_NOME_E_TAMANHO" if not h else "MESMO_NOME_E_TAMANHO_HASH_DIFERENTE"
        elif por_nome.get(nome.lower()):
            cand, corr = por_nome[nome.lower()], "MESMO_NOME_CONTEUDO_DIFERENTE"
        # entre os candidatos, o que tem destino na EAP (elegivel ou ja existente) primeiro
        cand = sorted(cand, key=lambda o: (plano.get((o["drive_id"], o["item_id"]), {}).get("destino_rel") is None,
                                           o["caminho"]))
        o = cand[0] if cand else None
        pl = plano.get((o["drive_id"], o["item_id"])) if o else None
        cls = pl["classificacao"] if pl else ("NAO_PLANEJADO" if o else "")
        dest = nfc(pl["destino_rel"]) if pl and pl["destino_rel"] else ""
        nome_rec, pasta_rec = (dest.rsplit("/", 1)[1], dest.rsplit("/", 1)[0]) if dest else ("", "")
        n_ok = ("CONFORME" if nome == nome_rec else "NAO_CONFORME") if nome_rec else "INDETERMINADO"
        if pasta_rec:
            l_ok = "CONFORME" if pasta_ == pasta_rec else "NAO_CONFORME"
        else:
            l_ok = {"CONFORME": "CONFORME", "NAO_CONFORME": "NAO_CONFORME"}.get(estrutura.get(pasta_, ("",))[0],
                                                                               "INDETERMINADO")
        rev = {"AUSENTE_E_ELEGIVEL": "CONFORME", "JA_EXISTIA_EM_OUTRO_DESTINO": "CONFORME",
               "EQUIVALENCIA_INCERTA": "CONFORME", "NAO_APROVADO": "NAO_CONFORME",
               "SEM_EVIDENCIA": "INDETERMINADO", "FORA_DO_ESCOPO": "NAO_CONFORME"}.get(cls, "INDETERMINADO")
        dup = (h and hash_parceiro[h] > 1) or nome_tam_parceiro[(nome.lower(), r["tamanho"])] > 1
        origem_arq = "PREEXISTENTE" if r["fileid"] in na_base else "ALTERACAO_EXTERNA (surgiu depois da foto-base)"
        problemas, corr_rec, prio = [], [], "BAIXA"
        if cls == "NAO_APROVADO":
            problemas.append("revisao NAO aprovada (ou com ressalva) presente no PARCEIRO")
            corr_rec.append("avaliar retirada: a nota 02 admite so a revisao final aprovada sem ressalva")
            prio = "ALTA"
        elif cls == "FORA_DO_ESCOPO":
            problemas.append(f"material fora do escopo da EAP ({pl['motivo'][:80]})")
            corr_rec.append("avaliar retirada ou realocacao para fora de 04. Engenharia")
            prio = "MEDIA"
        elif cls == "SEM_EVIDENCIA":
            problemas.append("sem evidencia de entrega/aprovacao nos controles oficiais")
            corr_rec.append("confirmar entrega/aprovacao; sem ela, avaliar retirada")
            prio = "MEDIA"
        if n_ok == "NAO_CONFORME":
            problemas.append("nome diferente do documento de origem")
            corr_rec.append(f"renomear para '{nome_rec}'")
            prio = max(prio, "MEDIA", key=["BAIXA", "MEDIA", "ALTA"].index)
        if l_ok == "NAO_CONFORME":
            problemas.append("pasta fora do lugar previsto na EAP")
            corr_rec.append(f"mover para '{pasta_rec[len(raiz) + 1:]}'" if pasta_rec else
                            "mover para a estrutura da EAP (pasta atual nao existe na EAP)")
            prio = max(prio, "MEDIA", key=["BAIXA", "MEDIA", "ALTA"].index)
        if dup:
            problemas.append("duplicado dentro do PARCEIRO (mesmo conteudo ou mesmo nome+tamanho em outra pasta)")
            corr_rec.append("manter uma copia, na pasta da EAP")
        if corr == "MESMO_NOME_CONTEUDO_DIFERENTE":
            problemas.append("mesmo nome da origem com conteudo diferente (outra revisao/versao?)")
            corr_rec.append("confirmar qual versao vale")
            prio = max(prio, "MEDIA", key=["BAIXA", "MEDIA", "ALTA"].index)
        if corr == "SEM_CORRESPONDENCIA":
            problemas.append("sem correspondencia na origem G2 (nao comprova entrega)")
            corr_rec.append("identificar a origem do documento")
        ev = []
        if o:
            ev.append(f"origem {o['caminho']} (item {o['item_id']}, {o['tamanho']} B, QuickXor {o['quick_xor_hash']})")
        if h:
            ev.append(f"QuickXor lido do PARCEIRO {h}")
        if pl:
            ev.append(f"plano: {cls} - {(pl['motivo'] or '')[:120]}")
        ev.append(f"estrutura da pasta: {estrutura.get(pasta_, ('nao avaliada',))[0]}")
        decisao = "SIM" if problemas else "NAO"
        cont[(corr, "COM_PROBLEMA" if problemas else "OK")] += 1
        linhas.append((go, origem_arq, nome, pasta_[len(raiz) + 1:], r["fileid"], r["etag"], r["tamanho"],
                       r["modificado"], corr, o["caminho"] if o else "", o["item_id"] if o else "", cls,
                       nome_rec, pasta_rec[len(raiz) + 1:] if pasta_rec else "", n_ok, l_ok, rev,
                       "SIM" if dup else "NAO", "; ".join(problemas), " | ".join(ev), "; ".join(corr_rec),
                       prio if problemas else "", decisao))

    dest = saida(c) / f"auditoria_{go}"
    dest.mkdir(parents=True, exist_ok=True)
    out = dest / "10_PREEXISTENTES_PARCEIRO.csv"
    escrever_csv(out, CAB, linhas)
    resumo = {"quando": agora(), "rodovia": go, "foto": foto, "foto_base": base, "inventario": inv,
              "preexistentes": len(linhas), "bytes": sum(r["tamanho"] or 0 for r in parceiro),
              "hash_lidos_gib": round(lidos / 2**30, 2), "sem_hash_por_orcamento": max(0, len(falta) - sum(
                  1 for r in falta if (r["fileid"], r["etag"]) in hashes)),
              "por_correspondencia": {f"{k[0]}|{k[1]}": v for k, v in sorted(cont.items())},
              "com_problema": sum(1 for ln in linhas if ln[-1] == "SIM"),
              "nao_aprovados": sum(1 for ln in linhas if ln[11] == "NAO_APROVADO"),
              "nome_nao_conforme": sum(1 for ln in linhas if ln[14] == "NAO_CONFORME"),
              "local_nao_conforme": sum(1 for ln in linhas if ln[15] == "NAO_CONFORME"),
              "duplicados": sum(1 for ln in linhas if ln[17] == "SIM")}
    (dest / "10_PREEXISTENTES_RESUMO.json").write_text(json.dumps(resumo, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{go}: {num(len(linhas))} preexistentes ({gib(resumo['bytes'])} GiB) | com problema "
          f"{num(resumo['com_problema'])} | nome NC {num(resumo['nome_nao_conforme'])} | local NC "
          f"{num(resumo['local_nao_conforme'])} | nao aprovados {num(resumo['nao_aprovados'])} | dup "
          f"{num(resumo['duplicados'])} | hash lido {resumo['hash_lidos_gib']} GiB -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
