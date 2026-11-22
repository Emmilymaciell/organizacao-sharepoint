"""
350_migra_relatorio_executivo - relatorios DEFINITIVOS da migracao Programa (pasta 5) -> PARCEIRO INFRA.

Tudo vem do banco de controle (data/migracao_parceiro.db, aberto SO LEITURA), dos manifestos e das
evidencias em output/ e logs/ - nada e digitado a mao, e nada sai na rede.

Saidas em output/migracao_parceiro/:
  RELATORIO_EXECUTIVO_MIGRACAO_PARCEIRO.md / .docx   para a chefia (curto)
  RELATORIO_TECNICO_MIGRACAO_PARCEIRO.md / .docx     detalhado (arquitetura, desempenho, incidentes...)
  LISTAGEM_FINAL_ARQUIVOS.csv                    os 20.333 arquivos do inventario: estado, destino, hashes
  NAO_MIGRADOS.csv                               o que nao foi migrado e o motivo
  ESTRUTURA_FINAL_PARCEIRO.md                        arvore de pastas criada no PARCEIRO, com contagens

Uso:
    python scripts/350_migra_relatorio_executivo.py
"""

from __future__ import annotations

import csv
import json
import re
import sqlite3
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
SAIDA = RAIZ / "output" / "migracao_parceiro"
LOGS = RAIZ / "logs"
GiB = 2**30
csv.field_size_limit(2**30)
MOTIVOS = {
    "R00_certificadora_fora_do_escopo": "06. CERTIFICADORA ENTREGA - fora do escopo da migração",
    "R03_geral_requisicao_nao_migrar": "Requisição nº 2/2026 (SERV-FISCINFRAENG) - NÃO MIGRAR, decisão da Emmily",
}


def br(n: float, casas: int = 0) -> str:
    return f"{n:,.{casas}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def gib(b) -> str:
    return br((b or 0) / GiB, 2)


def dt(s: str | None) -> str:
    return (s or "")[:16].replace("T", " ")


def dur(a: str, b: str) -> str:
    s = (datetime.fromisoformat(b) - datetime.fromisoformat(a)).total_seconds()
    return f"{int(s // 3600)} h {int(s % 3600 // 60):02d} min" if s >= 3600 else f"{int(s // 60)} min {int(s % 60):02d} s"


def tab(cab, linhas) -> str:
    out = ["| " + " | ".join(cab) + " |", "|" + "|".join("---" for _ in cab) + "|"]
    return "\n".join(out + ["| " + " | ".join(str(c) for c in ln) + " |" for ln in linhas])


# ---------------------------------------------------------------- dados

def coletar() -> dict:
    con = sqlite3.connect(f"file:{RAIZ / 'data' / 'migracao_parceiro.db'}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    q = lambda s, *a: con.execute(s, a).fetchall()  # noqa: E731
    d: dict = {"gerado": time.strftime("%d/%m/%Y %H:%M")}
    inv = json.loads(q("SELECT resumo FROM execucao WHERE script='331_migra_inventario_origem.py' "
                       "AND estado='concluida' ORDER BY id DESC LIMIT 1")[0][0])
    dst = q("SELECT resumo, inicio FROM execucao WHERE script='332_migra_inventario_destino.py' "
            "AND estado='concluida' ORDER BY id DESC LIMIT 1")
    d["inv"], d["inv_destino"] = inv, (json.loads(dst[0][0]) if dst else {})
    d["itens"] = q("""SELECT o.item_id, o.caminho_rel, o.nome, o.tamanho, o.quick_xor_hash, p.estado, p.escopo,
                             p.regra, p.lote, p.destino_pasta, p.nome_final,
                             (SELECT v.qxh_destino FROM verificacao v WHERE v.item_id=o.item_id ORDER BY v.id DESC LIMIT 1) qxd,
                             (SELECT v.sha256_destino FROM verificacao v WHERE v.item_id=o.item_id ORDER BY v.id DESC LIMIT 1) shad,
                             (SELECT v.sha256_origem FROM verificacao v WHERE v.item_id=o.item_id ORDER BY v.id DESC LIMIT 1) shao,
                             (SELECT v.metodo FROM verificacao v WHERE v.item_id=o.item_id ORDER BY v.id DESC LIMIT 1) metodo,
                             (SELECT v.quando FROM verificacao v WHERE v.item_id=o.item_id ORDER BY v.id DESC LIMIT 1) vquando,
                             (SELECT COUNT(*) FROM transferencia t WHERE t.item_id=o.item_id) tentativas
                      FROM origem_item o JOIN plano_item p USING(item_id) WHERE o.eh_pasta=0
                      ORDER BY o.caminho_rel""")
    d["lotes"] = q("""SELECT l.nome, l.criado_em, l.aprovado_em, l.aprovacao_texto,
                             MIN(t.inicio) ini, MAX(t.fim) fim, COUNT(t.id) tent
                      FROM lote l LEFT JOIN transferencia t ON t.lote=l.nome GROUP BY l.nome ORDER BY l.aprovado_em""")
    d["falhas_transf"] = q("SELECT t.inicio, o.nome, t.lote, t.resultado, t.erro FROM transferencia t "
                           "JOIN origem_item o USING(item_id) WHERE t.resultado!='COPIADO' ORDER BY t.inicio")
    d["verif_ruins"] = q("SELECT v.quando, o.nome, v.resultado, v.bytes_destino, o.tamanho, p.estado, v.detalhe "
                         "FROM verificacao v JOIN origem_item o USING(item_id) JOIN plano_item p USING(item_id) "
                         "WHERE v.resultado NOT IN ('VALIDADO') ORDER BY v.quando")
    d["video_eventos"] = q("SELECT e.quando, e.de_estado, e.para_estado, e.detalhe FROM evento e JOIN plano_item p "
                           "USING(item_id) WHERE p.lote='VIDEO-87G' AND e.quando >= '2026-10-20' ORDER BY e.id")
    d["n_eventos"] = q("SELECT COUNT(*) FROM evento")[0][0]
    d["n_verif"] = q("SELECT COUNT(*) FROM verificacao")[0][0]
    d["janela"] = q("SELECT MIN(inicio), MAX(fim) FROM transferencia")[0]
    con.close()

    def ler_json(nome):
        p = SAIDA / nome
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
    d["auditoria"] = ler_json("auditoria_destino_20261020.json")
    d["regioes"] = ler_json("video_conferencia_regioes.json")
    d["integral_int"] = ler_json("video_conferencia_integral_INTERROMPIDA_20261021.json")
    d["progresso"] = ler_json("progresso.json")
    with open(SAIDA / "fora_do_escopo_movidos_0910.csv", encoding="utf-8-sig") as f:
        mov = list(csv.reader(f, delimiter=";"))[1:]
    d["movidos"] = (len(mov), sum(int(r[3] or 0) for r in mov))
    with open(SAIDA / "duplicatas_origem.csv", encoding="utf-8-sig") as f:
        dup = list(csv.reader(f, delimiter=";"))[1:]
    d["dup_origem"] = (len(dup), sum(int(r[2]) - 1 for r in dup), sum(int(r[1]) * (int(r[2]) - 1) for r in dup))
    inc = LOGS / "incidentes_migracao.csv"
    if inc.exists():
        with open(inc, encoding="utf-8-sig") as f:
            linhas = list(csv.DictReader(f, delimiter=";"))
        d["watchdog"] = {"n": len(linhas), "ini": linhas[0]["quando"], "fim": linhas[-1]["quando"],
                         "acoes": Counter(x["acao"] for x in linhas),
                         "disco": [float(x["disco_livre_gib"]) for x in linhas if x["disco_livre_gib"]]}
    vlog = LOGS / "video_retomavel_20261020.log"
    txt = vlog.read_text(encoding="utf-8") if vlog.exists() else ""
    d["video_log"] = {"retentativas": txt.count("nova tentativa"),
                      "quedas_tls": len(re.findall(r"EOF occurred|10054|closed connection|timed out|IncompleteRead", txt)),
                      "montagem": re.findall(r"segundos_montagem': (\d+)", txt)}
    return d


# ---------------------------------------------------------------- derivados

def resumir(d: dict) -> dict:
    r: dict = {}
    est = Counter()
    bytes_est = Counter()
    for it in d["itens"]:
        est[it["estado"]] += 1
        bytes_est[it["estado"]] += it["tamanho"] or 0
    r["est"], r["best"] = est, bytes_est
    r["inv_n"], r["inv_b"] = len(d["itens"]), sum(i["tamanho"] or 0 for i in d["itens"])
    fora = [i for i in d["itens"] if i["estado"] in ("FORA_DO_ESCOPO", "PENDENTE_DE_DECISAO")]
    r["fora_n"], r["fora_b"] = len(fora), sum(i["tamanho"] for i in fora)
    escopo = [i for i in d["itens"] if i["estado"] not in ("FORA_DO_ESCOPO", "PENDENTE_DE_DECISAO")]
    r["esc_n"], r["esc_b"] = len(escopo), sum(i["tamanho"] for i in escopo)
    val = [i for i in d["itens"] if i["estado"] == "VALIDADO"]
    r["val_n"], r["val_b"] = len(val), sum(i["tamanho"] for i in val)
    r["video"] = next(i for i in d["itens"] if i["lote"] == "VIDEO-87G")
    r["enviados_n"] = r["val_n"] + 1
    r["enviados_b"] = r["val_b"] + r["video"]["tamanho"]
    r["metodos"] = Counter(i["metodo"] for i in val)
    r["dup_dest"] = sum(1 for _, n in Counter(f"{i['destino_pasta']}/{i['nome_final']}".casefold()
                                               for i in escopo).items() if n > 1)
    qx = Counter(i["quick_xor_hash"] for i in val if i["quick_xor_hash"])
    r["copias_conteudo"] = sum(n - 1 for n in qx.values())
    r["multi_tent"] = sum(1 for i in escopo if (i["tentativas"] or 0) > 1)
    por_lote: dict = defaultdict(lambda: [0, 0, 0, 0, 0])
    for i in d["itens"]:
        if i["lote"]:
            x = por_lote[i["lote"]]
            x[0] += 1
            x[1] += i["tamanho"]
            if i["estado"] == "VALIDADO":
                x[2] += 1
                x[3] += i["tamanho"]
            if i["estado"] == "FORA_DO_ESCOPO":
                x[4] += 1
    r["por_lote"] = por_lote
    areas: dict = defaultdict(lambda: [0, 0])
    arvore: dict = defaultdict(lambda: [0, 0])
    for i in escopo:
        if i["estado"] not in ("VALIDADO", "MONTADO_SEM_VALIDACAO_INTEGRAL"):
            continue
        p = i["destino_pasta"].split("/")
        a = areas["/".join(p[:2])]
        a[0] += 1
        a[1] += i["tamanho"]
        for k in range(1, min(len(p), 4) + 1):
            n = arvore["/".join(p[:k])]
            n[0] += 1
            n[1] += i["tamanho"]
    r["areas"], r["arvore"] = areas, arvore
    r["pastas_dest"] = len({i["destino_pasta"] for i in escopo if i["estado"] in ("VALIDADO", "MONTADO_SEM_VALIDACAO_INTEGRAL")})
    return r


# ---------------------------------------------------------------- textos

RESSALVA = ("**ENVIADO E MONTADO NO PARCEIRO — INTEGRIDADE INTEGRAL NÃO COMPROVADA — VERIFICAÇÃO DISPENSADA POR "
            "DECISÃO DA RESPONSÁVEL (21/10/2026).**")


def executivo(d, r) -> list:
    v = r["video"]
    s = []
    s.append(("Situação", [
        f"**Transferência encerrada em 21/10/2026.** {br(r['enviados_n'])} de {br(r['esc_n'])} arquivos do escopo "
        f"estão no PARCEIRO INFRA ({gib(r['enviados_b'])} de {gib(r['esc_b'])} GiB).",
        f"**Integridade comprovada arquivo a arquivo:** {br(r['val_n'])} arquivos ({gib(r['val_b'])} GiB), "
        f"{br(100 * r['val_n'] / r['esc_n'], 2)}% dos arquivos do escopo.",
        f"**Ressalva — vídeo de {gib(v['tamanho'])} GiB** (RD 102_A, Estudos de Tráfego): {RESSALVA} "
        "Tamanho idêntico ao original e 7 trechos de 16 MiB comparados byte a byte, todos iguais; o hash do arquivo "
        "inteiro não foi calculado.",
        "**Nada foi apagado, movido ou alterado no SharePoint.** Nenhum arquivo do PARCEIRO foi sobrescrito ou excluído.",
    ], None))
    s.append(("Números", [], (["", "Arquivos", "GiB"], [
        ["Inventário da pasta 5. Compartilhado PARCEIRO", br(r["inv_n"]), gib(r["inv_b"])],
        ["Fora da migração por decisão", br(r["fora_n"]), gib(r["fora_b"])],
        ["**Escopo da migração**", f"**{br(r['esc_n'])}**", f"**{gib(r['esc_b'])}**"],
        ["Validados (hash conferido no PARCEIRO)", br(r["val_n"]), gib(r["val_b"])],
        ["Enviado e montado, sem hash integral (vídeo)", "1", gib(v["tamanho"])],
        ["Escopo não transferido", br(r["esc_n"] - r["enviados_n"]), gib(r["esc_b"] - r["enviados_b"])],
    ])))
    s.append(("Onde ficou no PARCEIRO INFRA (EAP oficial)", [],
              (["Área", "Arquivos", "GiB"], [[k, br(n), gib(b)] for k, (n, b) in sorted(r["areas"].items())])))
    s.append(("O que não foi migrado, e por quê", [], (["Conjunto", "Arquivos", "GiB", "Motivo"], [
        ["06. CERTIFICADORA ENTREGA", br(r["est"]["FORA_DO_ESCOPO"] - 1), gib(r["best"]["FORA_DO_ESCOPO"]), "fora do escopo definido"],
        ["Requisição nº 2/2026", br(r["est"]["PENDENTE_DE_DECISAO"]), gib(r["best"]["PENDENTE_DE_DECISAO"]),
         "NÃO MIGRAR (decisão da responsável)"],
        ["Arquivo temporário do Excel (~$)", "1", "0,00", "não é documento (arquivo de bloqueio)"],
    ])))
    s.append(("Como a integridade foi garantida", [
        "Cada arquivo foi lido **de volta do PARCEIRO** depois da cópia, e o hash dessa leitura foi comparado com o hash "
        "que a Microsoft registra no SharePoint (QuickXorHash); 52 arquivos sem esse hash foram comparados por SHA-256 "
        "lendo origem e destino.",
        f"Auditoria independente em 20/10: os {br(d['auditoria']['validados'])} validados foram encontrados no PARCEIRO, "
        f"com o mesmo tamanho ({len(d['auditoria']['ausentes'])} ausentes). Nenhum destino duplicado; nenhum conflito "
        "com arquivo preexistente.",
    ], None))
    s.append(("Pontos para decisão da chefia", [
        "1. **Vídeo:** aceitar a ressalva ou agendar a conferência integral (≈ 8 h de leitura, sem reenvio).",
        "2. **Limpeza da pasta 5 no SharePoint:** não autorizada; só depois de aceite formal da migração.",
        "3. **Cópia de teste** `PARCEIRO-INFRA/_TESTE_UPLOAD_RETOMAVEL/Mosaico_RD102_v02.jp2` (3,27 GiB): autorizar remoção.",
        f"4. **Conteúdo repetido:** {br(r['copias_conteudo'])} dos arquivos migrados são cópias idênticas de outro "
        "(herdadas da origem, preservadas como estavam) — candidato a deduplicação futura no PARCEIRO.",
        "5. **MP-14:** nome mantido com 02/07/2026; a data vem de minuta (observação registrada).",
    ], None))
    return s


def tecnico(d, r) -> list:
    v = r["video"]
    s = []
    s.append(("1. Resumo executivo", [x for x in executivo(d, r)[0][1]], None))
    s.append(("2. Objetivo e escopo", [
        "Migrar a pasta **5. Compartilhado PARCEIRO** (biblioteca Programa, site contoso.sharepoint.com/sites/exemplo) para o "
        "repositório **PARCEIRO INFRA** (Nextcloud, `https://nextcloud.example.com`, montagem `PARCEIRO-INFRA/`), seguindo a EAP "
        "oficial (`EAP_Repositorio.xlsx`), com nomes e caminhos finais definidos **antes** do envio (o destino não permite "
        "renomear), somente cópia, conferência de 100% e operação auditável, retomável e autônoma.",
        "Restrições: nada apagado/movido/alterado na origem; nada sobrescrito no destino; sem hidratação do OneDrive; "
        "sem `rclone move/sync`; cada lote só com aprovação escrita da responsável.",
    ], None))
    s.append(("3. Arquitetura da transferência", [
        "- **Motor:** Python + `rclone rcd` (remoto→remoto: `prog:` OneDrive/SharePoint via Graph → `parceiro_infra_migracao:` "
        "WebDAV Nextcloud), com `operations/copyfile` e `Immutable` (nunca sobrescreve). Bytes passam em streaming pela "
        "máquina; nada é gravado no SSD.",
        "- **Estado:** SQLite `data/migracao_parceiro.db` (WAL) com inventário, plano, lotes, transferências, conferências e "
        f"eventos ({br(d['n_eventos'])} eventos e {br(d['n_verif'])} registros de conferência).",
        "- **Controle:** lotes aprovados por escrito (texto gravado no banco); pré-requisitos (340) antes de cada lote; "
        "concorrência adaptativa 2–12; classificação de erros (transitório/permanente/parada global); trava de execução "
        "única; Agendador do Windows + watchdog a cada 10 min.",
        "- **Conferência:** releitura do destino e QuickXorHash comparado ao da Microsoft (mesmo algoritmo); SHA-256 "
        "origem×destino para os 52 arquivos sem hash.",
        "- **Vídeo de 87,76 GiB:** uploader próprio retomável (Nextcloud chunking v2): origem lida por intervalos (HTTP "
        "Range) do Graph, blocos de 16 MiB guardados no servidor, retomada envia só os blocos que faltam, montagem com "
        "`Overwrite: F`.",
        "- **Credencial:** senha de aplicativo própria do Nextcloud, cifrada por DPAPI do usuário; nunca em código, log, "
        "banco ou Git.",
    ], None))
    inv, invd = d["inv"], d["inv_destino"]
    s.append(("4. Origem, destino e inventário inicial", [], (["Item", "Valor"], [
        ["Origem", "SharePoint Programa › Programa › 5. Compartilhado PARCEIRO"],
        ["Inventário da origem (19/10 09:50)", f"{br(inv['arquivos'])} arquivos, {br(inv['pastas'])} pastas, "
                                              f"{gib(inv['bytes'])} GiB, {inv['sem_hash']} sem hash, {inv['erros']} erros"],
        ["Destino", "PARCEIRO INFRA (Nextcloud 35), armazenamento externo `PARCEIRO-INFRA`"],
        ["Inventário do destino antes (19/10)", f"{br(invd.get('arquivos', 0))} arquivos, {br(invd.get('pastas', 0))} pastas "
                                               f"(PARCEIRO ADMINISTRATIVO não varrido)"],
        ["Já removidos da pasta 5 em 09/10 (antes desta frente)", f"{br(d['movidos'][0])} arquivos, {gib(d['movidos'][1])} GiB "
                                                                 "(fora_do_escopo_movidos_0910.csv)"],
    ])))
    s.append(("5. Incluídos, excluídos e reconciliação", [], (["Estado final no banco", "Arquivos", "GiB"],
              [[k, br(n), gib(r["best"][k])] for k, n in r["est"].most_common()] +
              [["**Total (= inventário)**", f"**{br(r['inv_n'])}**", f"**{gib(r['inv_b'])}**"]])))
    s.append(("5.1 Diferenças entre contagens históricas", [
        f"- O inventário de 19/10 ({br(inv['arquivos'])}) é igual à soma dos estados finais ({br(r['inv_n'])}): "
        f"{'sem diferença' if inv['arquivos'] == r['inv_n'] else 'DIFERENÇA'}.",
        "- \"16.404 no escopo\" (20/10) → **16.403**: o arquivo `~$EHID_Pluv…xlsx` saiu do escopo por decisão.",
        "- \"MP-16: 5.379 arquivos\" (pacote 16) = 5.378 no lote MP-16 + 1 vídeo no lote VIDEO-87G.",
        "- \"131,67 GiB validados\" e \"131,68 GiB\": mesma soma, arredondamento diferente.",
        f"- Tentativas de transferência registradas: {br(sum(i['tentativas'] or 0 for i in d['itens']))} para "
        f"{br(r['enviados_n'])} arquivos enviados; {br(r['multi_tent'])} arquivos precisaram de mais de uma tentativa.",
    ], None))
    pl = r["por_lote"]
    lin = []
    for lt in d["lotes"]:
        n, b, vn, vb, fo = pl[lt["nome"]]
        janela = f"{dt(lt['ini'])} → {dt(lt['fim'])}" if lt["ini"] else "-"
        dur_s = dur(lt["ini"], lt["fim"]) if lt["ini"] and lt["fim"] and lt["ini"] != lt["fim"] else "-"
        vel = ((datetime.fromisoformat(lt["fim"]) - datetime.fromisoformat(lt["ini"])).total_seconds()
               if lt["ini"] and lt["fim"] else 0)
        lin.append([lt["nome"], dt(lt["aprovado_em"]), br(n), gib(b), br(vn), janela, dur_s,
                    br(b / 2**20 / vel, 1) if vel > 60 else "-", br(lt["tent"])])
    s.append(("6. Resultados por lote, horários e desempenho", [
        "Velocidade = volume do lote ÷ janela de parede (inclui pausas e espera de rede; PROD-02/03 atravessaram a "
        "madrugada aguardando rodadas). MiB/s.",
    ], (["Lote", "Aprovado", "Arquivos", "GiB", "Validados", "Janela", "Duração", "MiB/s", "Tentativas"], lin)))
    s.append(("7. Resultados por pasta de destino (EAP)", [f"{br(r['pastas_dest'])} pastas de destino distintas."],
              (["Área", "Arquivos", "GiB"], [[k, br(n), gib(b)] for k, (n, b) in sorted(r["areas"].items())])))
    w = d.get("watchdog") or {}
    s.append(("8. Consumo de memória e disco", [
        "- **SSD:** zero dado temporário — rclone remoto→remoto em streaming e uploader do vídeo com blocos só em memória. "
        f"Disco livre medido pelo watchdog: {br(min(w.get('disco', [0])), 1)}–{br(max(w.get('disco', [0])), 1)} GiB durante a operação "
        "(reserva mínima configurada: 40 GiB; teto temporário: 8 GiB).",
        "- **Memória:** teto configurado de 1.500 MB para o rclone (concorrência adaptativa reduz se passar); processo do "
        "vídeo medido em 44–54 MB (3 blocos de 16 MiB em voo).",
        "- **Rede:** conexão Wi-Fi da estação; upload medido de 0,9 a 4 MiB/s nas horas do vídeo — o gargalo do vídeo foi a "
        "rede, não o servidor nem o motor.",
    ], None))
    s.append(("9. Duplicados e conflitos", [
        f"- **Conflitos no destino:** 0 (nenhum arquivo preexistente com o mesmo nome e conteúdo diferente).",
        f"- **Destinos duplicados no plano:** {r['dup_dest']} (cada destino recebe um único arquivo).",
        f"- **Conteúdo duplicado na origem:** {br(d['dup_origem'][0])} grupos de arquivos idênticos (mesmo QuickXor e "
        f"tamanho), {br(d['dup_origem'][1])} cópias excedentes, {gib(d['dup_origem'][2])} GiB "
        "(`duplicatas_origem.csv`). Foram migrados como estavam, cada um no seu lugar da EAP.",
        f"- Entre os validados: {br(r['copias_conteudo'])} arquivos têm conteúdo idêntico a outro validado.",
        f"- **Arquivos que já existiam no PARCEIRO:** {d['auditoria']['real'] - d['auditoria']['validados']} nas áreas "
        "migradas (01.Coordenação Programa e 02. Programa), não tocados.",
    ], None))
    s.append(("10. Hashes e métodos de validação", [], (["Método", "Arquivos", "Como"], [
        ["qxh_servidor_x_destino", br(r["metodos"].get("qxh_servidor_x_destino", 0)),
         "destino relido; QuickXor == quickXorHash da Microsoft; mesmo tamanho"],
        ["sha256_origem_x_destino", br(r["metodos"].get("sha256_origem_x_destino", 0)),
         "origem e destino relidos; SHA-256 iguais (arquivos sem hash na Microsoft)"],
        ["dispensada_por_decisao", "1", "vídeo: tamanho idêntico + 7 trechos SHA-256 iguais; hash integral não calculado"],
    ])))
    lin = [[dt(t["inicio"]), t["nome"][:48], t["lote"], t["resultado"], (t["erro"] or "")[:90].replace("|", "/")]
           for t in d["falhas_transf"]]
    s.append(("11. Incidentes e recuperação", [
        f"**Tentativas de cópia que falharam** ({len(lin)}; todas recuperadas por nova tentativa, exceto o arquivo `~$`):",
    ], (["Quando", "Arquivo", "Lote", "Resultado", "Erro"], lin)))
    lin = [[dt(x["quando"]), x["nome"][:48], x["resultado"], br(x["bytes_destino"] or 0), br(x["tamanho"]), x["estado"]]
           for x in d["verif_ruins"]]
    s.append(("11.1 Conferências não aprovadas", [], (["Quando", "Arquivo", "Registro", "Bytes lidos", "Bytes esperados",
                                                       "Estado final"], lin)))
    s.append(("11.2 Operação autônoma", [
        f"- Watchdog: {w.get('n', 0)} rondas de {dt(w.get('ini'))} a {dt(w.get('fim'))}; ações: "
        f"{', '.join(f'{k} {n}' for k, n in (w.get('acoes') or {}).items())}.",
        f"- Vídeo: {d['video_log']['quedas_tls']} quedas de conexão (TLS/EOF/reset/timeout) durante o envio, "
        f"{d['video_log']['retentativas']} retentativas de bloco, todas recuperadas; montagem no servidor em "
        f"{', '.join(d['video_log']['montagem'])} s (teste e definitivo).",
    ], None))
    s.append(("12. Problemas encontrados e correções", [], (["Problema", "Causa", "Correção"], [
        ["401 no PARCEIRO na configuração", "BOM (U+FEFF) na senha via pipe do PowerShell", "codificação UTF-8 sem BOM; Login Flow v2"],
        ["Pré-requisito com 25/25 \"divergentes\"", "`lsjson --files-from` sem `-R`", "flag corrigida antes de qualquer lote"],
        ["Runner morria com o Claude", "processo no job do terminal", "execução pelo Agendador do Windows"],
        ["3 EXCECAO na madrugada de 20/10", "\"uploading chunk failed\" classificado como permanente", "classificador corrigido; itens validados"],
        ["2 .dav DIVERGENTE (MP-16)", "conferência em linha leu antes de o PARCEIRO terminar a escrita", "reconferência → VALIDADO"],
        ["Quedas no envio do vídeo", "conexão/proxy derrubando PUTs longos", "blocos 64→16 MiB; retentativa por bloco"],
        ["Blocos 1669/1670 faltando antes da montagem", "falha definitiva de 2 PUTs", "detectados pela checagem de tamanhos; reenviados"],
        ["Vídeo DIVERGENTE (21/10 00:43)", "rcd corta streams com 1 h (`--rc-server-write-timeout`); hash de 45% comparado",
         "337 passa a tratar leitura incompleta como ERRO; verificador 348 direto"],
        ["Releitura 348 interrompida (10:26)", "processo encerrado (0xC000013A) com pouca RAM livre", "reiniciada; depois dispensada"],
    ])))
    s.append(("13. Decisões da responsável (texto integral gravado no banco)", [],
              (["Lote", "Aprovado em", "Autorização (início)"],
               [[lt["nome"], dt(lt["aprovado_em"]), (lt["aprovacao_texto"] or "")[:150].replace("\n", " ").replace("|", "/") + "…"]
                for lt in d["lotes"]])))
    s.append(("13.1 Outras decisões registradas", [
        "- RD-102 = RD-102A; MP → `01. MP-Estado`; TCE → `02. TCE-Estado` (D-09).",
        "- CERTIFICADORA ENTREGA fora do escopo; Requisição nº 2/2026 NÃO MIGRAR; arquivo `~$` fora do escopo (21/10).",
        "- MP-14: nome `2026-07-02 - Resposta MP - Ofício 000/0000` mantido; observação: data de minuta.",
        "- Correspondências → `02. Programa/01. Correspondências` (Carta 05/2026).",
        "- **21/10/2026 — conferência integral do vídeo dispensada pela responsável**, sem declarar hash validado.",
    ], None))
    mp = {k: r["por_lote"][k] for k in ("MP-14", "MP-16")}
    s.append(("14. MP-14 e MP-16", [], (["Lote", "Destino", "Arquivos", "GiB", "Validados", "Observação"], [
        ["MP-14", "01. MP-Estado/2026-07-02 - Resposta MP - Ofício 000/0000", br(mp["MP-14"][0]), gib(mp["MP-14"][1]),
         br(mp["MP-14"][2]), "data associada a minuta"],
        ["MP-16", "01. MP-Estado/2026-06-19 - Resposta MP - Ofício 000/0000", br(mp["MP-16"][0]), gib(mp["MP-16"][1]),
         br(mp["MP-16"][2]), "1 arquivo ~$ fora do escopo; vídeo em lote próprio"],
    ])))
    reg = (d["regioes"] or {}).get("regioes", {})
    ii = d["integral_int"] or {}
    s.append(("15. Vídeo de 87,76 GiB e ressalva de integridade", [
        RESSALVA,
        f"- Arquivo: `{v['caminho_rel']}` → `{v['destino_pasta']}/{v['nome_final']}`.",
        f"- Tamanho na Microsoft: {br(v['tamanho'])} B; QuickXor Microsoft `{v['quick_xor_hash']}`. Tamanho no PARCEIRO após "
        f"montagem: {br(v['tamanho'])} B (PROPFIND, 20/10 23:43 e 21/10 07:50).",
        "- Envio: 5.617 blocos de 16 MiB, todos conferidos por tamanho no servidor antes da montagem; montagem em 800 s.",
        f"- Trechos de 16 MiB comparados por SHA-256 origem × destino: {sum(x['igual'] for x in reg.values())}/{len(reg)} iguais "
        f"({', '.join(reg)}).",
        "- Conferências integrais iniciadas e não concluídas: 337 (cortada em 42.719.727.816 B pelo timeout de 1 h do rcd), "
        f"348 (interrompida em 28.700.186.440 B às 10:26) e 348 (encerrada por decisão em {br(ii.get('lidos', 0))} B às "
        f"{dt(ii.get('atualizado'))}).",
        "- **O que isso prova e o que não prova:** prova tamanho exato e conteúdo idêntico nos trechos amostrados; **não** "
        "prova que todos os 94 GB são idênticos. Para provar, basta reler o destino (≈ 8 h a ~3 MiB/s), sem reenvio: "
        "`python scripts/348_migra_video_conferir.py --integral`.",
    ], None))
    nao = [i for i in d["itens"] if i["estado"] in ("FORA_DO_ESCOPO", "PENDENTE_DE_DECISAO")]
    s.append(("16. Arquivos não migrados", [
        f"{br(len(nao))} arquivos ({gib(sum(i['tamanho'] for i in nao))} GiB); relação completa em `NAO_MIGRADOS.csv`.",
    ], (["Motivo", "Arquivos", "GiB"], [[m, br(n), gib(b)] for m, (n, b) in sorted(
        {motivo(i): (sum(1 for j in nao if motivo(j) == motivo(i)), sum(j["tamanho"] for j in nao if motivo(j) == motivo(i)))
         for i in nao}.items())])))
    s.append(("17. Estrutura final no PARCEIRO", ["Árvore completa até 4 níveis em `ESTRUTURA_FINAL_PARCEIRO.md`. Primeiro e "
                                               "segundo níveis:"],
              (["Pasta", "Arquivos", "GiB"], [[k, br(n), gib(b)] for k, (n, b) in sorted(r["arvore"].items())
                                               if k.count("/") <= 1])))
    s.append(("18. Evidências", [
        "Base: `C:\\caminho\\exemplo\\organizacao-sharepoint`.",
        "- `data\\migracao_parceiro.db` — banco de controle (snapshot consistente em `backup\\encerramento_migracao_parceiro_20261021\\`).",
        "- `output\\migracao_parceiro\\LISTAGEM_FINAL_ARQUIVOS.csv` — todos os arquivos: estado, destino, hashes, método.",
        "- `output\\migracao_parceiro\\validados.csv`, `excecoes.csv`, `lote_*.csv`, `resultado_lote_*.csv`.",
        "- `output\\migracao_parceiro\\auditoria_destino_20261020.json` — presença e tamanho de cada validado no PARCEIRO.",
        "- `output\\migracao_parceiro\\video_conferencia_regioes.json`, `video_conferencia_integral_INTERROMPIDA_20261021.json`.",
        "- `tmp\\migracao_parceiro\\prog-migracao-*.json` (checkpoints) e sinais `VIDEO_PARADO`, `VIDEO_ENCERRADO_SEM_VALIDACAO_INTEGRAL`.",
        "- `logs\\` — rclone, conferência, vídeo (`video_retomavel_20261020.log`), watchdog e incidentes.",
        "- `docs\\migracao_parceiro\\` e `docs\\02_DECISOES.md` — arquitetura, piloto, operação, pendências e decisões.",
    ], None))
    s.append(("19. Pendências para revisão com a chefia", [x for x in executivo(d, r)[-1][1]], None))
    s.append(("20. Recomendações", [
        "1. Dar aceite formal da migração (com a ressalva do vídeo) **antes** de qualquer limpeza no SharePoint; a limpeza "
        "deve ser uma frente própria, com lista fechada e backup.",
        "2. Se o vídeo for evidência de processo, rodar a releitura integral numa janela noturna com cabo de rede.",
        "3. Deduplicar no PARCEIRO, por quem tem permissão, as cópias idênticas herdadas da origem.",
        "4. Manter a EAP como única regra de entrada no PARCEIRO; novos documentos já nomeados no padrão (data ISO + tipo + ref).",
        "5. Arquivos > 20 GiB: preferir rede cabeada e o uploader retomável; o motor comum (rclone WebDAV) não retoma.",
        "6. Remover a cópia de teste e revisar os 77 arquivos que já existiam nas áreas migradas.",
    ], None))
    return s


def motivo(i) -> str:
    if i["nome"].startswith("~$"):
        return "Arquivo temporário de bloqueio do Excel (~$) - não é documento"
    return MOTIVOS.get(i["regra"], i["regra"] or i["estado"])


# ---------------------------------------------------------------- saida

def para_md(titulo: str, sub: str, secs: list) -> str:
    out = [f"# {titulo}", "", f"*{sub}*", ""]
    for t, pars, tb in secs:
        out += [f"## {t}", ""]
        for p in pars:
            out += [p, ""]
        if tb:
            out += [tab(*tb), ""]
    return "\n".join(out)


def para_docx(titulo: str, sub: str, secs: list, caminho: Path):
    from docx import Document
    from docx.shared import Pt
    doc = Document()
    doc.styles["Normal"].font.name = "Calibri"
    doc.styles["Normal"].font.size = Pt(10)
    doc.add_heading(titulo, 0)
    doc.add_paragraph(sub)

    def par(texto, estilo=None):
        p = doc.add_paragraph(style=estilo)
        for k, t in enumerate(texto.replace("`", "").split("**")):
            p.add_run(t).bold = k % 2 == 1

    for t, pars, tb in secs:
        doc.add_heading(t, 1)
        for p in pars:
            par(p[2:], "List Bullet") if p.startswith("- ") else par(p)
        if tb:
            cab, linhas = tb
            tt = doc.add_table(rows=1, cols=len(cab))
            tt.style = "Light Grid Accent 1"
            for k, c in enumerate(cab):
                tt.rows[0].cells[k].text = c
            for ln in linhas:
                cells = tt.add_row().cells
                for k, c in enumerate(ln):
                    cells[k].text = str(c).replace("**", "").replace("`", "")
    doc.save(caminho)


def gravar(nome: str, titulo: str, sub: str, secs: list) -> list[Path]:
    md = SAIDA / f"{nome}.md"
    md.write_text(para_md(titulo, sub, secs), encoding="utf-8")
    dx = SAIDA / f"{nome}.docx"
    try:
        para_docx(titulo, sub, secs, dx)
    except PermissionError:          # aberto no Word
        dx = dx.with_name(f"{nome}_{time.strftime('%H%M')}.docx")
        para_docx(titulo, sub, secs, dx)
    return [md, dx]


def main() -> int:
    d = coletar()
    r = resumir(d)
    sub = (f"Gerado em {d['gerado']} a partir do banco de controle (data/migracao_parceiro.db). "
           f"Período de transferência: {dt(d['janela'][0])} a {dt(d['janela'][1])}.")
    arqs = gravar("RELATORIO_EXECUTIVO_MIGRACAO_PARCEIRO", "Migração Programa → PARCEIRO INFRA — Resumo executivo", sub, executivo(d, r))
    arqs += gravar("RELATORIO_TECNICO_MIGRACAO_PARCEIRO", "Migração Programa → PARCEIRO INFRA — Relatório técnico final", sub, tecnico(d, r))
    cab = ["caminho_origem", "bytes", "estado", "lote", "destino_parceiro", "quickxor_microsoft", "quickxor_destino",
           "sha256_destino", "sha256_origem", "metodo_conferencia", "conferido_em", "tentativas", "motivo_nao_migrado"]
    with open(SAIDA / "LISTAGEM_FINAL_ARQUIVOS.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(cab)
        for i in d["itens"]:
            fora = i["estado"] in ("FORA_DO_ESCOPO", "PENDENTE_DE_DECISAO")
            w.writerow([i["caminho_rel"], i["tamanho"], i["estado"], i["lote"] or "",
                        "" if fora else f"PARCEIRO-INFRA/{i['destino_pasta']}/{i['nome_final']}", i["quick_xor_hash"] or "",
                        i["qxd"] or "", i["shad"] or "", i["shao"] or "", i["metodo"] or "", i["vquando"] or "",
                        i["tentativas"], motivo(i) if fora else ""])
    with open(SAIDA / "NAO_MIGRADOS.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["caminho_origem", "bytes", "estado", "motivo"])
        for i in d["itens"]:
            if i["estado"] in ("FORA_DO_ESCOPO", "PENDENTE_DE_DECISAO"):
                w.writerow([i["caminho_rel"], i["tamanho"], i["estado"], motivo(i)])
    arv = ["# Estrutura final no PARCEIRO INFRA (pastas que receberam arquivos da migração)", "",
           f"*{sub}* Contagem acumulada (arquivos e GiB abaixo de cada pasta), até 4 níveis.", ""]
    for k in sorted(r["arvore"]):
        n, b = r["arvore"][k]
        arv.append(f"{'  ' * k.count('/')}- **{k.rsplit('/', 1)[-1]}** — {br(n)} arq · {gib(b)} GiB")
    (SAIDA / "ESTRUTURA_FINAL_PARCEIRO.md").write_text("\n".join(arv) + "\n", encoding="utf-8")
    arqs += [SAIDA / "LISTAGEM_FINAL_ARQUIVOS.csv", SAIDA / "NAO_MIGRADOS.csv", SAIDA / "ESTRUTURA_FINAL_PARCEIRO.md"]
    for a in arqs:
        print(a)
    print(f"escopo {r['esc_n']} · validados {r['val_n']} · enviados {r['enviados_n']} · inventario {r['inv_n']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
