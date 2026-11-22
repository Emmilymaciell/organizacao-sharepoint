"""Motor de transferencia contra o rclone REAL, com duas pastas locais (sem rede)."""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from migra_parceiro.base import conectar  # noqa: E402
from migra_parceiro.espaco import GIB, Guarda  # noqa: E402
from migra_parceiro.motor import Motor, Trava  # noqa: E402
from migra_parceiro.quickxor import de_bytes  # noqa: E402
from migra_parceiro.rc import ErroRc, Rcd  # noqa: E402

RCLONE = Path(os.environ.get("LOCALAPPDATA", "")) / (
    "Microsoft/WinGet/Packages/Rclone.Rclone_Microsoft.Winget.Source_8wekyb3d8bbwe/"
    "rclone-v1.75.1-windows-amd64/rclone.exe")
pytestmark = pytest.mark.skipif(not RCLONE.exists(), reason="rclone do projeto ausente")

ARQS = {
    "17_Resposta TCE 06 10 26/Manifestação nº 031.pdf": os.urandom(70_000),
    "17_Resposta TCE 06 10 26/Anexos/A1/projeto.dwg": os.urandom(1_300_000),
    "05. Produtos/01 - Julho - 2025/G1/relatório.pdf": os.urandom(5_000),
    "05. Produtos/01 - Julho - 2025/G2/igual.pdf": b"conteudo igual",
    "05. Produtos/01 - Julho - 2025/G2/diferente.pdf": b"versao da origem",
    "sem hash/arquivo.bin": os.urandom(2_000),
}
DESTINO = {
    "17_Resposta TCE 06 10 26": "02. Programa/04. Atendimento aos Órgãos de Controle/02. TCE-Estado/"
                                "2026-10-06 - Resposta TCE - RD-101-Requisição 13-2026",
    "05. Produtos/01 - Julho - 2025/G1": "01.Coordenação Programa/02. Produtos Mensais/2025-07 - Julho/"
                                         "G1 - Coordenação Geral Executiva",
    "05. Produtos/01 - Julho - 2025/G2": "01.Coordenação Programa/02. Produtos Mensais/2025-07 - Julho/"
                                         "G2 - Gestão de Engenharia e Socioambiental",
    "sem hash": "x/sem hash",
}


def destino_de(rel: str) -> str:
    pasta = rel.rsplit("/", 1)[0]
    for pref, dst in sorted(DESTINO.items(), key=lambda kv: -len(kv[0])):
        if pasta == pref or pasta.startswith(pref + "/"):
            return dst + pasta[len(pref):]
    raise KeyError(rel)


@pytest.fixture
def cenario(tmp_path):
    src, dst = tmp_path / "src", tmp_path / "dst"
    for rel, dados in ARQS.items():
        (src / rel).parent.mkdir(parents=True, exist_ok=True)
        (src / rel).write_bytes(dados)
    # destino ja tem: um identico e um diferente, no caminho final
    for nome, dados in (("igual.pdf", b"conteudo igual"), ("diferente.pdf", b"VERSAO DE OUTRA PESSOA")):
        alvo = dst / destino_de(f"05. Produtos/01 - Julho - 2025/G2/{nome}") / nome
        alvo.parent.mkdir(parents=True, exist_ok=True)
        alvo.write_bytes(dados)
    con = conectar(tmp_path / "m.db")
    for i, (rel, dados) in enumerate(ARQS.items()):
        iid = f"id{i}"
        qxh = None if rel.startswith("sem hash") else de_bytes(dados)
        con.execute("INSERT INTO origem_item (item_id, drive_id, caminho_rel, nome, eh_pasta, tamanho, "
                    "quick_xor_hash, inventariado_em) VALUES (?,?,?,?,?,?,?,?)",
                    (iid, "d", rel, rel.rsplit("/", 1)[1], 0, len(dados), qxh, "t"))
        con.execute("INSERT INTO plano_item (item_id, estado, escopo, destino_pasta, nome_final, lote, "
                    "atualizado_em) VALUES (?,?,?,?,?,?,?)",
                    (iid, "APROVADO", "migrar", destino_de(rel), rel.rsplit("/", 1)[1], "T", "t"))
    con.commit()
    rcd = Rcd(comando_base=[str(RCLONE), "--config", "NUL"])
    with rcd:
        yield con, rcd, src.as_posix(), dst.as_posix(), tmp_path


def estados(con):
    return {r[0]: r[1] for r in con.execute(
        "SELECT o.caminho_rel, p.estado FROM plano_item p JOIN origem_item o USING(item_id)")}


def test_copia_renomeia_nao_sobrescreve_e_valida(cenario):
    con, rcd, src, dst, tmp = cenario
    antes = {p: p.read_bytes() for p in Path(src).rglob("*") if p.is_file()}
    m = Motor(con, rcd, src, dst, concorrencia=3, arquivo_progresso=tmp / "progresso.json")
    r = m.transferir("T")
    e = estados(con)
    assert e["05. Produtos/01 - Julho - 2025/G2/diferente.pdf"] == "CONFLITO"
    assert e["05. Produtos/01 - Julho - 2025/G2/igual.pdf"] == "COPIADO"      # ja existia identico
    assert r["conflitos"] == 1 and r["ja_existiam"] == 1 and r["falhas"] == 0
    # o arquivo de outra pessoa ficou intacto
    alvo = Path(dst) / destino_de("05. Produtos/01 - Julho - 2025/G2/diferente.pdf") / "diferente.pdf"
    assert alvo.read_bytes() == b"VERSAO DE OUTRA PESSOA"
    # chegou no caminho FINAL, ja com o nome definitivo
    final = Path(dst) / destino_de("17_Resposta TCE 06 10 26/Anexos/A1/projeto.dwg") / "projeto.dwg"
    assert final.read_bytes() == ARQS["17_Resposta TCE 06 10 26/Anexos/A1/projeto.dwg"]
    # a origem nao mudou nem um byte
    assert {p: p.read_bytes() for p in Path(src).rglob("*") if p.is_file()} == antes
    assert (tmp / "progresso.json").exists()

    c = m.conferir("T")
    e = estados(con)
    assert c.get("VALIDADO") == 5
    assert all(v == "VALIDADO" for k, v in e.items() if not k.endswith("diferente.pdf"))
    metodo = con.execute("SELECT metodo FROM verificacao v JOIN origem_item o USING(item_id) "
                         "WHERE o.caminho_rel LIKE 'sem hash%'").fetchone()[0]
    assert metodo == "sha256_origem_x_destino"


def test_conferencia_em_linha_valida_cada_arquivo_logo_apos_a_copia(cenario):
    con, rcd, src, dst, tmp = cenario
    r = Motor(con, rcd, src, dst, conferir_em_linha=True).transferir("T")
    e = estados(con)
    assert r["validados_em_linha"] == 5
    assert all(v == "VALIDADO" for k, v in e.items() if not k.endswith("diferente.pdf"))
    # VALIDADO veio de releitura do destino, nao do fim do upload
    assert con.execute("SELECT COUNT(*) FROM verificacao WHERE resultado='VALIDADO' "
                       "AND qxh_destino IS NOT NULL AND sha256_destino IS NOT NULL").fetchone()[0] == 5


def test_padroes_da_eap():
    from migra_parceiro.prerequisitos import conforme_eap
    assert conforme_eap("01.Coordenação Programa/02. Produtos Mensais/2025-07 - Julho/G1 - Coordenação Geral Executiva/x")[0]
    assert not conforme_eap("01.Coordenação Programa/02. Produtos Mensais/2025-07 - Agosto/G1 - Coordenação Geral Executiva")[0]
    assert conforme_eap("02. Programa/03. Prestação de Contas/04. Obras/RD-102A/Trimestre 2/REV 0")[0]
    assert not conforme_eap("02. Programa/03. Prestação de Contas/04. Obras/RD-102/Trimestre 2")[0]
    assert not conforme_eap("02. Programa/04. Atendimento aos Órgãos de Controle/01. MP-Estado/2026-06-19 - Resposta MP - {REF}")[0]
    assert conforme_eap("02. Programa/04. Atendimento aos Órgãos de Controle/02. TCE-Estado/"
                        "2026-10-06 - Resposta TCE - RD-101-Requisição 13-2026/Anexos")[0]
    assert conforme_eap("02. Programa/01. Correspondências")[0]
    assert conforme_eap("02. Programa/01. Correspondências/Carta_05.2026_PROG/Carta 05.2026 Programa - ANEXOS")[0]
    assert not conforme_eap("02. Programa/01. Correspondências/{REF}")[0]


def test_conferencia_acusa_conteudo_divergente(cenario):
    con, rcd, src, dst, tmp = cenario
    m = Motor(con, rcd, src, dst)
    m.transferir("T")
    alvo = Path(dst) / destino_de("05. Produtos/01 - Julho - 2025/G1/relatório.pdf") / "relatório.pdf"
    alvo.write_bytes(b"corrompido")
    m.conferir("T")
    assert estados(con)["05. Produtos/01 - Julho - 2025/G1/relatório.pdf"] == "DIVERGENTE"


def test_leitura_cortada_na_conferencia_e_erro_nao_divergente(cenario, monkeypatch):
    """O rcd corta streams com 1 h; um hash de trecho nao pode virar DIVERGENTE (video VIDEO-87G, 21/10)."""
    import migra_parceiro.motor as motor
    con, rcd, src, dst, tmp = cenario
    m = Motor(con, rcd, src, dst)
    m.transferir("T")
    original = motor.ler_e_hashear

    def cortado(rcd_, fs, remoto):
        n, qx, sha = original(rcd_, fs, remoto)
        return n // 2, "AAAA", sha
    monkeypatch.setattr(motor, "ler_e_hashear", cortado)
    c = m.conferir("T")
    e = estados(con)
    assert c.get("ERRO") == 5 and "DIVERGENTE" not in c
    assert e["17_Resposta TCE 06 10 26/Anexos/A1/projeto.dwg"] == "COPIADO"     # volta para nova conferencia
    monkeypatch.setattr(motor, "ler_e_hashear", original)
    assert m.conferir("T").get("VALIDADO") == 5


def test_retomada_nao_reenvia_o_que_ja_chegou(cenario):
    con, rcd, src, dst, tmp = cenario
    m = Motor(con, rcd, src, dst)
    m.transferir("T")
    # simula queda: um item ficou EM_TRANSFERENCIA com o arquivo ja no destino
    con.execute("UPDATE plano_item SET estado='EM_TRANSFERENCIA' WHERE item_id='id1'")
    con.commit()
    r = Motor(con, rcd, src, dst).transferir("T")
    assert estados(con)["17_Resposta TCE 06 10 26/Anexos/A1/projeto.dwg"] == "COPIADO"
    assert r["ja_existiam"] == 1
    ultima = con.execute("SELECT resultado FROM transferencia WHERE item_id='id1' ORDER BY id DESC").fetchone()[0]
    assert ultima == "JA_EXISTIA"


def test_pausa_e_guarda_de_disco_param_antes_de_comecar(cenario):
    con, rcd, src, dst, tmp = cenario
    (tmp / "PAUSAR").write_text("")
    r = Motor(con, rcd, src, dst, arquivo_pausa=tmp / "PAUSAR").transferir("T")
    assert r["pausado"] and r["feitos"] == 0
    g = Guarda(tmp, tmp / "t", reserva_minima=10**6 * GIB, teto_temporario=GIB)
    r = Motor(con, rcd, src, dst, guarda=g).transferir("T")
    assert r["parado_por_disco"] and r["feitos"] == 0


def test_trava_impede_execucao_simultanea(cenario):
    con, *_ , tmp = cenario
    con.execute("INSERT INTO trava (nome, pid, host, desde, batida) VALUES ('fila', ?, ?, 'x', ?)",
                (os.getppid(), __import__("socket").gethostname(), __import__("time").time()))
    con.commit()
    with pytest.raises(RuntimeError, match="execucao ativa"):
        with Trava(con):
            pass


def test_mesmo_qxh_aceita_as_tres_grafias():
    from migra_parceiro.motor import _mesmo_qxh
    b64 = de_bytes(b"abc" * 1000)
    hexa = __import__("base64").b64decode(b64).hex()
    assert _mesmo_qxh(b64, hexa)
    assert _mesmo_qxh(b64, b64.replace("+", "-").replace("/", "_"))
    assert not _mesmo_qxh(b64, de_bytes(b"abd" * 1000))


def test_classificacao_de_erros():
    from migra_parceiro.motor import classificar_erro
    assert classificar_erro(ErroRc(500, "Post ...: dial tcp: i/o timeout")) == "transitorio"
    assert classificar_erro(ErroRc(500, "429 Too Many Requests")) == "transitorio"
    assert classificar_erro(ErroRc(500, "503 Service Unavailable")) == "transitorio"
    assert classificar_erro(ErroRc(500, "context deadline exceeded")) == "transitorio"
    assert classificar_erro(ErroRc(500, "dial tcp: lookup x: no such host")) == "transitorio"
    assert classificar_erro(ErroRc(500, "423 Locked")) == "transitorio"
    assert classificar_erro(ErroRc(500, "403 Forbidden")) == "permanente"
    assert classificar_erro(ErroRc(500, "can't copy - destination exists and immutable")) == "permanente"
    # condicoes globais param o lote em vez de queimar a fila arquivo a arquivo
    assert classificar_erro(ErroRc(500, "507 Insufficient Storage")) == "parada_global"
    assert classificar_erro(ErroRc(500, "401 Unauthorized: Username or password was incorrect")) == "parada_global"
    assert classificar_erro(ErroRc(500, "invalid_grant: AADSTS700082 token expired")) == "parada_global"
    # erro real de 19/10 a noite: bloco do upload chegou vazio -> transitorio, apesar do "400"
    assert classificar_erro(ErroRc(500, "uploading chunk failed: Expected filesize of 10485760 bytes but read "
                                        "(from Nextcloud client) and wrote (to Nextcloud storage) 0 bytes. Could either be "
                                        "a network problem on the sending side: Sabre\\DAV\\Exception\\BadRequest: "
                                        "400 Bad Request")) == "transitorio"
    # o caminho do arquivo nao conta: 'Oficio 404.pdf' + erro de rede = transitorio
    caminho = "02. TCE-Estado/Oficio 404 - BR-403 invalidos.pdf"
    assert classificar_erro(ErroRc(500, f"{caminho}: dial tcp: no such host"), (caminho,)) == "transitorio"


def test_mesmo_qxh_base64url_sem_padding():
    from migra_parceiro.motor import _mesmo_qxh
    b64 = de_bytes(b"xyz" * 777)
    assert _mesmo_qxh(b64, b64.replace("+", "-").replace("/", "_").rstrip("="))


def test_batimento_da_trava_mantem_a_trava_viva(cenario, monkeypatch):
    import time as _t
    con, *_ = cenario
    monkeypatch.setattr(Trava, "INTERVALO", 0.2)
    with Trava(con):
        antes = con.execute("SELECT batida FROM trava").fetchone()[0]
        _t.sleep(0.7)
        depois = con.execute("SELECT batida FROM trava").fetchone()[0]
    assert depois > antes
    assert con.execute("SELECT COUNT(*) FROM trava").fetchone()[0] == 0


def test_concorrencia_adaptativa(monkeypatch):
    from migra_parceiro import motor
    relogio = iter(range(0, 10_000, 10))            # cada avaliacao = janela de 10 s
    monkeypatch.setattr(motor.time, "time", lambda: float(next(relogio)))
    c = motor.Concorrencia(4, 2, 12, janela_s=10)
    assert c.avaliar(100, 0, False) == 5            # primeira janela: sobe
    assert c.avaliar(300, 0, False) == 6            # vazao subiu: continua subindo
    assert c.avaliar(310, 0, False) == 5            # vazao caiu: inverte
    assert c.avaliar(320, 2, False) == 2            # erro transitorio: cai pela metade (piso 2)
    assert c.avaliar(330, 0, True) == 2             # memoria alta: nao sobe


class RcdInstavel:
    """Repassa ao rcd real, mas a 1a copia de cada arquivo falha com erro de rede."""
    def __init__(self, rcd, erro="connection reset by peer"):
        self.rcd, self.erro, self.vistos = rcd, erro, set()

    def __getattr__(self, nome):
        return getattr(self.rcd, nome)

    def copiar(self, src_fs, src, dst_fs, dst, config=None):
        if src not in self.vistos:
            self.vistos.add(src)
            raise ErroRc(500, self.erro, "operations/copyfile")
        return self.rcd.copiar(src_fs, src, dst_fs, dst, config)


def test_erro_transitorio_e_retentado_e_permanente_vira_excecao(cenario, monkeypatch):
    con, rcd, src, dst, tmp = cenario
    monkeypatch.setattr("migra_parceiro.motor.time.sleep", lambda s: None)
    r = Motor(con, RcdInstavel(rcd), src, dst, tentativas_por_arquivo=3).transferir("T")
    assert r["falhas"] == 0 and r["feitos"] == 5          # 4 copiados apos 1 retentativa + 1 ja existia
    tentativas = con.execute("SELECT COUNT(*) FROM transferencia WHERE resultado='FALHOU' "
                             "AND erro LIKE 'transitorio:%'").fetchone()[0]
    assert tentativas == 4
    # erro de permissao: isola em EXCECAO, nao fica tentando
    con.execute("UPDATE plano_item SET estado='APROVADO' WHERE estado IN ('COPIADO','CONFLITO')")
    con.execute("DELETE FROM transferencia")
    con.commit()
    for p in Path(dst).rglob("*"):
        if p.is_file() and p.name not in ("igual.pdf", "diferente.pdf"):
            p.unlink()
    r = Motor(con, RcdInstavel(rcd, "403 Forbidden"), src, dst).transferir("T")
    assert r["excecoes"] == 4
    assert con.execute("SELECT COUNT(*) FROM plano_item WHERE estado='EXCECAO'").fetchone()[0] == 4


def test_autenticacao_recusada_para_o_lote_sem_queimar_a_fila(cenario):
    con, rcd, src, dst, tmp = cenario
    for _ in range(5):   # cinco paradas seguidas por 401 nao gastam tentativa nenhuma
        r = Motor(con, RcdInstavel(rcd, "401 Unauthorized: Username or password was incorrect"), src, dst,
                  concorrencia=1, max_tentativas=3).transferir("T")
        assert r["parado_por_autenticacao"]
    assert con.execute("SELECT COUNT(*) FROM plano_item WHERE estado IN ('EXCECAO','ESGOTADO')").fetchone()[0] == 0
    assert con.execute("SELECT COUNT(*) FROM transferencia WHERE resultado='FALHOU'").fetchone()[0] == 0
    # credencial renovada: tudo segue normalmente (o identico e o conflito ja tinham sido decididos)
    r = Motor(con, rcd, src, dst).transferir("T")
    assert r["feitos"] == 4 and r["falhas"] == 0
    e = estados(con)
    assert sum(v == "COPIADO" for v in e.values()) == 5 and list(e.values()).count("CONFLITO") == 1


class RedeFora:
    """Rede fora nas N primeiras copias; depois volta."""
    def __init__(self, rcd, n):
        self.rcd, self.n, self.chamadas = rcd, n, 0

    def __getattr__(self, nome):
        return getattr(self.rcd, nome)

    def copiar(self, *a, **k):
        self.chamadas += 1
        if self.chamadas <= self.n:
            raise ErroRc(500, "dial tcp: connection refused", "operations/copyfile")
        return self.rcd.copiar(*a, **k)


def test_queda_de_rede_espera_com_backoff_crescente_e_testa_com_um_arquivo(cenario, monkeypatch):
    con, rcd, src, dst, tmp = cenario
    monkeypatch.setattr("migra_parceiro.motor.time.sleep", lambda s: None)
    esperas = []
    monkeypatch.setattr(Motor, "_esperar", lambda self, s: esperas.append(s))
    fake = RedeFora(rcd, 14)
    r = Motor(con, fake, src, dst, concorrencia=2, tentativas_por_arquivo=1, max_tentativas=20).transferir("T")
    assert r["feitos"] == 5 and r["falhas"] == 0          # nada esgotado: a rede voltou e tudo foi
    assert esperas and esperas == sorted(esperas)          # backoff cresce com o tempo de queda
    assert len(set(esperas)) > 1


def test_falhou_esgotado_ganha_estado_proprio(cenario, monkeypatch):
    con, rcd, src, dst, tmp = cenario
    monkeypatch.setattr("migra_parceiro.motor.time.sleep", lambda s: None)
    monkeypatch.setattr(Motor, "_esperar", lambda self, s: None)
    r = Motor(con, RedeFora(rcd, 10**6), src, dst, concorrencia=1, tentativas_por_arquivo=1,
              max_tentativas=2).transferir("T")
    n = con.execute("SELECT COUNT(*) FROM plano_item WHERE estado='ESGOTADO'").fetchone()[0]
    assert n == 4 and r["falhas"] == 4                    # 4 a copiar (1 ja existia, 1 conflito)


def test_origem_alterada_nao_copia(cenario):
    con, rcd, src, dst, tmp = cenario
    (Path(src) / "05. Produtos/01 - Julho - 2025/G1/relatório.pdf").write_bytes(b"mudou depois do inventario")
    Motor(con, rcd, src, dst).transferir("T")
    assert estados(con)["05. Produtos/01 - Julho - 2025/G1/relatório.pdf"] == "ORIGEM_ALTERADA"
    assert not (Path(dst) / destino_de("05. Produtos/01 - Julho - 2025/G1/relatório.pdf") / "relatório.pdf").exists()


def test_cliente_rc_nao_sabe_apagar(cenario):
    _con, rcd, src, *_ = cenario
    for metodo in ("operations/delete", "operations/purge", "sync/move", "sync/sync", "operations/movefile"):
        with pytest.raises(ErroRc, match="BLOQUEADO"):
            rcd.chamar(metodo, {"fs": src, "remote": "x"})
