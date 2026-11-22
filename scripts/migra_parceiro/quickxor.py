"""
quickxor - QuickXorHash, o hash que o SharePoint calcula no servidor.

Portado de PROG_Cerebro_Documental/scripts/quickxor.py (mesma titular), com um
caminho vetorizado em numpy: o XOR de todos os bytes que caem na mesma faixa
(posicao mod 160) e uma reducao por coluna, feita em C. O laco Python restante
roda 160 vezes por bloco, independente do tamanho do bloco. Isso leva a
conferencia de ~15 MB/s (Python puro) para a ordem de centenas de MB/s.

Usado na conferencia (336): os bytes lidos DO PARCEIRO passam por aqui e o
resultado e comparado ao quickXorHash que a Microsoft calculou sobre os bytes
da ORIGEM. Mesmo algoritmo dos dois lados - nunca comparado com SHA-256.
Nao e hash criptografico; o SHA-256 e calculado junto, na mesma leitura.
"""

from __future__ import annotations

import base64

try:
    import numpy as _np
except ImportError:  # pragma: no cover - numpy vem com pandas nesta maquina
    _np = None

LARGURA_BITS = 160
DESLOCAMENTO = 11
BITS_ULTIMA_CELULA = 32
CELULAS = (LARGURA_BITS - 1) // 64 + 1
MASCARA64 = (1 << 64) - 1


class QuickXorHash:
    def __init__(self):
        self._dados = [0] * CELULAS
        self._deslocamento = 0
        self._tamanho = 0

    def _combinados(self, bloco: bytes) -> list[int]:
        n = len(bloco)
        if _np is not None and n >= 4 * LARGURA_BITS:
            arr = _np.frombuffer(bloco, dtype=_np.uint8)
            cheio = (n // LARGURA_BITS) * LARGURA_BITS
            comb = _np.bitwise_xor.reduce(arr[:cheio].reshape(-1, LARGURA_BITS), axis=0)
            if cheio < n:
                cauda = arr[cheio:]
                comb[: len(cauda)] ^= cauda
            return comb.tolist()
        comb = [0] * min(n, LARGURA_BITS)
        for i in range(len(comb)):
            x = 0
            for j in range(i, n, LARGURA_BITS):
                x ^= bloco[j]
            comb[i] = x
        return comb

    def update(self, bloco: bytes) -> "QuickXorHash":
        if not bloco:
            return self
        n = len(bloco)
        indice = self._deslocamento // 64
        offset = self._deslocamento % 64
        for combinado in self._combinados(bloco)[: min(n, LARGURA_BITS)]:
            ultima = indice == CELULAS - 1
            bits_da_celula = BITS_ULTIMA_CELULA if ultima else 64
            if offset <= bits_da_celula - 8:
                self._dados[indice] ^= (combinado << offset) & MASCARA64
            else:
                seguinte = 0 if ultima else indice + 1
                resto = bits_da_celula - offset
                self._dados[indice] ^= (combinado << offset) & MASCARA64
                self._dados[seguinte] ^= (combinado >> resto) & MASCARA64
            offset += DESLOCAMENTO
            while offset >= bits_da_celula:
                indice = 0 if ultima else indice + 1
                offset -= bits_da_celula
        self._deslocamento = (self._deslocamento + DESLOCAMENTO * (n % LARGURA_BITS)) % LARGURA_BITS
        self._tamanho += n
        return self

    def digest(self) -> bytes:
        rgb = bytearray((LARGURA_BITS - 1) // 8 + 1)
        for i in range(CELULAS - 1):
            rgb[i * 8:(i + 1) * 8] = (self._dados[i] & MASCARA64).to_bytes(8, "little")
        sobra = len(rgb) - (CELULAS - 1) * 8
        rgb[(CELULAS - 1) * 8:] = (self._dados[-1] & MASCARA64).to_bytes(8, "little")[:sobra]
        comprimento = (self._tamanho & MASCARA64).to_bytes(8, "little")
        inicio = LARGURA_BITS // 8 - 8
        for i, byte in enumerate(comprimento):
            rgb[inicio + i] ^= byte
        return bytes(rgb)

    def base64(self) -> str:
        return base64.b64encode(self.digest()).decode("ascii")


def de_bytes(dados: bytes) -> str:
    return QuickXorHash().update(dados).base64()
