"""
379_g2_reorganizar_preexistentes - MOVE para o lugar da EAP os arquivos que os estagiarios/terceiros puseram fora
do lugar nas arvores 04. Engenharia. Autorizacao escrita da Emmily, 30/10/2026 (~13:50): "PERMITO VC MOVER PROS
LUGARES CERTOS DE FORMA A RESPEITAR A EAP ... MOVA TUDO QUE FOR NECESSARIO DE ORGANIZACAO DOS ESTAGIARIOS E
REGISTRE EM RELATORIO TB TUDO QUE FOI MOVIDO".

So MOVE: nada e apagado, renomeado ou sobrescrito. Entrada: a auditoria 375 (auditoria_<GO>/10_PREEXISTENTES_PARCEIRO.csv).
Move somente o que tem destino COMPROVADO pela EAP:
  conformidade_localizacao = NAO_CONFORME, correspondencia IDENTICO_A_ORIGEM (QuickXor igual ao da origem) e
  classificacao JA_EXISTIA_EM_OUTRO_DESTINO (o arquivo do estagiario e a UNICA copia daquele documento na arvore;
  pasta_recomendada = destino calculado pelo planejador 366 para o item de origem).
Nao move (fica e vai para o relatorio): conteudo diferente da origem, sem correspondencia, nao aprovado, sem
evidencia, fora do escopo, ou documento cuja copia correta ja esta no lugar da EAP (JA_EXISTIA_CORRETO: mover
criaria duplicata no destino).
Cada movimento: PROPFIND da origem (mesmo fileid e etag da auditoria; senao PRESERVADO_ALTERADO) -> destino tem de
estar livre -> pastas da EAP criadas com MKCOL (trava escrita_permitida: nunca Eventograma, RPFO/DUP so pasta) ->
MOVE com Overwrite: F (412 se o destino passou a existir) -> PROPFIND confirma o mesmo fileid no destino.
Pastas que ficarem vazias NAO sao apagadas (so listadas).

Saida: output/migracao_parceiro_g2/MOVIMENTACOES_PREEXISTENTES_G2[_<ts>].csv (todas as linhas, inclusive nao movidas)
       tabela movimentacao no banco G2.
Uso:   python scripts/379_g2_reorganizar_preexistentes.py [--rodovia RD-102A] [--executar] [--workers 4]
"""

from __future__ import annotations

import argparse
import re
import csv
import sys
import threading
import unicodedata
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "scripts"))

from migra_g2.base import agora, cfg, conectar, escrever_csv, num, saida  # noqa: E402

SQL_TAB = """CREATE TABLE IF NOT EXISTS movimentacao (
    em TEXT NOT NULL, rodovia TEXT, arquivo TEXT, de TEXT, para TEXT, fileid TEXT, etag TEXT, tamanho INTEGER,
    resultado TEXT, http INTEGER, motivo TEXT)"""
CAB = ["em", "rodovia", "arquivo", "de_pasta", "para_pasta", "fileid", "etag", "tamanho", "resultado", "http", "motivo"]
MOVER = ("IDENTICO_A_ORIGEM",)


RAMOS_EAP = {"01. Projetos/01. Anteprojeto", "01. Projetos/02. Executivo",
             "01. Projetos/03. RPFO (Revisão de Projeto em Fase de Obra)", "02. Boletim de Medição",
             "03. Licenciamento Ambiental", "04. Desapropriação", "05. Diário de Obras", "06. Relatório de Obras",
             "07. Não Conformidades", "08. Segurança do Trabalho", "09. Eventograma"}


def nfc(s):
    return unicodedata.normalize("NFC", s or "")


def ramo(pasta: str) -> str:
    """Ramo da EAP: 2 niveis em 01. Projetos, 1 nivel nos demais."""
    seg = nfc(pasta).split("/")
    return "/".join(seg[:2]) if seg[0] == "01. Projetos" else seg[0]


def candidatos(out: Path, go: str):
    p = out / f"auditoria_{go}" / "10_PREEXISTENTES_PARCEIRO.csv"
    if not p.exists():
        return [], []
    mover, ficar = [], []
    with open(p, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f, delimiter=";"):
            if r["conformidade_localizacao"] != "NAO_CONFORME":
                continue
            ok = (r["correspondencia_origem"] in MOVER and r["classificacao_eap"] == "JA_EXISTIA_EM_OUTRO_DESTINO"
                  and r["pasta_recomendada"] and nfc(r["pasta_recomendada"]) != nfc(r["pasta_encontrada"]))
            if ok and ramo(r["pasta_encontrada"]) in RAMOS_EAP and ramo(r["pasta_encontrada"]) != ramo(r["pasta_recomendada"]):
                # ja esta num ramo VALIDO da EAP diferente (ex.: Licenca x BM): o mesmo documento pertence aos dois;
                # tirar de um ramo valido seria desorganizar
                r["_motivo"] = "documento esta em outro ramo valido da EAP (tambem pertence a ele): nao movido entre ramos"
                ok = False
            (mover if ok else ficar).append(r)
    return mover, ficar


RE_MES = re.compile(r"\d{4}-\d{2} - [^/]+")


def mes_do_estagiario(de: str, para: str) -> str:
    """BM: o mesmo documento (mesmo hash) aparece em varios meses; o mes certo e o da pasta em que o estagiario o
    pos, nao o do 1o item da origem com aquele hash (correcao de 30/10/2026). Fora do BM: para inalterado."""
    m = RE_MES.search(de)
    if not de.startswith("02. Boletim de Medição") or not m or not RE_MES.search(para):
        return para
    return RE_MES.sub(m.group(0), para, count=1)


def corrigir(a, c, cexec, con, nc) -> list:
    """2a passada (30/10/2026): (1) BM movido para mes diferente do da pasta do estagiario -> mes dele; (2) BM que
    ficou em DESTINO_OCUPADO por ir para o mes errado -> mes dele; (3) NAO_MOVIDO (423 no MKCOL) -> nova tentativa."""
    reg = []
    linhas = con.execute("SELECT rowid, rodovia, arquivo, de, para, fileid, etag, tamanho, resultado FROM movimentacao"
                         " WHERE resultado IN ('MOVIDO', 'DESTINO_OCUPADO', 'NAO_MOVIDO')").fetchall()
    for rowid, go, nome, de, para, fid, et, tam, res in linhas:
        if a.rodovia and go != a.rodovia:
            continue
        raiz = nfc(c["destino"]["arvores"][go])
        certo = mes_do_estagiario(de, para)
        if res == "MOVIDO":
            if certo == para:
                continue
            src, de_atual = f"{raiz}/{para}/{nome}", para
        elif res == "DESTINO_OCUPADO" and certo == para:
            continue
        else:
            src, de_atual = f"{raiz}/{de}/{nome}", de
        dst = f"{raiz}/{certo}/{nome}"
        base = (go, nome, de_atual, certo, fid, et, tam)
        if not a.executar:
            reg.append((agora(), *base, "SIMULADO", None, f"2a passada ({res})"))
            continue
        e = nc.estado(src)
        if e is None or e.eh_pasta or e.fileid != fid:
            reg.append((agora(), *base, "PRESERVADO_ALTERADO", None, f"2a passada ({res}): origem sumiu/mudou"))
            continue
        if nc.estado(dst) is not None:
            reg.append((agora(), *base, "DESTINO_OCUPADO", None, f"2a passada ({res}): ja ha item no destino; mantido"))
            continue
        atual = raiz
        for seg in certo.split("/"):
            atual = f"{atual}/{seg}"
            if nc.criar_pasta(atual, cexec) == "CRIADA":
                reg.append((agora(), go, "(pasta)", "", atual[len(raiz) + 1:], None, None, None, "PASTA_CRIADA", 201,
                            "2a passada"))
        nc._exigir(dst, False, cexec)
        st, _ = nc._req_ret("MOVE", nc.url_arquivo(src), {"Destination": nc.url_arquivo(dst), "Overwrite": "F"})
        v = nc.estado(dst)
        ok = st in (201, 204) and v is not None and v.fileid == fid
        reg.append((agora(), *base, "MOVIDO" if ok else "NAO_MOVIDO", st,
                    f"2a passada ({res}): " + ("BM no mes da pasta do estagiario" if certo != para else "nova tentativa")))
        if ok:
            con.execute("UPDATE movimentacao SET resultado=resultado||'_CORRIGIDO' WHERE rowid=?", (rowid,))
        con.execute("INSERT INTO movimentacao VALUES (?,?,?,?,?,?,?,?,?,?,?)", reg[-1])
        con.commit()
    return reg


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rodovia")
    ap.add_argument("--executar", action="store_true")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--corrigir", action="store_true", help="2a passada: mes do BM e novas tentativas")
    a = ap.parse_args()
    if a.corrigir:
        sys.stdout.reconfigure(encoding="utf-8")
        c = cfg()
        con = conectar()
        con.execute("PRAGMA busy_timeout = 900000")
        from migra_g2.credencial import parceiro
        from migra_g2.governador import baixar_prioridade_propria
        baixar_prioridade_propria()
        reg = corrigir(a, c, {**c, "fase": "execucao"}, con, parceiro(c))
        arq = saida(c) / ("MOVIMENTACOES_PREEXISTENTES_G2_CORRECAO" + ("" if a.executar else "_SIMULACAO") + ".csv")
        escrever_csv(arq, CAB, reg)
        print(dict(Counter((x[1], x[8]) for x in reg)))
        print(f"-> {arq}")
        return 0
    sys.stdout.reconfigure(encoding="utf-8")
    c = cfg()
    cexec = {**c, "fase": "execucao"}           # so para a trava de escopo; a autorizacao e a desta operacao
    out = saida(c)
    con = conectar()
    con.execute("PRAGMA busy_timeout = 900000")
    con.execute(SQL_TAB)
    from migra_g2.credencial import parceiro
    from migra_g2.governador import baixar_prioridade_propria
    print("prioridade:", baixar_prioridade_propria())
    nc = parceiro(c)
    reg = []
    trava_pastas = threading.Lock()
    pastas_ok: set[str] = set()

    def garantir(raiz: str, rel_pasta: str, go: str):
        atual = raiz
        for seg in rel_pasta.split("/"):
            atual = f"{atual}/{seg}"
            with trava_pastas:
                if atual in pastas_ok:
                    continue
            res = nc.criar_pasta(atual, cexec)
            with trava_pastas:
                pastas_ok.add(atual)
            if res == "CRIADA":
                reg.append((agora(), go, "(pasta)", "", atual[len(raiz) + 1:], None, None, None, "PASTA_CRIADA",
                            201, "pasta da EAP criada para receber os arquivos movidos"))

    def mover(go: str, raiz: str, r: dict):
        nome, de, para = nfc(r["nome_encontrado"]), nfc(r["pasta_encontrada"]), nfc(r["pasta_recomendada"])
        src, dst = f"{raiz}/{de}/{nome}", f"{raiz}/{para}/{nome}"
        base = (go, nome, de, para, r["fileid"], r["etag"], int(r["tamanho"] or 0))
        e = nc.estado(src)
        if e is None or e.eh_pasta or e.fileid != r["fileid"] or (e.etag or "") != r["etag"]:
            return (agora(), *base, "PRESERVADO_ALTERADO", None, "origem sumiu ou mudou desde a auditoria: nao movido")
        d = nc.estado(dst)
        if d is not None:
            return (agora(), *base, "DESTINO_OCUPADO", None,
                    f"ja ha item no destino (fileid {d.fileid}): nada sobrescrito, arquivo mantido onde estava")
        try:
            nc._exigir(dst, False, cexec)
            garantir(raiz, para, go)
        except Exception as ex:                                     # trava ou MKCOL recusado
            return (agora(), *base, "NAO_MOVIDO", None, f"trava/pasta: {ex}"[:300])
        st, _ = nc._req_ret("MOVE", nc.url_arquivo(src), {"Destination": nc.url_arquivo(dst), "Overwrite": "F"})
        if st not in (201, 204):
            return (agora(), *base, "NAO_MOVIDO", st, "MOVE recusado (412 = destino passou a existir)")
        v = nc.estado(dst)
        ok = v is not None and v.fileid == r["fileid"] and nc.estado(src) is None
        return (agora(), *base, "MOVIDO" if ok else "MOVIDO_VERIFICAR", st,
                "movido para o lugar da EAP (mesmo arquivo, mesmo nome)" if ok else "MOVE respondeu ok mas a "
                "conferencia nao bateu: verificar")

    tot = Counter()
    for go, raiz in c["destino"]["arvores"].items():
        if a.rodovia and go != a.rodovia:
            continue
        raiz = nfc(raiz)
        mv, fica = candidatos(out, go)
        print(f"{go}: mover {num(len(mv))} | ficam (sem destino comprovado) {num(len(fica))}")
        for r in fica:
            motivo = r.get("_motivo") or {"SEM_CORRESPONDENCIA": "sem documento correspondente na origem: destino da EAP indeterminado",
                      "MESMO_NOME_CONTEUDO_DIFERENTE": "conteudo diferente do documento de origem (outra versao)"
                      }.get(r["correspondencia_origem"]) or {
                "NAO_APROVADO": "revisao nao aprovada: nao pertence a EAP", "SEM_EVIDENCIA": "sem evidencia de entrega/"
                "aprovacao", "FORA_DO_ESCOPO": "fora do escopo da EAP", "JA_EXISTIA_CORRETO": "copia correta ja esta "
                "no lugar da EAP: mover criaria duplicata"}.get(r["classificacao_eap"], "destino nao comprovado")
            reg.append((agora(), go, nfc(r["nome_encontrado"]), nfc(r["pasta_encontrada"]),
                        nfc(r["pasta_recomendada"]), r["fileid"], r["etag"], int(r["tamanho"] or 0), "MANTIDO", None,
                        motivo))
        if not a.executar:
            for r in mv:
                reg.append((agora(), go, nfc(r["nome_encontrado"]), nfc(r["pasta_encontrada"]),
                            nfc(r["pasta_recomendada"]), r["fileid"], r["etag"], int(r["tamanho"] or 0), "SIMULADO",
                            None, "seria movido"))
            continue
        feitos = 0
        with ThreadPoolExecutor(max_workers=max(1, a.workers)) as pool:
            for fut in as_completed([pool.submit(mover, go, raiz, r) for r in mv]):
                try:
                    linha = fut.result()
                except Exception as ex:
                    linha = (agora(), go, "?", "", "", None, None, None, "ERRO", None, str(ex)[:300])
                reg.append(linha)
                con.execute("INSERT INTO movimentacao VALUES (?,?,?,?,?,?,?,?,?,?,?)", linha)
                feitos += 1
                tot[(go, linha[8])] += 1
                if feitos % 100 == 0:
                    con.commit()
                    print(f"  {go}: {feitos}/{len(mv)} {dict(Counter(x[8] for x in reg if x[1] == go))}", flush=True)
        con.commit()
    for x in reg:
        if x[8] == "PASTA_CRIADA" and a.executar:
            con.execute("INSERT INTO pasta_criada (caminho, motivo, criada_em, fileid, resultado) VALUES (?,?,?,?,?)",
                        (f"{c['destino']['arvores'][x[1]]}/{x[4]}", "379 reorganizacao preexistentes", x[0], None,
                         "CRIADA"))
    con.commit()
    nome = "MOVIMENTACOES_PREEXISTENTES_G2" + ("" if a.executar else "_SIMULACAO") + ".csv"
    arq = out / nome
    if a.executar and arq.exists():
        arq = arq.with_name(arq.stem + f"_{agora()[:19].replace(':', '')}.csv")
    escrever_csv(arq, CAB, reg)
    print(dict(Counter((x[1], x[8]) for x in reg)))
    print(f"-> {arq}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
