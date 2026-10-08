"""DeepSeekHashV1: SHA3-256 with Keccak-f[1600] round 0 skipped (rounds 1..23).

Pure Python, with Keccak state precomputation after the fixed prefix so each
nonce trial only absorbs a few bytes.
"""

from __future__ import annotations

# Keccak round constants (24 rounds; index 0 is skipped by DeepSeekHashV1)
_RC = [
    0x0000000000000001, 0x0000000000008082, 0x800000000000808A,
    0x8000000080008000, 0x000000000000808B, 0x0000000080000001,
    0x8000000080008081, 0x8000000000008009, 0x000000000000008A,
    0x0000000000000088, 0x0000000080008009, 0x000000008000000A,
    0x000000008000808B, 0x800000000000008B, 0x8000000000008089,
    0x8000000000008003, 0x8000000000008002, 0x8000000000000080,
    0x000000000000800A, 0x800000008000000A, 0x8000000080008081,
    0x8000000000008080, 0x0000000080000001, 0x8000000080008008,
]

_ROT = [
    [0, 36, 3, 41, 18],
    [1, 44, 10, 45, 2],
    [62, 6, 43, 15, 61],
    [28, 55, 25, 21, 56],
    [27, 20, 39, 8, 14],
]

_MASK = (1 << 64) - 1


def _keccak_f(s: list[int], rounds: range = range(1, 24)) -> None:
    for rnd in rounds:
        # theta
        c = [s[x] ^ s[x + 5] ^ s[x + 10] ^ s[x + 15] ^ s[x + 20] for x in range(5)]
        d = [c[(x - 1) % 5] ^ (((c[(x + 1) % 5] << 1) | (c[(x + 1) % 5] >> 63)) & _MASK)
             for x in range(5)]
        for x in range(5):
            for y in range(5):
                s[x + 5 * y] ^= d[x]
        # rho + pi
        b = [0] * 25
        for x in range(5):
            for y in range(5):
                i = x + 5 * y
                r = _ROT[x][y]
                v = s[i]
                v = ((v << r) | (v >> (64 - r))) & _MASK if r else v
                b[y + 5 * ((2 * x + 3 * y) % 5)] = v
        # chi
        for y in range(5):
            row = y * 5
            t = b[row:row + 5]
            for x in range(5):
                s[row + x] = t[x] ^ ((~t[(x + 1) % 5] & _MASK) & t[(x + 2) % 5])
        # iota
        s[0] ^= _RC[rnd]


def _new_state() -> list[int]:
    return [0] * 25


def _xor_block(state: list[int], block: bytes) -> None:
    for i in range(len(block) // 8):
        state[i] ^= int.from_bytes(block[i * 8:(i + 1) * 8], "little")


def deepseek_hash_v1(data: bytes) -> bytes:
    """Full DeepSeekHashV1 of data (suffix 0x06, rate 136, 32-byte digest)."""
    state = _new_state()
    rate = 136
    n = len(data) // rate
    for i in range(n):
        _xor_block(state, data[i * rate:(i + 1) * rate])
        _keccak_f(state)
    rem = data[n * rate:]
    block = bytearray(rate)
    block[:len(rem)] = rem
    block[len(rem)] ^= 0x06
    block[rate - 1] ^= 0x80
    _xor_block(state, bytes(block))
    _keccak_f(state)
    out = b"".join(v.to_bytes(8, "little") for v in state)
    return out[:32]


class Solver:
    """Precomputes Keccak state after the fixed prefix for fast trials."""

    def __init__(self, salt: str, expire_at: int) -> None:
        self.prefix = f"{salt}_{expire_at}_".encode()
        rate = 136
        self._base = _new_state()
        data = self.prefix
        n = len(data) // rate
        for i in range(n):
            _xor_block(self._base, data[i * rate:(i + 1) * rate])
            _keccak_f(self._base)
        self._rem = data[n * rate:]

    def digest(self, nonce: int) -> bytes:
        msg = self._rem + str(nonce).encode()
        rate = 136
        # nonces are small; prefix remainder + nonce fits in ≤2 blocks
        assert len(msg) < 2 * rate - 1
        state = list(self._base)
        if len(msg) < rate:
            block = bytearray(rate)
            block[:len(msg)] = msg
            block[len(msg)] ^= 0x06
            block[rate - 1] ^= 0x80
            _xor_block(state, bytes(block))
        else:
            _xor_block(state, msg[:rate])
            _keccak_f(state)
            rest = msg[rate:]
            block = bytearray(rate)
            block[:len(rest)] = rest
            block[len(rest)] ^= 0x06
            block[rate - 1] ^= 0x80
            _xor_block(state, bytes(block))
        _keccak_f(state)
        return b"".join(v.to_bytes(8, "little") for v in state)[:32]

    def solve(self, challenge_hex: str, difficulty: int) -> int:
        target = bytes.fromhex(challenge_hex)
        for nonce in range(difficulty):
            if self.digest(nonce) == target:
                return nonce
        raise RuntimeError("PoW: no nonce in range")


def solve_challenge(challenge: dict) -> int:
    return Solver(challenge["salt"], challenge["expire_at"]).solve(
        challenge["challenge"], challenge["difficulty"])
