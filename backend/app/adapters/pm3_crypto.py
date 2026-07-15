from __future__ import annotations

from dataclasses import dataclass
from struct import pack, unpack_from


class Pm3CryptoError(ValueError):
    pass


@dataclass(frozen=True)
class Pm3Decryption:
    plaintext: bytes
    slot: int
    header: int
    plain_length: int
    used_cut: bool


# Recovered from the PM3 FPGA implementation. Decryption is an affine bit
# permutation rather than standard software AES.
FPGA_PERM_D0 = (
    95, 32, 33, 34, 35, 36, 37, 38, 39, 24, 25, 26, 27, 28, 29, 30,
    31, 96, 97, 98, 99, 100, 101, 102, 103, 64, 65, 66, 67, 68, 69, 70,
    71, 56, 57, 58, 59, 60, 61, 62, 63, 112, 113, 114, 115, 116, 117, 118,
    119, 72, 73, 74, 75, 76, 77, 78, 79, 88, 89, 90, 91, 92, 93, 94,
    125, 126, 127, 16, 17, 18, 19, 20, 21, 22, 23, 48, 49, 50, 51, 52,
    53, 54, 55, 8, 9, 10, 11, 12, 13, 14, 15, 80, 81, 82, 83, 84,
    85, 86, 87, 40, 41, 42, 43, 44, 45, 46, 47, 104, 105, 106, 107, 108,
    109, 110, 111, 0, 1, 2, 3, 4, 5, 6, 7, 120, 121, 122, 123, 124,
)

FPGA_CONSTANTS = (
    bytes.fromhex("0aa12cb7dd30bd481ead81af5985a64c"),  # a36rom
    bytes.fromhex("4419740c0b9827b4ed10c5b4502f642a"),  # songlist
    bytes.fromhex("4c3c3e2c1938a7bcbd41f5b010a774a3"),  # slot 0
    bytes.fromhex("699ee2c16c0331ecaf42a5d5c2de6320"),  # slot 1
    bytes.fromhex("1b0e4607c44a46838cd3c11472d29f2b"),  # slot 2
    bytes.fromhex("558e5a8e8b7aa4d978dec58472bf084a"),  # slot 3
    bytes.fromhex("2bcbddc04aab19b47572e56c33ddc64a"),  # slot 4
    bytes.fromhex("251b02071d6faef2a2d082d416573ce3"),  # slot 5
    bytes.fromhex("529fa5018c1325d49ff1a4f0d3e4ff92"),  # slot 6
    bytes.fromhex("4ce8e6ed4d0c379696d0d4f55ce7548f"),  # slot 7
    bytes.fromhex("abdc8a67cc0a5de48dc465d277d2dbca"),  # slot 8
    bytes.fromhex("239c12cbe7ee3413e44c4e236bf33486"),  # slot 9
)


def slot_for_header(header: int) -> int:
    return (
        ((header >> 26) & 8)
        | int(bool(header & 0x10))
        | ((header >> 15) & 4)
        | (2 * int(bool(header & 0x400)))
    )


def _decrypt_block(block: bytes, constant: bytes) -> bytes:
    if len(block) != 16:
        raise Pm3CryptoError("PM3 加密块必须为 16 字节")
    permuted = bytearray(16)
    for source_bit, target_bit in enumerate(FPGA_PERM_D0):
        if block[source_bit // 8] & (1 << (source_bit % 8)):
            permuted[target_bit // 8] |= 1 << (target_bit % 8)
    return bytes(value ^ constant[index] for index, value in enumerate(permuted))


def _decrypt_blocks(payload: bytes, constant: bytes) -> bytes:
    if len(payload) % 16:
        raise Pm3CryptoError("PM3 密文长度不是 16 字节的整数倍")
    return b"".join(
        _decrypt_block(payload[offset:offset + 16], constant)
        for offset in range(0, len(payload), 16)
    )


def _encrypt_block(block: bytes, constant: bytes) -> bytes:
    if len(block) != 16:
        raise Pm3CryptoError("PM3 明文块必须为 16 字节")
    # Decryption is D(C) = P(C) xor K, therefore encryption is
    # C = P^-1(D xor K). Apply the inverse permutation explicitly so the
    # implementation stays auditable against the recovered FPGA table.
    mixed = bytes(value ^ constant[index] for index, value in enumerate(block))
    encrypted = bytearray(16)
    for source_bit, target_bit in enumerate(FPGA_PERM_D0):
        if mixed[target_bit // 8] & (1 << (target_bit % 8)):
            encrypted[source_bit // 8] |= 1 << (source_bit % 8)
    return bytes(encrypted)


def _encrypt_blocks(payload: bytes, constant: bytes) -> bytes:
    if len(payload) % 16:
        raise Pm3CryptoError("PM3 明文长度不是 16 字节的整数倍")
    return b"".join(
        _encrypt_block(payload[offset:offset + 16], constant)
        for offset in range(0, len(payload), 16)
    )


def header_for_slot(slot: int) -> int:
    if slot < 0 or slot > 9:
        raise Pm3CryptoError("PM3 key slot 必须在 0..9")
    # Only four header bits participate in slot selection. The remaining
    # fixed pattern makes generated headers non-zero and deterministic.
    header = 0x13572468 & ~((1 << 4) | (1 << 10) | (1 << 17) | (1 << 29))
    header |= (slot & 1) << 4
    header |= ((slot >> 1) & 1) << 10
    header |= ((slot >> 2) & 1) << 17
    header |= ((slot >> 3) & 1) << 29
    if slot_for_header(header) != slot:
        raise Pm3CryptoError("无法构造 PM3 key slot header")
    return header


def _encrypt_container(plaintext: bytes, *, header: int, constant: bytes, slot: int) -> bytes:
    if not plaintext:
        raise Pm3CryptoError("PM3 明文不得为空")
    if len(plaintext) > 2 * 1024 * 1024:
        raise Pm3CryptoError("PM3 明文不得超过 2 MB")
    padding = b"\0" * (-(len(plaintext) + 12) % 16)
    body = plaintext + padding + pack("<III", 1, len(plaintext), slot)
    return pack("<I", header & 0xFFFFFFFF) + _encrypt_blocks(body, constant)


def encrypt_chart(plaintext: bytes, *, header: int | None = None, slot: int = 0) -> bytes:
    if header is None:
        header = header_for_slot(slot)
    actual_slot = slot_for_header(header)
    if actual_slot != slot:
        raise Pm3CryptoError(f"PM3 header 对应 slot {actual_slot}，请求的是 {slot}")
    return _encrypt_container(
        plaintext, header=header, constant=FPGA_CONSTANTS[slot + 2], slot=slot
    )


def encrypt_song_list(plaintext: bytes, *, header: int | None = None) -> bytes:
    if header is None:
        header = header_for_slot(3)
    slot = slot_for_header(header)
    return _encrypt_container(plaintext, header=header, constant=FPGA_CONSTANTS[1], slot=slot)


def decrypt_song_list(payload: bytes) -> Pm3Decryption:
    if len(payload) < 20 or len(payload) % 16 != 4:
        raise Pm3CryptoError("SongList.enc 的容器长度无效")
    header = unpack_from("<I", payload)[0]
    decrypted = _decrypt_blocks(payload[4:], FPGA_CONSTANTS[1])
    valid, plain_length, footer_slot = unpack_from("<III", decrypted, len(decrypted) - 12)
    if valid <= 0 or plain_length >= len(payload):
        raise Pm3CryptoError("SongList.enc 解密校验失败")
    return Pm3Decryption(decrypted[:plain_length], footer_slot, header, plain_length, False)


def decrypt_chart(payload: bytes, *, cut_data: bytes | None = None) -> Pm3Decryption:
    if cut_data is not None:
        if len(cut_data) != 16:
            raise Pm3CryptoError("PM3 cut data 必须为 16 字节")
        encrypted = cut_data[4:] + payload
        header = unpack_from("<I", cut_data)[0]
        used_cut = True
    else:
        if len(payload) < 20 or len(payload) % 16 != 4:
            raise Pm3CryptoError("PM3 .enc 容器长度无效")
        header = unpack_from("<I", payload)[0]
        encrypted = payload[4:]
        used_cut = False
    if len(encrypted) < 16 or len(encrypted) % 16:
        raise Pm3CryptoError("PM3 谱面密文长度无效")
    slot = slot_for_header(header)
    decrypted = _decrypt_blocks(encrypted, FPGA_CONSTANTS[slot + 2])
    valid, plain_length, footer_slot = unpack_from("<III", decrypted, len(decrypted) - 12)
    if valid <= 0:
        raise Pm3CryptoError("PM3 谱面解密有效标记错误")
    if plain_length >= len(encrypted):
        raise Pm3CryptoError("PM3 谱面解密长度校验失败")
    if footer_slot != slot:
        raise Pm3CryptoError(f"PM3 谱面 key slot 校验失败：头部 {slot}，尾部 {footer_slot}")
    return Pm3Decryption(decrypted[:plain_length], slot, header, plain_length, used_cut)

