"""
leitura - leitura pontual de documentos da origem, so para CLASSIFICAR.

Autorizada pela Emmily em 19/10/2026 (demanda de continuidade, secao 10.4):
ler oficios/manifestacoes para achar data, numero, obra e orgao.

- GET /content pelo Graph, com o redirecionamento seguido SEM repassar o
  token ao endereco de download (mesmo cuidado do GraphRO do Cerebro Documental).
- Bytes ficam so em memoria, com teto de tamanho; nada e gravado em disco.
- Extrai o minimo: texto das primeiras paginas (PDF) ou paragrafos (DOCX).
"""

from __future__ import annotations

import io
import re
import urllib.error
import urllib.request

from graph_util import GRAPH, Graph

TETO_BYTES = 25 * 2**20


class _SemRedirecionar(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def baixar_memoria(g: Graph, drive_id: str, item_id: str, teto: int = TETO_BYTES) -> bytes:
    req = urllib.request.Request(f"{GRAPH}/drives/{drive_id}/items/{item_id}/content",
                                 headers={"Authorization": f"Bearer {g.token()}"})
    opener = urllib.request.build_opener(_SemRedirecionar)
    try:
        resp = opener.open(req, timeout=120)
        url = None
    except urllib.error.HTTPError as e:
        if e.code not in (301, 302, 303, 307, 308):
            raise
        url = e.headers["Location"]
    if url:  # URL pre-autenticada: vai sem Authorization
        resp = urllib.request.urlopen(url, timeout=300)
    with resp:
        dados = resp.read(teto + 1)
    if len(dados) > teto:
        raise ValueError(f"arquivo acima do teto de {teto} bytes - nao lido")
    return dados


def texto(dados: bytes, extensao: str, paginas: int = 3) -> str:
    ext = (extensao or "").lower()
    if ext == "pdf":
        import fitz
        with fitz.open(stream=dados, filetype="pdf") as doc:
            return "\n".join(doc[i].get_text() for i in range(min(paginas, doc.page_count)))
    if ext == "docx":
        import docx
        d = docx.Document(io.BytesIO(dados))
        partes = [p.text for p in d.paragraphs]
        for t in d.tables:
            for linha in t.rows:
                partes.append(" | ".join(c.text for c in linha.cells))
        return "\n".join(partes)
    return ""


PADROES = {
    "oficio": re.compile(r"of[ií]cio\s*(?:n[º°o.]*\s*)?[\w./-]*\d[\w./-]*", re.I),
    "processo": re.compile(r"processo\s*(?:sei\s*)?(?:n[º°o.]*\s*)?[\d./-]{8,}", re.I),
    "sei": re.compile(r"\bSEI\b[^\n]{0,40}?\d{6,}", re.I),
    "manifestacao": re.compile(r"manifesta[çc][ãa]o\s*n[º°o.]*\s*[\w./-]+", re.I),
    "requisicao": re.compile(r"requisi[çc][ãa]o\s*(?:de\s*\w+\s*)?n[º°o.]*\s*[\w./-]+", re.I),
    "notificacao": re.compile(r"notifica[çc][ãa]o\s*n[º°o.]*\s*[\w./-]+", re.I),
    "procedimento": re.compile(r"(?:procedimento|inqu[ée]rito|not[íi]cia de fato)[^\n]{0,60}?\d[\d./-]{5,}", re.I),
    "data": re.compile(r"\b\d{1,2}\s*(?:/|de)\s*(?:\d{1,2}|janeiro|fevereiro|mar[çc]o|abril|maio|junho|julho|agosto|setembro|outubro|novembro|dezembro)\s*(?:/|de)\s*\d{2,4}\b", re.I),
    "obra": re.compile(r"\bRD[\s-]?\d{3}A?\b", re.I),
    "orgao": re.compile(r"Minist[ée]rio P[úu]blico|MP-Estado|MP-Estado|Tribunal de Contas|TCE[-\s]?Estado|Órgão Supervisor|Órgão Contratante|Promotoria", re.I),
    "entidade": re.compile(r"Estruturadora|PARCEIRO|Instituto Parceiro", re.I),
}


def achados(txt: str) -> dict[str, list[str]]:
    out = {}
    for nome, rx in PADROES.items():
        vistos = []
        for m in rx.finditer(txt):
            v = " ".join(m.group(0).split())
            if v not in vistos:
                vistos.append(v)
        if vistos:
            out[nome] = vistos[:8]
    return out
