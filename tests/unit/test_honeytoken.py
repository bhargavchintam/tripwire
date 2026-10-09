from __future__ import annotations

import base64
import random

from checkpoint.honeytoken import scan
from tests.unit.conftest import TOKENS

TOK = TOKENS[0]


def test_raw_hit():
    assert scan(f"key={TOK}&x=1", TOKENS)
    assert scan(f"prefix {TOKENS[1]} suffix", TOKENS)


def test_empty_and_no_tokens():
    assert not scan("", TOKENS)
    assert not scan(f"key={TOK}", [])
    assert not scan(f"key={TOK}", [""])


def test_base64_standard():
    enc = base64.b64encode(f"export AWS_ACCESS_KEY_ID={TOK}\n".encode()).decode()
    assert TOK not in enc
    assert scan(f'{{"data": "{enc}"}}', TOKENS)


def test_base64_urlsafe_unpadded():
    raw = b"\xfb\xef\xbe\xff" + f"token={TOK}".encode() + b"\xfb\xff"
    enc = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    assert "-" in enc or "_" in enc  # genuinely url-safe alphabet
    assert scan(f"https://x.example/?d={enc}", TOKENS)


def test_base64_glued_to_word_chars():
    enc = base64.b64encode(f"secret {TOK}".encode()).decode()
    for prefix in ("a", "ab", "abc"):
        assert scan(prefix + enc, TOKENS), prefix


def test_nested_base64():
    inner = base64.b64encode(f"k={TOK}".encode())
    outer = base64.b64encode(inner).decode()
    assert scan(outer, TOKENS)


def test_no_false_positive_on_random_base64():
    for seed in range(200):
        rnd = random.Random(seed)
        blob = rnd.randbytes(rnd.randint(9, 300))
        assert not scan(base64.b64encode(blob).decode(), TOKENS)
        assert not scan(base64.urlsafe_b64encode(blob).decode(), TOKENS)


def test_no_false_positive_on_benign_encoded_text():
    enc = base64.b64encode(b"deploy ok: build 4821 shipped to staging").decode()
    assert not scan(enc, TOKENS)
    assert not scan("AKIA-TRIPWIRE-DECOY-7Q", TOKENS)  # truncated token is not the token
