"""
espaco - guarda de disco local da migracao.

Antes de cada envio o motor pergunta `pode_iniciar(tamanho)`. A resposta e
NAO quando o arquivo, somado ao que ja esta em uso, deixaria o disco abaixo
da reserva minima ou estouraria o teto de temporarios PROPRIOS da migracao.

So mede e decide. Nunca apaga nada fora de `tmp/migracao_parceiro` e nunca
mexe em cache de outro programa (OneDrive, montagem PARCEIRO).
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

GIB = 2**30


def uso_pasta(pasta: Path) -> int:
    if not pasta.exists():
        return 0
    return sum(p.stat().st_size for p in pasta.rglob("*") if p.is_file())


@dataclass
class Guarda:
    disco: Path                 # qualquer caminho no disco a proteger (ex.: C:\)
    tmp: Path                   # temporarios proprios da migracao
    reserva_minima: int         # bytes livres que nunca podem ser consumidos
    teto_temporario: int        # soma maxima dos temporarios proprios
    extra_externo: int = 0      # caches de terceiros que o envio vai encher (ex.: VFS da montagem)

    @classmethod
    def do_config(cls, raiz: Path, c: dict) -> "Guarda":
        e = c["espaco"]
        return cls(Path(raiz.anchor), raiz / e["tmp"], int(e["reserva_minima_gib"]) * GIB,
                   int(e["teto_temporario_gib"]) * GIB)

    def livre(self) -> int:
        return shutil.disk_usage(self.disco).free

    def pode_iniciar(self, bytes_local: int, em_voo: int = 0) -> tuple[bool, str]:
        """bytes_local: quanto este arquivo vai ocupar no disco (0 no streaming puro).
        em_voo: bytes locais ja reservados por envios em andamento."""
        livre = self.livre()
        if livre - bytes_local - em_voo - self.extra_externo < self.reserva_minima:
            return False, (f"reserva: livre {livre / GIB:.1f} GiB, precisaria "
                           f"{(bytes_local + em_voo) / GIB:.1f} GiB, reserva "
                           f"{self.reserva_minima / GIB:.0f} GiB")
        if uso_pasta(self.tmp) + bytes_local + em_voo > self.teto_temporario:
            return False, f"teto de temporarios ({self.teto_temporario / GIB:.0f} GiB)"
        return True, "ok"
