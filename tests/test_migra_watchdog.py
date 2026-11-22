import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from migra_parceiro.watchdog import Evidencias, decidir  # noqa: E402


def ev(**k):
    base = dict(runners_vivos=1, batimento_min=1.0, log_min=1.0, progresso_min=1.0, pausar=False,
                parada_global=False, disco_livre_gib=80, reserva_gib=40, retentaveis=100, reinicios_24h=0,
                ultimo_reinicio_min=None)
    base.update(k)
    return Evidencias(**base)


def test_saudavel_nao_interfere():
    assert decidir(ev()).acao == "nenhuma"


def test_arquivo_grande_sem_conclusao_nao_e_travamento():
    # 3 h sem concluir arquivo e progresso parado, mas o motor bate: SAUDAVEL
    d = decidir(ev(batimento_min=0.8, log_min=180, progresso_min=180))
    assert d.situacao == "SAUDAVEL" and d.acao == "nenhuma"


def test_entre_lotes_sem_trava_e_saudavel():
    assert decidir(ev(batimento_min=None, log_min=2)).situacao == "SAUDAVEL"


def test_uma_evidencia_isolada_nao_derruba_processo():
    d = decidir(ev(batimento_min=25, log_min=5, progresso_min=40))
    assert d.situacao == "OBSERVAR" and d.acao == "nenhuma"


def test_travado_com_todas_as_evidencias():
    d = decidir(ev(batimento_min=40, log_min=40, progresso_min=60))
    assert d.situacao == "TRAVADO" and d.acao == "encerrar_e_reiniciar"


def test_travado_mas_limite_de_reinicios():
    d = decidir(ev(batimento_min=40, log_min=40, progresso_min=60, reinicios_24h=4))
    assert d.situacao == "IMPEDIMENTO" and d.humano


def test_morto_com_pendentes_reinicia():
    assert decidir(ev(runners_vivos=0, batimento_min=None)).acao == "reiniciar"


def test_morto_e_concluido_nao_reinicia():
    assert decidir(ev(runners_vivos=0, retentaveis=0)).situacao == "CONCLUIDO"


def test_pausa_humana_respeitada():
    assert decidir(ev(runners_vivos=0, pausar=True)).situacao == "PAUSADO"


def test_401_ou_cota_nao_reinicia():
    d = decidir(ev(runners_vivos=0, parada_global=True))
    assert d.situacao == "IMPEDIMENTO" and d.acao == "registrar" and d.humano


def test_disco_baixo_nao_reinicia():
    assert decidir(ev(runners_vivos=0, disco_livre_gib=35)).situacao == "IMPEDIMENTO"


def test_backoff_e_limite_evitam_ciclo_infinito():
    assert decidir(ev(runners_vivos=0, ultimo_reinicio_min=5)).situacao == "AGUARDAR"
    assert decidir(ev(runners_vivos=0, reinicios_24h=4)).situacao == "IMPEDIMENTO"


def test_duplicado_nao_age():
    d = decidir(ev(runners_vivos=2))
    assert d.situacao == "DUPLICADO" and d.acao == "registrar"
