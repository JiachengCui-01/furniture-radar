"""加密报告外壳与首页。

外壳页面里只有密文；浏览器用 WebCrypto 解密：
  1. 优先读取链接 #k=<报告密钥>（钉钉消息里的链接自带）；
  2. 也可以手动粘贴“报告密钥”或“主密钥 REPORT_KEY”（主密钥会在浏览器里用 HKDF 派生出该报告的密钥）。
解密成功后把密钥从地址栏去掉（存进 sessionStorage），再用解密出的 HTML 替换整页。
"""
from __future__ import annotations

import html
import json

DECRYPT_JS = r"""
const enc = new TextEncoder();
function b64u(s){s=s.replace(/-/g,'+').replace(/_/g,'/');while(s.length%4)s+='=';const b=atob(s);
  const o=new Uint8Array(b.length);for(let i=0;i<b.length;i++)o[i]=b.charCodeAt(i);return o;}
async function decryptWith(raw,p){
  const key=await crypto.subtle.importKey('raw',raw,{name:'AES-GCM'},false,['decrypt']);
  const pt=await crypto.subtle.decrypt({name:'AES-GCM',iv:b64u(p.iv),additionalData:enc.encode('v1|'+p.id)},key,b64u(p.ct));
  return new TextDecoder().decode(pt);}
async function deriveReportKey(master,id){
  const base=await crypto.subtle.importKey('raw',master,'HKDF',false,['deriveBits']);
  const bits=await crypto.subtle.deriveBits({name:'HKDF',hash:'SHA-256',salt:enc.encode(id),info:enc.encode('report-v1')},base,256);
  return new Uint8Array(bits);}
async function openReport(keyText,p){
  const raw=b64u(keyText.trim());
  try{return {html:await decryptWith(raw,p),key:keyText.trim()};}catch(e){}
  const derived=await deriveReportKey(raw,p.id);
  const html=await decryptWith(derived,p);
  let s='';derived.forEach(c=>s+=String.fromCharCode(c));
  return {html,key:btoa(s).replace(/\+/g,'-').replace(/\//g,'_').replace(/=+$/,'')};}
"""

BOOT_JS = r"""
(async function(){
  const p=JSON.parse(document.getElementById('payload').textContent);
  const msg=document.getElementById('msg'), form=document.getElementById('form');
  function show(h){
    const doc=new DOMParser().parseFromString(h,'text/html');
    document.replaceChild(document.importNode(doc.documentElement,true),document.documentElement);
    // DOMParser 解析出来的脚本不会执行，重新创建一遍报告里的脚本（小窗等交互）
    Array.prototype.forEach.call(document.querySelectorAll('script'),function(old){
      var s=document.createElement('script');s.text=old.text;old.parentNode.replaceChild(s,old);});}
  async function attempt(k){
    const r=await openReport(k,p);
    try{sessionStorage.setItem('radar:'+p.id,r.key);}catch(e){}
    if(location.hash)history.replaceState(null,'',location.pathname+location.search);
    show(r.html);}
  if(!(window.crypto&&crypto.subtle)){msg.textContent='当前浏览器不支持解密，请用新版 Chrome / Safari / Edge 打开此链接。';return;}
  const fromHash=new URLSearchParams(location.hash.slice(1)).get('k');
  let saved=null;try{saved=sessionStorage.getItem('radar:'+p.id);}catch(e){}
  const k=fromHash||saved;
  if(k){try{await attempt(k);return;}catch(e){msg.textContent='链接中的密钥无法解密这份报告，请检查链接是否完整。';}}
  else{msg.textContent='这份报告已加密。请通过钉钉消息里的链接打开，或在下方粘贴密钥。';}
  form.style.display='block';
  form.addEventListener('submit',async ev=>{ev.preventDefault();
    try{await attempt(document.getElementById('key').value);}catch(e){msg.textContent='密钥不正确。';}});
})();
"""

SHELL_CSS = """body{margin:0;font:15px/1.6 -apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif;
background:#f5f6f8;color:#1c2230}.box{max-width:520px;margin:12vh auto;padding:24px 16px;text-align:center}
h1{font-size:20px}#form{display:none;margin-top:14px}input{width:100%;padding:10px;border:1px solid #d0d5dd;
border-radius:8px;font-size:14px;box-sizing:border-box}button{margin-top:10px;padding:9px 18px;border:0;border-radius:8px;
background:#1c6dd0;color:#fff;font-size:14px}.sub{color:#667085;font-size:13px}
@media (prefers-color-scheme:dark){body{background:#0f1218;color:#e6e8ec}input{background:#181c24;color:#e6e8ec;border-color:#2a303b}}"""


def encrypted_page(title: str, envelope: dict, report_id: str) -> str:
    payload = json.dumps({**envelope, "id": report_id}, separators=(",", ":"))
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow">
<meta name="referrer" content="no-referrer"><title>{html.escape(title)}</title><style>{SHELL_CSS}</style></head>
<body><div class="box"><h1>{html.escape(title)}</h1><p id="msg" class="sub">正在解密报告…</p>
<form id="form"><input id="key" type="password" autocomplete="off" placeholder="粘贴报告密钥或主密钥">
<button type="submit">打开报告</button></form></div>
<script id="payload" type="application/json">{payload}</script>
<script>{DECRYPT_JS}{BOOT_JS}</script></body></html>"""


def index_page(title: str, report_ids: list[str]) -> str:
    """首页只列日期，不含任何商品信息；点进去仍需密钥。"""
    links = "".join(f'<li><a href="reports/{html.escape(r)}.html">{html.escape(r)}</a></li>'
                    for r in sorted(report_ids, reverse=True))
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow">
<title>{html.escape(title)}</title><style>{SHELL_CSS} ul{{text-align:left;display:inline-block}}</style></head>
<body><div class="box"><h1>{html.escape(title)}</h1>
<p class="sub">报告已加密。请通过钉钉消息中的链接打开；也可以点开任意一期后粘贴主密钥查看。</p>
<ul>{links or '<li>暂无报告</li>'}</ul></div></body></html>"""
