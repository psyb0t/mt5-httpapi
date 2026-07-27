#!/usr/bin/env python3
"""Codec for the MT5 ``[Experts] WebRequestUrl=`` allowlist blob (Config/common.ini).

The WebRequest allowed-URL list (Tools -> Options -> Expert Advisors) is stored in
each terminal's ``Config/common.ini`` as ``WebRequestUrl=<hex>``.  The hex is a
length-preserving encrypted blob produced by terminal64.exe.  It is
NOT machine-bound: the key is fixed in the binary, so a blob generated here is
accepted verbatim by any terminal of the same build.  That lets us provision the
allowlist programmatically (no RDP / no Options dialog), which is what Chart
Deployments needs so deployed EAs can call WebRequest without manual setup.

FORMAT (fully reverse-engineered from terminal64.exe fn @0x7ff7824d4010):
  ini hex string  --%04X per uint16, stored LE-->  ciphertext bytes  (== swap16 of naive hex)
  plaintext = u16le(1) + u16le(checksum) + utf16le(";".join(urls))
    where checksum = (sum of every UTF-16 code unit in the joined string) & 0xffff
  cipher (decode, config-load direction) is a byte-wise CFB stream:
    p[i] = ((c[i-1] + KEY[i % 16]) & 0xff) ^ c[i]      with c[-1] = 0
  encode is the exact inverse (feedback taken from the ciphertext byte).

The leading u16 is a constant 1 in every observed blob.  The checksum lives at the
FRONT of the plaintext, so editing any URL character changes it and re-ciphers the
whole tail -- this is why the blob looked like a strong full-avalanche cipher in
black-box testing when it is really a single CFB pass plus a front checksum.

Verified: round-trips byte-identically against 18 independent real broker-terminal
blobs (BlackBull, IC Markets, FP Markets, Darwinex, Ducascopy, AquaFunded, ...).
"""
from __future__ import annotations

# 16-byte key, recovered by cryptanalysis of the CFB recurrence and confirmed
# against 50+ plaintext bytes and 18 full-blob round-trips.  (The binary derives
# it at runtime via an obfuscated routine from a .rdata seed; the derived bytes
# are what matter and are reproduced here directly.)
KEY = bytes([0xe2, 0x30, 0x54, 0xb4, 0xde, 0xe5, 0xcc, 0x04,
             0x9c, 0x70, 0x8f, 0x3c, 0x6b, 0x87, 0x78, 0xf0])


def _swap16(data: bytes) -> bytes:
    b = bytearray(data)
    for i in range(0, len(b) - 1, 2):
        b[i], b[i + 1] = b[i + 1], b[i]
    return bytes(b)


def _decode_cfb(ct: bytes) -> bytes:
    out = bytearray(len(ct))
    prev = 0
    for i, c in enumerate(ct):
        out[i] = ((prev + KEY[i % 16]) & 0xff) ^ c
        prev = c
    return bytes(out)


def _encode_cfb(pt: bytes) -> bytes:
    out = bytearray(len(pt))
    prev = 0
    for i, p in enumerate(pt):
        c = ((prev + KEY[i % 16]) & 0xff) ^ p
        out[i] = c
        prev = c
    return bytes(out)


def _checksum(s: str) -> int:
    # sum over UTF-16 code units (BMP chars == ord); & 0xffff
    return sum(b[0] | (b[1] << 8)
               for b in (s.encode("utf-16-le")[i:i + 2]
                         for i in range(0, len(s) * 2, 2))) & 0xffff


def decode_blob(hex_blob: str) -> list[str]:
    """Ciphertext hex (value of ``WebRequestUrl=``) -> list of URL strings."""
    pt = _decode_cfb(_swap16(bytes.fromhex(hex_blob.strip())))
    body = pt[4:].decode("utf-16-le")
    return body.split(";") if body else []


def encode_urls(urls: list[str]) -> str:
    """List of URL strings -> uppercase ciphertext hex for ``WebRequestUrl=``."""
    joined = ";".join(urls)
    pt = (1).to_bytes(2, "little") + _checksum(joined).to_bytes(2, "little") \
        + joined.encode("utf-16-le")
    return _swap16(_encode_cfb(pt)).hex().upper()


# --- embedded verified test vectors (real captured broker blobs) ---
_VECTORS = [
    ("13E33B7856715A56F2822DF172EBC0D0BD8DF23E89A42B2716A602C68D06506070409EEA021DA6A29"
     "525874B0D86E7F7D8A8FC4898B30E0A3DCDFBBF9D166474552580CC55704642FD8D1DE13AB3CADA08D8"
     "DC28AFCA0C080292FABED14A1828BA8A1B67BCD700FC69F9D094E55E2A3AAE7E17637D986B67D868511"
     "562DB",
     ["https://tracker.algotradingspace.com", "https://api.telegram.org"]),
    ("13E33B7E56715A56F2822DF172EBC0D0BD8DF23E96B1070332C2DCA0AD263444A7774E9A324DA39FCE5"
     "E783C0982D0E0CC9CF7439FBA",
     ["https://aaaaaaaaaaaaaa.co"]),
]


def _selftest() -> None:
    for blob, urls in _VECTORS:
        blob = blob.replace("\n", "")
        assert decode_blob(blob) == urls, decode_blob(blob)
        assert encode_urls(urls) == blob, encode_urls(urls)
    print("webrequest_allowlist_codec: self-test OK (%d vectors)" % len(_VECTORS))


if __name__ == "__main__":
    import sys
    if len(sys.argv) == 1 or sys.argv[1] == "--selftest":
        _selftest()
    elif sys.argv[1] == "decode":
        for u in decode_blob(sys.argv[2]):
            print(u)
    elif sys.argv[1] == "encode":
        print(encode_urls(sys.argv[2:]))
    else:
        print("usage: webrequest_allowlist_codec.py [--selftest | decode <hex> | encode <url>...]")
