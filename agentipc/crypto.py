# -*- coding: utf-8 -*-
"""End-to-end encryption for encrypted virtual servers, using PyNaCl.

PyNaCl is optional. It is imported only when an encrypted server is used, so
nodes and clients without it still serve unencrypted servers. PyNaCl 1.4.0 is
the last release that supports Python 2.7.
"""

import base64


class CryptoError(Exception):
    pass


def _secretbox():
    try:
        import nacl.secret
        import nacl.utils
    except ImportError:
        raise CryptoError("encrypted server needs PyNaCl (pip install pynacl)")
    return nacl.secret, nacl.utils


def new_key():
    """A random 32-byte SecretBox key."""
    _, utils = _secretbox()
    return utils.random(32)


def encrypt(key, data):
    """Returns base64 text: nonce plus ciphertext. data is bytes or text."""
    secret, _ = _secretbox()
    if not isinstance(data, bytes):
        data = data.encode("utf-8")
    box = secret.SecretBox(key)
    return base64.b64encode(bytes(box.encrypt(data))).decode("ascii")


def decrypt(key, text):
    """Reverse of encrypt. Returns bytes. Raises CryptoError on a wrong key or tampering."""
    secret, _ = _secretbox()
    box = secret.SecretBox(key)
    try:
        return bytes(box.decrypt(base64.b64decode(text.encode("ascii"))))
    except Exception:
        raise CryptoError("cannot decrypt")
