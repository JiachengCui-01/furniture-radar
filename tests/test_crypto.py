import json
import shutil
import subprocess

import pytest

from radar import crypto
from radar.report import shell


def test_json_roundtrip(master_key):
    key = crypto.data_key(crypto.parse_master_key(master_key))
    blob = crypto.encrypt_json(key, {"中文": [1, 2, None]})
    assert b"\xe4\xb8\xad" not in blob  # 密文里不应出现明文
    assert crypto.decrypt_json(key, blob) == {"中文": [1, 2, None]}


def test_wrong_key_fails(master_key):
    key = crypto.data_key(crypto.parse_master_key(master_key))
    other = crypto.data_key(crypto.parse_master_key(crypto.generate_master_key()))
    blob = crypto.encrypt_json(key, {"a": 1})
    with pytest.raises(Exception):
        crypto.decrypt_json(other, blob)


def test_report_keys_are_per_report(master_key):
    master = crypto.parse_master_key(master_key)
    assert crypto.report_key(master, "2026-10-08") == crypto.report_key(master, "2026-10-08")
    assert crypto.report_key(master, "2026-10-08") != crypto.report_key(master, "2026-10-11")
    assert crypto.report_key(master, "2026-10-08") != crypto.data_key(master)


def test_bad_master_key():
    with pytest.raises(crypto.MasterKeyError):
        crypto.parse_master_key("")
    with pytest.raises(crypto.MasterKeyError):
        crypto.parse_master_key("c2hvcnQ")


@pytest.mark.skipif(shutil.which("node") is None, reason="需要 Node.js 验证浏览器端解密")
@pytest.mark.parametrize("use_master", [False, True])
def test_browser_decryptor_matches_python(master_key, use_master, tmp_path):
    """浏览器端 WebCrypto（用 Node 的同一套 API 运行）必须能解开 Python 加密的报告，
    并且粘贴主密钥时能在浏览器里派生出同样的报告密钥。"""
    master = crypto.parse_master_key(master_key)
    report_id = "2026-10-08"
    envelope = crypto.encrypt(crypto.report_key(master, report_id), "<h1>家具爆品雷达</h1>".encode(),
                              crypto.report_aad(report_id))
    payload = {**envelope, "id": report_id}
    key_text = master_key if use_master else crypto.report_key_text(master, report_id)
    script = shell.DECRYPT_JS + f"""
openReport({json.dumps(key_text)}, {json.dumps(payload)}).then(r => {{
  console.log(JSON.stringify(r));
}}).catch(e => {{ console.error(e); process.exit(1); }});
"""
    path = tmp_path / "check.mjs"
    path.write_text(script, encoding="utf-8")
    out = subprocess.run(["node", str(path)], capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert out.returncode == 0, out.stderr
    result = json.loads(out.stdout)
    assert result["html"] == "<h1>家具爆品雷达</h1>"
    assert result["key"] == crypto.report_key_text(master, report_id)
