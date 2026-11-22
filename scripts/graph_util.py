"""
graph_util — acesso ao SharePoint pela API do Microsoft Graph.

Sem dependencia externa: usa apenas a biblioteca padrao do Python.

Credenciais vem do arquivo `.env` na raiz do repositorio (fora do Git).
Nada aqui escreve no SharePoint — escrita fica em modulo proprio, depois
que a leitura estiver validada.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

RAIZ_REPO = Path(__file__).resolve().parents[1]
ENV = RAIZ_REPO / ".env"
GRAPH = "https://graph.microsoft.com/v1.0"


# ----------------------------------------------------------------------
# Credenciais
# ----------------------------------------------------------------------

def carregar_env(caminho: Path | str | None = None) -> dict:
    """Le o .env (chave=valor, # comenta). Variaveis de ambiente vencem."""
    dados: dict[str, str] = {}
    caminho = Path(caminho) if caminho else ENV
    if caminho.exists():
        for linha in caminho.read_text(encoding="utf-8").splitlines():
            linha = linha.strip()
            if not linha or linha.startswith("#") or "=" not in linha:
                continue
            chave, valor = linha.split("=", 1)
            dados[chave.strip()] = valor.strip()
    for chave in list(dados) + ["GRAPH_TENANT_ID", "GRAPH_CLIENT_ID", "GRAPH_CLIENT_SECRET"]:
        if os.environ.get(chave):
            dados[chave] = os.environ[chave]
    return dados


class ErroGraph(Exception):
    """Erro devolvido pelo Graph, com codigo HTTP e mensagem legivel."""

    def __init__(self, status: int, codigo: str, mensagem: str, url: str = ""):
        self.status = status
        self.codigo = codigo
        self.mensagem = mensagem
        self.url = url
        super().__init__(f"HTTP {status} {codigo}: {mensagem}")


# ----------------------------------------------------------------------
# Cliente
# ----------------------------------------------------------------------

class Graph:
    def __init__(self, env: dict | None = None):
        self.env = env or carregar_env()
        faltando = [c for c in ("GRAPH_TENANT_ID", "GRAPH_CLIENT_ID", "GRAPH_CLIENT_SECRET")
                    if not self.env.get(c)]
        if faltando:
            raise ValueError(f".env sem as chaves: {', '.join(faltando)}")
        self.host = self.env.get("SHAREPOINT_HOST", "contoso.sharepoint.com")
        self.site = self.env.get("SHAREPOINT_SITE", "/sites/exemplo")
        self._token = None
        self._expira_em = 0.0

    # -- autenticacao ---------------------------------------------------
    def token(self) -> str:
        """Token de aplicativo (client credentials). Renova sozinho."""
        if self._token and time.time() < self._expira_em - 120:
            return self._token

        url = (f"https://login.microsoftonline.com/{self.env['GRAPH_TENANT_ID']}"
               f"/oauth2/v2.0/token")
        corpo = urllib.parse.urlencode({
            "client_id": self.env["GRAPH_CLIENT_ID"],
            "client_secret": self.env["GRAPH_CLIENT_SECRET"],
            "scope": "https://graph.microsoft.com/.default",
            "grant_type": "client_credentials",
        }).encode()
        requisicao = urllib.request.Request(
            url, data=corpo,
            headers={"Content-Type": "application/x-www-form-urlencoded"})
        try:
            with urllib.request.urlopen(requisicao, timeout=60) as resposta:
                dados = json.loads(resposta.read())
        except urllib.error.HTTPError as exc:
            detalhe = exc.read().decode("utf-8", "replace")
            try:
                erro = json.loads(detalhe)
                mensagem = erro.get("error_description", detalhe)
            except json.JSONDecodeError:
                mensagem = detalhe
            raise ErroGraph(exc.code, "falha_autenticacao", mensagem.split("\r")[0], url) from exc

        self._token = dados["access_token"]
        self._expira_em = time.time() + int(dados.get("expires_in", 3600))
        return self._token

    # -- chamadas -------------------------------------------------------
    def get(self, caminho: str, tentativas: int = 4) -> dict:
        """GET no Graph. `caminho` pode ser relativo ('/sites/...') ou URL completa."""
        # Caminhos do SharePoint tem espaco e acento: precisam ser codificados,
        # sem estragar o que ja veio codificado nem a query string.
        url = (caminho if caminho.startswith("http")
               else GRAPH + urllib.parse.quote(caminho, safe="/:?$&=,@!*'()+~%"))
        for tentativa in range(1, tentativas + 1):
            requisicao = urllib.request.Request(
                url, headers={"Authorization": f"Bearer {self.token()}",
                              "Accept": "application/json"})
            try:
                with urllib.request.urlopen(requisicao, timeout=120) as resposta:
                    return json.loads(resposta.read())
            except urllib.error.HTTPError as exc:
                bruto = exc.read().decode("utf-8", "replace")
                codigo, mensagem = _detalhar(bruto, exc)
                # 429/503: o Graph pediu para esperar. Nao e erro do documento.
                if exc.code in (429, 503, 504) and tentativa < tentativas:
                    espera = float(exc.headers.get("Retry-After", 5 * tentativa))
                    time.sleep(min(espera, 60))
                    continue
                raise ErroGraph(exc.code, codigo, mensagem, url) from exc
            except urllib.error.URLError as exc:
                if tentativa < tentativas:
                    time.sleep(3 * tentativa)
                    continue
                raise ErroGraph(0, "sem_rede", str(exc.reason), url) from exc
        raise ErroGraph(0, "tentativas_esgotadas", url)

    def paginar(self, caminho: str):
        """Percorre todas as paginas de uma coleção (@odata.nextLink)."""
        pagina = self.get(caminho)
        while True:
            for item in pagina.get("value", []):
                yield item
            proxima = pagina.get("@odata.nextLink")
            if not proxima:
                return
            pagina = self.get(proxima)

    # -- atalhos do SharePoint ------------------------------------------
    def site_id(self) -> str:
        caminho = f"/sites/{self.host}:{self.site}"
        return self.get(caminho)["id"]

    def bibliotecas(self, site_id: str) -> list[dict]:
        return list(self.paginar(f"/sites/{site_id}/drives"))

    def permissoes_do_site(self, site_id: str) -> list[dict]:
        return list(self.paginar(f"/sites/{site_id}/permissions"))


def _detalhar(bruto: str, exc) -> tuple[str, str]:
    try:
        erro = json.loads(bruto).get("error", {})
        return erro.get("code", "desconhecido"), erro.get("message", bruto)[:400]
    except json.JSONDecodeError:
        return "desconhecido", (bruto or str(exc))[:400]


# ----------------------------------------------------------------------
# Diagnostico legivel dos erros mais comuns
# ----------------------------------------------------------------------

def explicar(erro: ErroGraph) -> str:
    if erro.status == 401 or erro.codigo == "falha_autenticacao":
        return ("A credencial nao foi aceita. Conferir Tenant ID, Client ID e o "
                "segredo no .env — ou o segredo ja expirou/foi revogado.")
    if erro.status == 403:
        return ("Autenticou, mas o aplicativo NAO tem acesso ao site. Falta a TI:\n"
                "  (a) conceder a permissao Sites.Selected no Entra ID com "
                "consentimento do administrador; e\n"
                "  (b) liberar o aplicativo NESTE site, com role 'write' "
                "(Etapa 4 do anexo).")
    if erro.status == 404:
        return ("Autenticou, mas o site/caminho nao foi encontrado. Conferir host e "
                "caminho do site no .env.")
    if erro.status == 429:
        return "O Graph limitou a taxa de requisicoes. Diminuir o ritmo e repetir."
    return erro.mensagem
