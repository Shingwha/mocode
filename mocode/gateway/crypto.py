"""AES-128-ECB encryption utilities for WeChat CDN media"""

import base64


def pkcs7_pad(data: bytes, block_size: int = 16) -> bytes:
    """PKCS7 padding."""
    pad_len = block_size - (len(data) % block_size)
    return data + bytes([pad_len] * pad_len)


def pkcs7_unpad(data: bytes) -> bytes:
    """PKCS7 unpadding with validation."""
    if not data:
        raise ValueError("Empty data")
    pad_len = data[-1]
    if pad_len < 1 or pad_len > 16:
        raise ValueError(f"Invalid padding byte: {pad_len}")
    if data[-pad_len:] != bytes([pad_len] * pad_len):
        raise ValueError("Invalid PKCS7 padding")
    return data[:-pad_len]


def _do_encrypt(cipher, data: bytes) -> bytes:
    """Encrypt using a pycryptodome or cryptography cipher object."""
    if hasattr(cipher, "update"):  # cryptography
        return cipher.update(data) + cipher.finalize()
    return cipher.encrypt(data)  # pycryptodome


def _do_decrypt(cipher, data: bytes) -> bytes:
    """Decrypt using a pycryptodome or cryptography cipher object."""
    if hasattr(cipher, "update"):
        return cipher.update(data) + cipher.finalize()
    return cipher.decrypt(data)


def _get_cipher(key: bytes):
    """Get AES ECB encryptor, trying pycryptodome then cryptography."""
    try:
        from Crypto.Cipher import AES
        return AES.new(key, AES.MODE_ECB)
    except ImportError:
        pass
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    return Cipher(algorithms.AES(key), modes.ECB()).encryptor()


def _get_decipher(key: bytes):
    """Get AES ECB decryptor, trying pycryptodome then cryptography."""
    try:
        from Crypto.Cipher import AES
        return AES.new(key, AES.MODE_ECB)
    except ImportError:
        pass
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    return Cipher(algorithms.AES(key), modes.ECB()).decryptor()


def aes_ecb_encrypt(data: bytes, key: bytes) -> bytes:
    """AES-128-ECB encrypt with PKCS7 padding."""
    if len(key) != 16:
        raise ValueError(f"Key must be 16 bytes, got {len(key)}")
    return _do_encrypt(_get_cipher(key), pkcs7_pad(data))


def aes_ecb_decrypt(data: bytes, key: bytes) -> bytes:
    """AES-128-ECB decrypt with PKCS7 unpadding."""
    if len(key) != 16:
        raise ValueError(f"Key must be 16 bytes, got {len(key)}")
    if len(data) % 16 != 0:
        raise ValueError(f"Ciphertext must be multiple of 16 bytes, got {len(data)}")
    return pkcs7_unpad(_do_decrypt(_get_decipher(key), data))


def parse_aes_key(b64_key: str) -> bytes:
    """Parse AES key from base64 string.

    Handles two formats:
    - base64 of raw 16 bytes (decoded length == 16)
    - base64 of 32-char hex string (decoded length == 32, convert hex->bytes->16 bytes)
    """
    raw = base64.b64decode(b64_key)
    if len(raw) == 16:
        return raw
    if len(raw) == 32:
        return bytes.fromhex(raw.decode("ascii"))
    raise ValueError(f"Cannot parse AES key: decoded length {len(raw)}, expected 16 or 32")
