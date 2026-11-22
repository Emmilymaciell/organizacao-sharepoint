"""
320_parceiro_mover_unicos - MOVE os arquivos unicos da `5. Compartilhado PARCEIRO`
para o repositorio principal e apaga 4 pastas vazias autorizadas.

ESTE SCRIPT ESCREVE NO SHAREPOINT (PATCH = mover, POST = criar pasta,
DELETE = pasta vazia para a lixeira).

AUTORIZACAO: Emmily Maciel, nesta conversa, 09/10/2026:
  "PODE JÁ COLOCAR OS ARQUIVOS UNICOS (que nao existe no sharepoint atual e
   nos arquivos que transferimos) MOVER para onde vc acha que faz sentido,
   ai cria um excel dessa mudanca e salva em backup"
  "01. RD103, 02. RD102A, 03. RD101, 0. Geral/Diário de Obras esses sao as
   unicas pastas vazias que permito apagar o resto deixa"
  Correcao, durante a execucao: "produtos de 2025 nao precisa kkkk eu fiz
  backup bem antes" -> produtos de 2025 saem da lista; os ja movidos voltam
  para a origem (--reverter-produtos-2025) e as pastas de 2025 criadas
  por este script em `4. Produtos` vao para a lixeira, se estiverem vazias.

O QUE E "UNICO" AQUI (lista fechada, gerada a partir de
output/parceiro/PARCEIRO_UNICOS.csv do `300_parceiro_unicos.py`):
  - sem copia viva (mesmo quickXorHash) fora da pasta 5;
  - e cuja copia NAO foi para o OneDrive nem para o Google Drive nas
    transferencias de 30/09 e 01-02/10 (coluna `historico`);
  - SEM_HASH so entra se nao houver arquivo de mesmo nome e tamanho fora
    da pasta 5.

DESTINO (espelho): o arquivo vai para a copia do mesmo pacote no SharePoint
principal, no mesmo subcaminho relativo (RAIZES abaixo). Pasta que nao
existe e criada. Nome de arquivo so muda se ja houver outro arquivo de
mesmo nome no destino: ganha o sufixo " (PARCEIRO)".

TRAVAS:
  - antes de mover, GET do item: tem de estar no caminho e com o hash do
    catalogo; se nao, nao move e registra;
  - conflictBehavior=fail: nunca sobrescreve;
  - pasta vazia so e apagada se o GET confirmar 0 arquivos na subarvore;
  - log por item em logs/movimentacao_parceiro.csv (retomavel: item ja OK e
    pulado); `--simular` gera a planilha e nao escreve nada.

Saida: backup/MOVIMENTACAO_UNICOS_PARCEIRO_<data>.xlsx

Uso:
    python scripts\\320_parceiro_mover_unicos.py --simular
    python scripts\\320_parceiro_mover_unicos.py
    python scripts\\320_parceiro_mover_unicos.py --reverter-produtos-2025
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config_util import caminho_banco, ler_config  # noqa: E402
from graph_util import ErroGraph, Graph, GRAPH      # noqa: E402

try:
    sys.stdout.reconfigure(errors="replace")
except AttributeError:
    pass

RAIZ = Path(__file__).resolve().parents[1]
BRT = timezone(timedelta(hours=-3))
LOG = RAIZ / "logs" / "movimentacao_parceiro.csv"
LIXEIRA = RAIZ / "logs" / "lixeira.csv"
UNICOS = RAIZ / "output" / "parceiro" / "PARCEIRO_UNICOS.csv"
BACKUP = RAIZ / "backup"
P5 = "/5. Compartilhado PARCEIRO"
CI = "/2. Compartilhado interno"
OBRAS = f"{CI}/07_Resposta Prestação de Contas - Obras"

# pasta de 2o nivel da pasta 5 -> pasta-espelho no SharePoint principal
RAIZES = {
    "05. Produtos": "/4. Produtos",
    "8. Prestação de Contas Obras": OBRAS,
    "09. Prestação de Contas Obras - Set a Nov - 25": f"{OBRAS}/09. Prestação de Contas Obras - Set a Nov - 25",
    "5_Prestação de Contas (out a dez-25)": f"{OBRAS}/5_Prestação de Contas (out a dez-25)",
    "07_Prestação de Contas Trimestral - Programa": f"{CI}/04_Resposta Prestacao de Contas - Trimestral - Programa",
    "11. Prestação de Contas Mensal Programa": f"{CI}/05_Resposta Prestação de Contas  - Mensal - Programa",
    "10. Prestação de Contas Anual Programa": f"{CI}/08_Estruturação Governança/06_Resposta Prestação de Contas Anual - Programa",
    "14_Resposta MP 06 26": f"{CI}/11_MP-Estado/14_Resposta MP 03 06 26",
    "16_Resposta MP 19 06 26": f"{CI}/11_MP-Estado/16_Resposta MP 19 06 26",
    "17_Resposta TCE 06 10 26": f"{CI}/12_TCE-Estado/04_RD101 Resposta TCE 10-26",
}
# subpastas com destino proprio (prefixo relativo a pasta 5)
ESPECIAIS = {
    "0. Geral/Análise Prestação de Contas out a dez 2025":
        f"{OBRAS}/5_Prestação de Contas (out a dez-25)/Análise Prestação de Contas out a dez 2025",
    "0. Geral/REQUISICAO Nº 22026 - SERV-FISCINFRAENG":
        "/1. Gestão do Contrato/2. Correspondências/02 - Cartas/2026/REQUISICAO Nº 22026 - SERV-FISCINFRAENG",
    "10. Prestação de Contas Anual Programa/2025":
        f"{CI}/08_Estruturação Governança/06_Resposta Prestação de Contas Anual - Programa/2025/REV 1",
    "07_Prestação de Contas Trimestral - Programa/ABR A JUN - 2026":
        f"{CI}/04_Resposta Prestacao de Contas - Trimestral - Programa/ABR A JUN 2026",
}
# Produtos de 2025: a Emmily ja tem backup proprio - nao mover.
RE_PROD_2025 = re.compile(r"^/5\. Compartilhado PARCEIRO/05\. Produtos/[^/]*2025(/|$)")
VAZIAS = [f"{P5}/01. RD103", f"{P5}/02. RD102A", f"{P5}/03. RD101", f"{P5}/0. Geral/Diário de Obras"]

CAMPOS_LOG = ["quando", "acao", "item_id", "origem", "destino", "nome_final",
              "bytes", "resultado", "detalhe"]


# ----------------------------------------------------------------------
def destino_de(pasta_origem: str) -> tuple[str, str]:
    rel = pasta_origem[len(P5) + 1:]
    for pref in sorted(ESPECIAIS, key=len, reverse=True):
        if rel == pref or rel.startswith(pref + "/"):
            return ESPECIAIS[pref] + rel[len(pref):], "pasta especifica"
    n2, _, resto = rel.partition("/")
    if n2 in RAIZES:
        return RAIZES[n2] + ("/" + resto if resto else ""), "espelho do pacote"
    return "", "SEM DESTINO"


def montar_plano(con) -> list[dict]:
    fora = {r[0] for r in con.execute(
        "SELECT lower(nome)||'|'||tamanho FROM item_nuvem"
        " WHERE eh_pasta=0 AND deletado=0 AND nivel1<>'5. Compartilhado PARCEIRO'")}
    nomes_pasta: dict[str, set] = {}
    for pai, nome in con.execute(
            "SELECT pasta_pai, lower(nome) FROM item_nuvem WHERE eh_pasta=0 AND deletado=0"):
        nomes_pasta.setdefault(pai, set()).add(nome)
    pastas = {r[0] for r in con.execute(
        "SELECT caminho FROM item_nuvem WHERE eh_pasta=1 AND deletado=0")}
    plano = []
    with UNICOS.open(encoding="utf-8-sig") as fh:
        for x in csv.DictReader(fh, delimiter=";"):
            h = x["historico"]
            if "OneDrive" in h or "Google Drive" in h:
                continue
            if RE_PROD_2025.match(x["caminho"]):
                continue
            if x["classe"] == "SEM_HASH" and f"{x['nome'].lower()}|{x['bytes']}" in fora:
                continue
            origem_pasta = x["caminho"].rsplit("/", 1)[0]
            dest, regra = destino_de(origem_pasta)
            nome = x["nome"]
            conflito = ""
            usados = nomes_pasta.setdefault(dest, set())
            if nome.lower() in usados:
                base, ponto, ext = nome.rpartition(".")
                nome = f"{base} (PARCEIRO).{ext}" if ponto else f"{nome} (PARCEIRO)"
                conflito = "ja existia arquivo com o mesmo nome no destino"
            usados.add(nome.lower())
            plano.append({
                "item_id": x["item_id"], "nome_original": x["nome"], "nome_final": nome,
                "origem": x["caminho"], "pasta_destino": dest,
                "caminho_final": f"{dest}/{nome}", "bytes": int(x["bytes"]),
                "hash": x["quick_xor_hash"], "criado_em": x["criado_em"],
                "modificado_em": x["modificado_em"], "modificado_por": x["modificado_por"],
                "motivo_unico": h, "regra_destino": regra,
                "pasta_destino_existia": "sim" if dest in pastas else "criada",
                "conflito_nome": conflito, "web_url_antes": x["web_url"],
            })
    return plano


# ----------------------------------------------------------------------
class Escrita:
    """PATCH/POST/DELETE com nova tentativa em 429/503."""

    def __init__(self, g: Graph):
        self.g = g

    def chamar(self, metodo: str, caminho: str, corpo: dict | None = None) -> dict:
        url = GRAPH + caminho
        dados = json.dumps(corpo).encode() if corpo is not None else None
        for t in range(1, 6):
            req = urllib.request.Request(url, data=dados, method=metodo, headers={
                "Authorization": f"Bearer {self.g.token()}",
                "Content-Type": "application/json", "Accept": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=120) as r:
                    bruto = r.read()
                    return json.loads(bruto) if bruto else {}
            except urllib.error.HTTPError as exc:
                if exc.code in (429, 503, 504) and t < 5:
                    time.sleep(min(float(exc.headers.get("Retry-After", 5 * t)), 60))
                    continue
                raise RuntimeError(f"HTTP {exc.code}: {exc.read().decode('utf-8', 'replace')[:300]}")
        raise RuntimeError("tentativas esgotadas")


def item_por_caminho(g: Graph, did: str, caminho: str) -> dict | None:
    # codifica tudo (inclusive % e #) e passa URL completa, que o get nao recodifica
    try:
        return g.get(f"{GRAPH}/drives/{did}/root:{urllib.parse.quote(caminho, safe='/')}")
    except ErroGraph as exc:
        if exc.status == 404:
            return None
        raise


def garantir_pasta(g: Graph, w: Escrita, did: str, caminho: str, cache: dict,
                   criadas: list) -> str:
    if caminho in cache:
        return cache[caminho]
    item = item_por_caminho(g, did, caminho)
    if item is None:
        pai, nome = caminho.rsplit("/", 1)
        pai_id = garantir_pasta(g, w, did, pai, cache, criadas) if pai else \
            g.get(f"/drives/{did}/root")["id"]
        item = w.chamar("POST", f"/drives/{did}/items/{pai_id}/children",
                        {"name": nome, "folder": {}, "@microsoft.graph.conflictBehavior": "fail"})
        criadas.append({"pasta": caminho, "item_id": item["id"]})
        print(f"    + pasta criada: {caminho}")
    cache[caminho] = item["id"]
    return item["id"]


def arquivos_na_subarvore(g: Graph, did: str, item_id: str) -> int:
    n = 0
    for filho in g.paginar(f"/drives/{did}/items/{item_id}/children"):
        n += arquivos_na_subarvore(g, did, filho["id"]) if "folder" in filho else 1
    return n


def registrar(arq: Path, campos: list[str], linha: dict) -> None:
    arq.parent.mkdir(parents=True, exist_ok=True)
    novo = not arq.exists()
    with arq.open("a", encoding="utf-8-sig", newline="") as fh:
        wr = csv.DictWriter(fh, delimiter=";", fieldnames=campos)
        if novo:
            wr.writeheader()
        wr.writerow(linha)


def ler_log() -> list[dict]:
    if not LOG.exists():
        return []
    with LOG.open(encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh, delimiter=";"))


def ja_movidos() -> set[str]:
    linhas = ler_log()
    revertidos = {r["item_id"] for r in linhas if r["acao"] == "reverter" and r["resultado"] == "OK"}
    return {r["item_id"] for r in linhas
            if r["acao"] == "mover" and r["resultado"] == "OK"} - revertidos


def reverter_produtos_2025(con) -> int:
    """Devolve a origem os produtos de 2025 ja movidos e apaga as pastas de
    2025 criadas em `4. Produtos` (so se vazias e se nao existiam antes)."""
    linhas = ler_log()
    feitos = {r["item_id"] for r in linhas if r["acao"] == "reverter" and r["resultado"] == "OK"}
    alvos = [r for r in linhas if r["acao"] == "mover" and r["resultado"] == "OK"
             and RE_PROD_2025.match(r["origem"]) and r["item_id"] not in feitos]
    existiam = {r[0] for r in con.execute(
        "SELECT caminho FROM item_nuvem WHERE eh_pasta=1 AND deletado=0")}
    g = Graph()
    w = Escrita(g)
    did = [d for d in g.bibliotecas(g.site_id()) if d["name"] == "Programa"][0]["id"]
    print(f"reverter {len(alvos)} produto(s) de 2025")
    cache: dict[str, str] = {}
    ok = 0
    for i, r in enumerate(alvos, 1):
        pasta_orig, nome_orig = r["origem"].rsplit("/", 1)
        try:
            if pasta_orig not in cache:
                item = item_por_caminho(g, did, pasta_orig)
                if item is None:
                    raise RuntimeError(f"pasta de origem sumiu: {pasta_orig}")
                cache[pasta_orig] = item["id"]
            w.chamar("PATCH", f"/drives/{did}/items/{r['item_id']}?@microsoft.graph.conflictBehavior=fail",
                     {"parentReference": {"id": cache[pasta_orig]}, "name": nome_orig})
            res, det = "OK", ""
            ok += 1
        except Exception as exc:
            res, det = "ERRO", str(exc)[:300]
        registrar(LOG, CAMPOS_LOG, {"quando": datetime.now(BRT).isoformat(), "acao": "reverter",
                                    "item_id": r["item_id"], "origem": r["destino"] + "/" + r["nome_final"],
                                    "destino": pasta_orig, "nome_final": nome_orig, "bytes": r["bytes"],
                                    "resultado": res, "detalhe": det})
        if res != "OK" or i % 50 == 0:
            print(f"  [{i:>3}/{len(alvos)}] {res} {nome_orig[:60]} {det[:80]}")
    meses = sorted({"/".join(r["destino"].split("/")[:3]) for r in linhas
                    if r["acao"] == "mover" and RE_PROD_2025.match(r["origem"])})
    for m in meses:
        reg = {"quando": datetime.now(BRT).isoformat(), "frente": "parceiro_reversao", "caminho": m,
               "item_id": "", "arquivos": 0, "bytes": 0, "resultado": "", "detalhe": ""}
        if not m.startswith("/4. Produtos/") or m in existiam:
            reg.update(resultado="MANTIDA", detalhe="ja existia antes do script")
        else:
            item = item_por_caminho(g, did, m)
            if item is None:
                reg.update(resultado="NAO_ENCONTRADA")
            else:
                qtd = arquivos_na_subarvore(g, did, item["id"])
                reg.update(item_id=item["id"], arquivos=qtd)
                if qtd:
                    reg.update(resultado="RECUSADA", detalhe=f"{qtd} arquivo(s) dentro")
                else:
                    w.chamar("DELETE", f"/drives/{did}/items/{item['id']}")
                    reg.update(resultado="OK", detalhe="pasta criada pelo script, vazia -> lixeira")
        registrar(LIXEIRA, ["quando", "frente", "caminho", "item_id", "arquivos", "bytes",
                            "resultado", "detalhe"], reg)
        print(f"  pasta {reg['resultado']:>14}  {m}")
    print(f"revertidos {ok}/{len(alvos)}")
    return 0 if ok == len(alvos) else 2


# ----------------------------------------------------------------------
def gravar_excel(plano, resultados, criadas, vazias, simulacao: bool) -> Path:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    BACKUP.mkdir(exist_ok=True)
    agora = datetime.now(BRT)
    arq = BACKUP / f"MOVIMENTACAO_UNICOS_PARCEIRO_{agora:%Y-%m-%d}{'_SIMULACAO' if simulacao else ''}.xlsx"
    h = Font(name="Arial", bold=True, color="FFFFFF", size=10)
    hf = PatternFill("solid", fgColor="1F4E78")
    n = Font(name="Arial", size=10)
    wb = Workbook()

    ws = wb.active
    ws.title = "Resumo"
    ok = sum(1 for p in plano if resultados.get(p["item_id"], ("",))[0] == "OK")
    linhas = [
        ("Movimentação dos arquivos únicos da pasta 5. Compartilhado PARCEIRO", True),
        (f"{'SIMULAÇÃO — nada foi movido' if simulacao else 'Executado'} em {agora:%d/%m/%Y %H:%M}", False),
        ("Autorização: Emmily Maciel, por escrito na conversa de 09/10/2026.", False),
        ("", False),
        (f"Arquivos na lista: {len(plano)}  ·  {sum(p['bytes'] for p in plano) / 1024**3:.2f} GiB".replace(".", ","), False),
        (f"Movidos com sucesso: {ok}", False),
        (f"Pastas criadas: {len(criadas)}", False),
        (f"Pastas vazias apagadas (lixeira do site): {sum(1 for v in vazias if v['resultado'] == 'OK')}", False),
        ("", False),
        ("Critério de único: sem cópia (mesmo hash) no SharePoint atual e sem cópia nas", False),
        ("transferências para OneDrive (30/09) e Google Drive (01–02/10).", False),
        ("Critério de destino: a cópia do mesmo pacote no SharePoint principal, no mesmo subcaminho.", False),
        ("", False),
        ("Como desfazer", True),
        ("Cada linha da aba 'Arquivos' traz o item_id (não muda ao mover) e o caminho de origem.", False),
        ("Para voltar um arquivo: mover o item_id de volta para a pasta de origem.", False),
        ("Pastas apagadas estão na lixeira do site e se restauram por lá.", False),
    ]
    for i, (t, neg) in enumerate(linhas, 1):
        ws.cell(i, 1, t).font = Font(name="Arial", size=11 if neg else 10, bold=neg)
    ws.column_dimensions["A"].width = 110

    cab = ["nº", "item_id", "nome_original", "nome_final", "origem", "pasta_destino",
           "caminho_final", "bytes", "hash", "criado_em", "modificado_em", "modificado_por",
           "motivo_unico", "regra_destino", "pasta_destino_existia", "conflito_nome",
           "resultado", "detalhe", "web_url_antes"]
    wa = wb.create_sheet("Arquivos")
    for j, c in enumerate(cab, 1):
        cel = wa.cell(1, j, c)
        cel.font, cel.fill = h, hf
    for i, p in enumerate(plano, 2):
        res, det = resultados.get(p["item_id"], ("SIMULADO" if simulacao else "", ""))
        vals = [i - 1] + [p[c] for c in cab[1:16]] + [res, det, p["web_url_antes"]]
        for j, v in enumerate(vals, 1):
            wa.cell(i, j, v).font = n
    larg = [6, 36, 40, 40, 70, 70, 70, 14, 30, 22, 22, 22, 40, 18, 12, 30, 12, 40, 50]
    for j, wd in enumerate(larg, 1):
        wa.column_dimensions[wa.cell(1, j).column_letter].width = wd
    wa.freeze_panes = "C2"
    wa.auto_filter.ref = wa.dimensions

    wp = wb.create_sheet("Pastas criadas")
    for j, c in enumerate(["pasta", "item_id"], 1):
        cel = wp.cell(1, j, c)
        cel.font, cel.fill = h, hf
    fonte = criadas if not simulacao else [
        {"pasta": d, "item_id": "(seria criada)"}
        for d in sorted({p["pasta_destino"] for p in plano if p["pasta_destino_existia"] == "criada"})]
    for i, c in enumerate(fonte, 2):
        wp.cell(i, 1, c["pasta"]).font = n
        wp.cell(i, 2, c["item_id"]).font = n
    wp.column_dimensions["A"].width = 120
    wp.column_dimensions["B"].width = 40

    wv = wb.create_sheet("Pastas vazias apagadas")
    for j, c in enumerate(["pasta", "item_id", "arquivos_na_subarvore", "resultado", "detalhe"], 1):
        cel = wv.cell(1, j, c)
        cel.font, cel.fill = h, hf
    for i, v in enumerate(vazias, 2):
        for j, c in enumerate(["pasta", "item_id", "arquivos", "resultado", "detalhe"], 1):
            wv.cell(i, j, v.get(c, "")).font = n
    wv.column_dimensions["A"].width = 60
    wv.column_dimensions["B"].width = 40
    for col in "CDE":
        wv.column_dimensions[col].width = 22
    for s in wb.worksheets:
        for row in s.iter_rows():
            for c in row:
                c.alignment = Alignment(vertical="top")
    wb.save(arq)
    return arq


# ----------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--simular", action="store_true")
    ap.add_argument("--reverter-produtos-2025", action="store_true")
    args = ap.parse_args()

    cfg = ler_config()
    con = sqlite3.connect(f"file:{caminho_banco(cfg).as_posix()}?mode=ro", uri=True)
    if args.reverter_produtos_2025:
        return reverter_produtos_2025(con)
    plano = montar_plano(con)
    sem = [p for p in plano if not p["pasta_destino"]]
    if sem:
        print(f"ABORTADO: {len(sem)} arquivo(s) sem destino.")
        for p in sem[:5]:
            print("   ", p["origem"])
        return 1
    if any(not p["pasta_destino"].startswith(("/1.", "/2.", "/3.", "/4.")) for p in plano):
        print("ABORTADO: destino fora do repositorio principal.")
        return 1

    tot = sum(p["bytes"] for p in plano)
    print(f"{len(plano)} arquivos · {tot / 1024**3:.2f} GiB · "
          f"{len({p['pasta_destino'] for p in plano})} pastas de destino · "
          f"{sum(1 for p in plano if p['conflito_nome'])} renomeados por conflito")

    if args.simular:
        arq = gravar_excel(plano, {}, [], [{"pasta": v, "resultado": "SIMULADO"} for v in VAZIAS], True)
        print(f"SIMULACAO — nada foi escrito. Planilha: {arq}")
        return 0

    g = Graph()
    w = Escrita(g)
    did = [d for d in g.bibliotecas(g.site_id()) if d["name"] == "Programa"][0]["id"]
    feitos = ja_movidos()
    cache: dict[str, str] = {}
    criadas: list[dict] = []
    resultados: dict[str, tuple[str, str]] = {}

    for i, p in enumerate(plano, 1):
        iid = p["item_id"]
        quando = datetime.now(BRT).isoformat()
        if iid in feitos:
            resultados[iid] = ("OK", "movido em execucao anterior")
            continue
        try:
            atual = g.get(f"/drives/{did}/items/{iid}")
            cam_atual = urllib.parse.unquote(
                atual["parentReference"]["path"].split("root:", 1)[1]) + "/" + atual["name"]
            hash_atual = atual.get("file", {}).get("hashes", {}).get("quickXorHash", "")
            if cam_atual != p["origem"] or (p["hash"] and hash_atual != p["hash"]):
                raise RuntimeError(f"item mudou desde o catalogo: {cam_atual}")
            pid = garantir_pasta(g, w, did, p["pasta_destino"], cache, criadas)
            corpo = {"parentReference": {"id": pid}}
            if p["nome_final"] != p["nome_original"]:
                corpo["name"] = p["nome_final"]
            try:
                w.chamar("PATCH", f"/drives/{did}/items/{iid}?@microsoft.graph.conflictBehavior=fail", corpo)
                resultados[iid] = ("OK", "")
            except RuntimeError as exc:
                if "nameAlreadyExists" not in str(exc) or p["conflito_nome"]:
                    raise
                base, ponto, ext = p["nome_original"].rpartition(".")
                p["nome_final"] = f"{base} (PARCEIRO).{ext}" if ponto else f"{p['nome_original']} (PARCEIRO)"
                p["caminho_final"] = f"{p['pasta_destino']}/{p['nome_final']}"
                p["conflito_nome"] = "nome ja existia no destino (detectado pelo SharePoint)"
                corpo["name"] = p["nome_final"]
                w.chamar("PATCH", f"/drives/{did}/items/{iid}?@microsoft.graph.conflictBehavior=fail", corpo)
                resultados[iid] = ("OK", "renomeado: nome ja existia no destino")
        except Exception as exc:                                   # REGRA 5
            resultados[iid] = ("ERRO", str(exc)[:300])
        res, det = resultados[iid]
        registrar(LOG, CAMPOS_LOG, {"quando": quando, "acao": "mover", "item_id": iid,
                                    "origem": p["origem"], "destino": p["pasta_destino"],
                                    "nome_final": p["nome_final"], "bytes": p["bytes"],
                                    "resultado": res, "detalhe": det})
        if res != "OK" or i % 50 == 0:
            print(f"  [{i:>3}/{len(plano)}] {res} {p['nome_original'][:60]} {det[:80]}")
    for c in criadas:
        registrar(LOG, CAMPOS_LOG, {"quando": datetime.now(BRT).isoformat(), "acao": "criar_pasta",
                                    "item_id": c["item_id"], "origem": "", "destino": c["pasta"],
                                    "nome_final": "", "bytes": 0, "resultado": "OK", "detalhe": ""})

    # ---- pastas vazias autorizadas ------------------------------------
    vazias = []
    for v in VAZIAS:
        reg = {"pasta": v, "item_id": "", "arquivos": "", "resultado": "", "detalhe": ""}
        try:
            item = item_por_caminho(g, did, v)
            if item is None:
                reg.update(resultado="NAO_ENCONTRADA")
            else:
                reg["item_id"] = item["id"]
                qtd = arquivos_na_subarvore(g, did, item["id"])
                reg["arquivos"] = qtd
                if qtd:
                    reg.update(resultado="RECUSADA", detalhe=f"{qtd} arquivo(s) dentro")
                else:
                    w.chamar("DELETE", f"/drives/{did}/items/{item['id']}")
                    reg.update(resultado="OK", detalhe="pasta vazia -> lixeira do site")
        except Exception as exc:
            reg.update(resultado="ERRO", detalhe=str(exc)[:300])
        vazias.append(reg)
        print(f"  vazia {reg['resultado']:>14}  {v}")
        if reg["resultado"] in ("OK", "NAO_ENCONTRADA", "RECUSADA", "ERRO"):
            registrar(LIXEIRA, ["quando", "frente", "caminho", "item_id", "arquivos", "bytes",
                                "resultado", "detalhe"],
                      {"quando": datetime.now(BRT).isoformat(), "frente": "parceiro_vazias",
                       "caminho": v, "item_id": reg["item_id"], "arquivos": reg["arquivos"] or 0,
                       "bytes": 0, "resultado": reg["resultado"], "detalhe": reg["detalhe"]})

    ok = sum(1 for r, _ in resultados.values() if r == "OK")
    arq = gravar_excel(plano, resultados, criadas, vazias, False)
    print(f"movidos {ok}/{len(plano)} · pastas criadas {len(criadas)} · planilha {arq}")
    return 0 if ok == len(plano) else 2


if __name__ == "__main__":
    raise SystemExit(main())
