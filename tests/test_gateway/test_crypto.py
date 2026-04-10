"""AES crypto tests"""

import base64

import pytest

from mocode.gateway.crypto import (
    aes_ecb_decrypt,
    aes_ecb_encrypt,
    parse_aes_key,
    pkcs7_pad,
    pkcs7_unpad,
)


class TestPkcs7:
    def test_pad_unpad_roundtrip(self):
        data = b"hello world"
        padded = pkcs7_pad(data)
        assert len(padded) % 16 == 0
        assert pkcs7_unpad(padded) == data

    def test_pad_full_block(self):
        """Data already at block boundary gets a full block of padding"""
        data = b"a" * 16
        padded = pkcs7_pad(data)
        assert len(padded) == 32
        assert pkcs7_unpad(padded) == data

    def test_unpad_empty(self):
        with pytest.raises(ValueError, match="Empty data"):
            pkcs7_unpad(b"")

    def test_unpad_invalid_padding(self):
        with pytest.raises(ValueError, match="Invalid"):
            pkcs7_unpad(b"\x00" * 16)


class TestAesEcb:
    def test_encrypt_decrypt_roundtrip(self):
        key = b"0123456789abcdef"
        plaintext = b"Hello, AES-128-ECB!"
        encrypted = aes_ecb_encrypt(plaintext, key)
        decrypted = aes_ecb_decrypt(encrypted, key)
        assert decrypted == plaintext

    def test_invalid_key_length(self):
        with pytest.raises(ValueError, match="16 bytes"):
            aes_ecb_encrypt(b"data", b"short")

    def test_invalid_ciphertext_length(self):
        key = b"0123456789abcdef"
        with pytest.raises(ValueError, match="multiple of 16"):
            aes_ecb_decrypt(b"not16bytesalign", key)

    def test_empty_data_roundtrip(self):
        key = b"0123456789abcdef"
        encrypted = aes_ecb_encrypt(b"", key)
        decrypted = aes_ecb_decrypt(encrypted, key)
        assert decrypted == b""


class TestParseAesKey:
    def test_16bytes_raw(self):
        raw_key = b"0123456789abcdef"
        b64 = base64.b64encode(raw_key).decode()
        result = parse_aes_key(b64)
        assert result == raw_key

    def test_32bytes_hex(self):
        hex_str = b"30313233343536373839616263646566"
        b64 = base64.b64encode(hex_str).decode()
        result = parse_aes_key(b64)
        assert result == bytes.fromhex(hex_str.decode())

    def test_invalid_length(self):
        raw = b"too_short"
        b64 = base64.b64encode(raw).decode()
        with pytest.raises(ValueError, match="Cannot parse AES key"):
            parse_aes_key(b64)
