"""命令行入口：python -m radar <命令>"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import crypto, log, pipeline, store
from .config import PROJECT_ROOT, load_config, load_secrets, report_base_url


def _utf8_stdout() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass


def cmd_run(args, cfg, secrets) -> int:
    result = pipeline.run(cfg, secrets, args.site_dir, force=args.force, budget=args.budget,
                          notify=args.notify)
    if args.github_output and os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as fh:
            fh.write(f"published={'true' if result.published else 'false'}\n")
            if result.report_id:
                fh.write(f"report_id={result.report_id}\n")
    return 0


def cmd_notify(args, cfg, secrets) -> int:
    results = pipeline.send_last(cfg, secrets, args.site_dir)
    return 0 if results and all(results) else 1


def cmd_alert(args, cfg, secrets) -> int:
    from .notify import dingtalk

    if not secrets.dingtalk_webhooks:
        log.warn("未配置 DINGTALK_WEBHOOK，无法发送失败告警")
        return 0
    text = dingtalk.failure_text(cfg["report"]["title"], args.stage, args.run_url or "")
    dingtalk.send(secrets.dingtalk_webhooks, secrets.dingtalk_secrets, dingtalk.text_message(text))
    return 0


def cmd_demo(args, cfg, secrets) -> int:
    """用模拟数据跑两期，生成可以直接用浏览器打开的报告（不花积分）。"""
    from .demo import SyntheticWorld
    from .config import Secrets

    out = Path(args.out)
    site = out / "site"
    if site.exists():
        for path in sorted(site.rglob("*"), reverse=True):
            path.unlink() if path.is_file() else path.rmdir()
    key = secrets.report_key or crypto.generate_master_key()
    demo_secrets = Secrets(report_key=key, report_base_url="", llm_api_key=secrets.llm_api_key if args.llm else "")
    # 演示不花钱：每期全部刷新、探索整个基线
    cfg = {**cfg, "budget": {**cfg["budget"], "stable_refresh_every": 1, "per_run": 400, "bootstrap_run": 400},
           "pool": {**cfg["pool"], "explore_per_run": 400}}
    now = datetime.now(timezone.utc) - timedelta(days=3 * (args.runs - 1))
    world = SyntheticWorld(now.date())
    result = None
    for i in range(args.runs):
        result = pipeline.run(cfg, demo_secrets, site, transport=world, now=now, force=True)
        world.advance(3)
        now += timedelta(days=3)
    master = crypto.parse_master_key(key)
    plain = out / "report.html"
    blob = (site / "reports" / f"{result.report_id}.html").read_text(encoding="utf-8")
    plain.write_text(_decrypt_page(blob, master), encoding="utf-8")
    key_text = crypto.report_key_text(master, result.report_id)
    log.info(f"演示报告（明文）：{plain.resolve()}")
    log.info(f"演示报告（加密版，带密钥打开）：{(site / 'reports' / (result.report_id + '.html')).resolve().as_uri()}#k={key_text}")
    log.info(f"模拟调用 {len(world.calls)} 次（未花费任何积分）")
    return 0


def _decrypt_page(page: str, master: bytes) -> str:
    start = page.index('<script id="payload" type="application/json">') + len('<script id="payload" type="application/json">')
    payload = json.loads(page[start: page.index("</script>", start)])
    key = crypto.report_key(master, payload["id"])
    return crypto.decrypt(key, payload, crypto.report_aad(payload["id"])).decode("utf-8")


def cmd_decrypt(args, cfg, secrets) -> int:
    master = crypto.parse_master_key(secrets.report_key)
    target = Path(args.file)
    if target.name == "state.enc":
        state = store.load(target.parent.parent, master)
        output = json.dumps(state, ensure_ascii=False, indent=1)
    else:
        output = _decrypt_page(target.read_text(encoding="utf-8"), master)
    out = Path(args.output or (Path("out") / (target.stem + ("-state.json" if target.name == "state.enc" else "-plain.html"))))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(output, encoding="utf-8")
    log.info(f"已解密到 {out.resolve()}（请勿提交或外传）")
    return 0


def cmd_keygen(args, cfg, secrets) -> int:
    print(crypto.generate_master_key())
    log.warn("请把这个密钥保存到 GitHub Secrets（REPORT_KEY）和你的密码管理器。丢失后历史数据无法恢复。")
    return 0


def _live_client(secrets):
    from .mcp_client import McpClient

    if not secrets.sellersprite_key:
        raise SystemExit("缺少 SELLERSPRITE_SECRET_KEY（写在 .env 里）")
    return McpClient(secrets.sellersprite_url, headers={"secret-key": secrets.sellersprite_key},
                     client_name="furniture-radar")


def cmd_probe(args, cfg, secrets) -> int:
    """列出卖家精灵 MCP 的全部工具和参数；可选调用一次工具并保存原始响应（会消耗积分）。"""
    client = _live_client(secrets)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    tools = client.list_tools()
    (out / "tools.json").write_text(json.dumps(
        [{"name": t.name, "description": t.description, "input_schema": t.input_schema} for t in tools],
        ensure_ascii=False, indent=1), encoding="utf-8")
    log.info(f"共 {len(tools)} 个工具，完整参数已保存到 {out / 'tools.json'}")
    for t in tools:
        props = t.input_schema.get("properties") or {}
        if "request" in props:
            inner = (props["request"].get("properties") or {})
            params = "request{" + ",".join(list(inner)[:12]) + ("…" if len(inner) > 12 else "") + "}"
        else:
            params = ",".join(list(props)[:12])
        print(f"  {t.name:<40} {params}")
    if args.tool:
        arguments = json.loads(args.args or "{}")
        text = client.call_tool(args.tool, arguments)
        path = out / f"{args.tool}.json"
        path.write_text(text, encoding="utf-8")
        log.info(f"{args.tool} 的原始响应已保存到 {path}（{len(text)} 字节）")
    return 0


def cmd_search_category(args, cfg, secrets) -> int:
    client = _live_client(secrets)
    schema = next((t.input_schema for t in client.list_tools() if t.name == "product_node"), {})
    props = schema.get("properties") or {}
    nested = "request" in props
    inner = (props["request"].get("properties") or {}) if nested else props
    key = next((k for k in inner if "keyword" in k.lower()), None) or \
        next((k for k in inner if k.lower() in ("q", "query", "name", "label")), "keyword")
    body = {"marketplace": cfg["marketplace"], key: args.keyword}
    text = client.call_tool("product_node", {"request": body} if nested else body)
    data = json.loads(text)
    rows = data.get("data") if isinstance(data, dict) else data
    rows = rows.get("items", []) if isinstance(rows, dict) else (rows or [])
    if not rows:
        log.info("没有找到匹配的类目")
        return 0
    for row in rows[:30]:
        print(f"{row.get('nodeIdPath', ''):<45} {row.get('products', ''):>8}  "
              f"{row.get('nodeLabelPathLocale') or ''}  |  {row.get('nodeLabelPath', '')}")
    log.info("把需要的 nodeIdPath 填到 config.yaml 的 scope.extra_nodes 或 scope.roots 里")
    return 0


def cmd_test_dingtalk(args, cfg, secrets) -> int:
    from .notify import dingtalk

    if not secrets.dingtalk_webhooks:
        raise SystemExit("缺少 DINGTALK_WEBHOOK（写在 .env 里）")
    url = args.url or report_base_url(cfg, secrets) or "https://github.com"
    if args.with_key and secrets.report_key:
        url = url.rstrip("#") + "#k=test-fragment-ok"
    text = (f"### {cfg['report']['title']} · 测试消息\n"
            f"如果你看到这条消息，说明钉钉机器人配置成功。\n\n点击下方按钮应能打开：{url.split('#')[0]}")
    payload = dingtalk.action_card(f"{cfg['report']['title']} 测试", text, url,
                                   open_in_browser=bool(cfg["notify"]["open_in_browser"]))
    results = dingtalk.send(secrets.dingtalk_webhooks, secrets.dingtalk_secrets, payload)
    log.info(f"发送结果：成功 {sum(results)}/{len(results)}")
    return 0 if all(results) else 1


def cmd_status(args, cfg, secrets) -> int:
    master = crypto.parse_master_key(secrets.report_key)
    state = store.load(args.site_dir, master)
    disc = state.get("discovery") or {}
    print(f"期数：{state.get('run_seq')}　上次成功：{state.get('last_success')}")
    print(f"发现月份：{disc.get('period')}（{'完整' if disc.get('complete') else '未完成'}），"
          f"子类目 {len(disc.get('nodes') or [])} 个，基线商品 {len(disc.get('products') or {})} 个")
    print(f"追踪 ASIN：{len(state.get('asins') or {})} 个　报告：{len(state.get('reports') or [])} 期")
    print(f"每月调用：{state.get('calls')}")
    for run in (state.get("runs") or [])[-5:]:
        print(f"  {run['id']}  {run.get('counts')}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m radar", description="家具爆品雷达")
    parser.add_argument("--config", help="配置文件路径（默认项目根目录 config.yaml）")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("run", help="运行一期：拉数据、分析、生成加密报告")
    p.add_argument("--site-dir", default=str(PROJECT_ROOT / "site"))
    p.add_argument("--force", action="store_true", help="忽略“每 3 天一次”的间隔")
    p.add_argument("--budget", type=int, help="覆盖本期调用预算")
    p.add_argument("--notify", action="store_true", help="完成后立即推送钉钉")
    p.add_argument("--github-output", action="store_true", help=argparse.SUPPRESS)
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("notify", help="把最近一期报告推送到钉钉")
    p.add_argument("--site-dir", default=str(PROJECT_ROOT / "site"))
    p.set_defaults(func=cmd_notify)

    p = sub.add_parser("alert", help="发送运行失败告警（不含数据）")
    p.add_argument("--stage", default="运行")
    p.add_argument("--run-url", default="")
    p.set_defaults(func=cmd_alert)

    p = sub.add_parser("demo", help="用模拟数据生成演示报告（不花积分）")
    p.add_argument("--out", default=str(PROJECT_ROOT / "out" / "demo"))
    p.add_argument("--runs", type=int, default=2)
    p.add_argument("--llm", action="store_true", help="演示时也调用大模型写总结")
    p.set_defaults(func=cmd_demo)

    p = sub.add_parser("probe", help="列出卖家精灵 MCP 工具；可选调用一次并保存原始响应")
    p.add_argument("--tool")
    p.add_argument("--args", help='JSON 参数，例如 \'{"marketplace":"US","asin":"B0..."}\'')
    p.add_argument("--out", default=str(PROJECT_ROOT / "out" / "probe"))
    p.set_defaults(func=cmd_probe)

    p = sub.add_parser("search-category", help="按关键词查类目 nodeIdPath")
    p.add_argument("keyword")
    p.set_defaults(func=cmd_search_category)

    p = sub.add_parser("test-dingtalk", help="发送一条测试卡片消息")
    p.add_argument("--url")
    p.add_argument("--with-key", action="store_true", help="链接带上 #k= 片段，检查钉钉是否保留")
    p.set_defaults(func=cmd_test_dingtalk)

    p = sub.add_parser("decrypt", help="本地解密一份报告或 data/state.enc")
    p.add_argument("file")
    p.add_argument("-o", "--output")
    p.set_defaults(func=cmd_decrypt)

    p = sub.add_parser("keygen", help="生成新的 REPORT_KEY")
    p.set_defaults(func=cmd_keygen)

    p = sub.add_parser("status", help="查看历史数据概况")
    p.add_argument("--site-dir", default=str(PROJECT_ROOT / "site"))
    p.set_defaults(func=cmd_status)
    return parser


def main(argv: list[str] | None = None) -> int:
    _utf8_stdout()
    args = build_parser().parse_args(argv)
    cfg = load_config(args.config)
    secrets = load_secrets(cfg)
    try:
        return args.func(args, cfg, secrets)
    except crypto.MasterKeyError as exc:
        log.warn(str(exc))
        return 2
