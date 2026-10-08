"""加密：历史数据和报告都用 AES-256-GCM 加密后才发布到公开仓库/网站。

主密钥 REPORT_KEY（32 字节，base64url）只存在 GitHub Secrets / 本地 .env。
用 HKDF-SHA256 派生：
* 数据密钥：加密 site/data/state.enc
* 报告密钥：每份报告一把（salt = 报告 ID），放在钉钉链接的 #k= 里。
  泄露一个链接只暴露那一份报告，拿不到历史数据。
浏览器端用 WebCrypto 做同样的 HKDF / AES-GCM（见 report/shell.py），两边必须保持一致。
"""
from __future__ import annotations

import base64
import gzip
import json
import os

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

DATA_INFO = b"data-v1"
REPORT_INFO = b"report-v1"
DATA_SALT = b"radar-data"


class MasterKeyError(ValueError):
    """主密钥缺失或格式不对。"""


def b64u_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def b64u_decode(text: str) -> bytes:
    text = text.strip()
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def generate_master_key() -> str:
    return b64u_encode(os.urandom(32))


def parse_master_key(text: str) -> bytes:
    if not text:
        raise MasterKeyError("缺少 REPORT_KEY。用 `python -m radar keygen` 生成一个，并保存到 Secrets / .env")
    try:
        raw = b64u_decode(text)
    except Exception as exc:  # noqa: BLE001
        raise MasterKeyError("REPORT_KEY 不是合法的 base64url 字符串") from exc
    if len(raw) != 32:
        raise MasterKeyError(f"REPORT_KEY 解码后应为 32 字节，实际 {len(raw)} 字节")
    return raw


def _hkdf(master: bytes, salt: bytes, info: bytes) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=salt, info=info).derive(master)


def data_key(master: bytes) -> bytes:
    return _hkdf(master, DATA_SALT, DATA_INFO)


def report_key(master: bytes, report_id: str) -> bytes:
    return _hkdf(master, report_id.encode("utf-8"), REPORT_INFO)


def report_key_text(master: bytes, report_id: str) -> str:
    return b64u_encode(report_key(master, report_id))


def report_aad(report_id: str) -> bytes:
    return f"v1|{report_id}".encode("utf-8")


def encrypt(key: bytes, plaintext: bytes, aad: bytes) -> dict:
    iv = os.urandom(12)
    ct = AESGCM(key).encrypt(iv, plaintext, aad)  # ct || tag，与 WebCrypto 格式一致
    return {"v": 1, "iv": b64u_encode(iv), "ct": b64u_encode(ct)}


def decrypt(key: bytes, envelope: dict, aad: bytes) -> bytes:
    return AESGCM(key).decrypt(b64u_decode(envelope["iv"]), b64u_decode(envelope["ct"]), aad)


def encrypt_json(key: bytes, obj, aad: bytes = b"state-v1") -> bytes:
    raw = gzip.compress(json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    return json.dumps(encrypt(key, raw, aad)).encode("ascii")


def decrypt_json(key: bytes, blob: bytes, aad: bytes = b"state-v1"):
    envelope = json.loads(blob.decode("ascii"))
    return json.loads(gzip.decompress(decrypt(key, envelope, aad)).decode("utf-8"))
