"""加密报告外壳与首页。

外壳页面里只有密文；浏览器用 WebCrypto 解密：
  1. 优先读取链接 #k=<报告密钥>（钉钉消息里的链接自带）；
  2. 也可以手动粘贴“报告密钥”或“主密钥 REPORT_KEY”（主密钥会在浏览器里用 HKDF 派生出该报告的密钥）。
解密成功后把密钥从地址栏去掉（存进 sessionStorage），再用解密出的 HTML 替换整页。
报告 HTML 先压缩（zlib，"z": "deflate"）再加密：1.5 MB 的报告压缩后约 0.2 MB，手机钉钉里打开快、不容易内存不够。
浏览器用自带的 DecompressionStream 解压；没有的（iOS 16.4 以前等）用页面里的小解压程序（inflate，按 RFC 1950/1951）。
以前没压缩的报告（没有 "z"）照样能打开。
外壳还记着最新一期的 ID（每期运行时改写往期外壳，不需要解密）：打开的是往期报告时，顶部加一条“回到最新报告”。
"""
from __future__ import annotations

import html
import json
import re
import zlib

from .. import crypto

DECRYPT_JS = r"""
const enc = new TextEncoder();
function b64u(s){s=s.replace(/-/g,'+').replace(/_/g,'/');while(s.length%4)s+='=';const b=atob(s);
  const o=new Uint8Array(b.length);for(let i=0;i<b.length;i++)o[i]=b.charCodeAt(i);return o;}
function RenderError(m){const e=new Error(m);e.render=true;return e;}
// zlib 解压（RFC 1950/1951），只在浏览器没有 DecompressionStream 时用
function inflateZlib(src){
  let pos=2,bitbuf=0,bitcnt=0,out=new Uint8Array(src.length*6+1024),n=0;
  function need(k){if(n+k>out.length){const b=new Uint8Array(Math.max(out.length*2,n+k));b.set(out.subarray(0,n));out=b;}}
  function bits(k){while(bitcnt<k){if(pos>=src.length)throw RenderError('inflate: eof');bitbuf|=src[pos++]<<bitcnt;bitcnt+=8;}
    const v=bitbuf&((1<<k)-1);bitbuf>>>=k;bitcnt-=k;return v;}
  function build(lens){const count=new Uint16Array(16),offs=new Uint16Array(16),sym=new Uint16Array(lens.length);
    for(let i=0;i<lens.length;i++)count[lens[i]]++;count[0]=0;
    for(let i=1;i<16;i++)offs[i]=offs[i-1]+count[i-1];
    for(let i=0;i<lens.length;i++)if(lens[i])sym[offs[lens[i]]++]=i;return {count,sym};}
  function decode(h){let code=0,first=0,index=0;
    for(let len=1;len<16;len++){code|=bits(1);const c=h.count[len];if(code-c<first)return h.sym[index+(code-first)];
      index+=c;first+=c;first<<=1;code<<=1;}throw RenderError('inflate: bad code');}
  const LB=[3,4,5,6,7,8,9,10,11,13,15,17,19,23,27,31,35,43,51,59,67,83,99,115,131,163,195,227,258],
    LE=[0,0,0,0,0,0,0,0,1,1,1,1,2,2,2,2,3,3,3,3,4,4,4,4,5,5,5,5,0],
    DB=[1,2,3,4,5,7,9,13,17,25,33,49,65,97,129,193,257,385,513,769,1025,1537,2049,3073,4097,6145,8193,12289,16385,24577],
    DE=[0,0,0,0,1,1,2,2,3,3,4,4,5,5,6,6,7,7,8,8,9,9,10,10,11,11,12,12,13,13],
    ORD=[16,17,18,0,8,7,9,6,10,5,11,4,12,3,13,2,14,1,15];
  function codes(lc,dc){for(;;){let s=decode(lc);
    if(s<256){need(1);out[n++]=s;}else if(s===256)return;
    else{s-=257;if(s>=29)throw RenderError('inflate: bad length');const len=LB[s]+bits(LE[s]);
      const ds=decode(dc);if(ds>=30)throw RenderError('inflate: bad distance');const dist=DB[ds]+bits(DE[ds]);
      if(dist>n)throw RenderError('inflate: distance too far');need(len);for(let i=0;i<len;i++){out[n]=out[n-dist];n++;}}}}
  let last;
  do{last=bits(1);const type=bits(2);
    if(type===0){bitbuf=0;bitcnt=0;if(pos+4>src.length)throw RenderError('inflate: eof');
      const len=src[pos]|(src[pos+1]<<8);pos+=4;if(pos+len>src.length)throw RenderError('inflate: eof');
      need(len);out.set(src.subarray(pos,pos+len),n);n+=len;pos+=len;}
    else if(type===1){const l=new Uint8Array(288);l.fill(8,0,144);l.fill(9,144,256);l.fill(7,256,280);l.fill(8,280,288);
      codes(build(l),build(new Uint8Array(30).fill(5)));}
    else if(type===2){const nlen=bits(5)+257,ndist=bits(5)+1,ncode=bits(4)+4,cl=new Uint8Array(19);
      for(let i=0;i<ncode;i++)cl[ORD[i]]=bits(3);const ch=build(cl),lens=new Uint8Array(nlen+ndist);
      for(let i=0;i<nlen+ndist;){const s=decode(ch);
        if(s<16)lens[i++]=s;
        else{let rep,val=0;if(s===16){if(!i)throw RenderError('inflate: bad repeat');val=lens[i-1];rep=3+bits(2);}
          else if(s===17)rep=3+bits(3);else rep=11+bits(7);
          if(i+rep>nlen+ndist)throw RenderError('inflate: bad lengths');while(rep--)lens[i++]=val;}}
      codes(build(lens.subarray(0,nlen)),build(lens.subarray(nlen)));}
    else throw RenderError('inflate: bad block');
  }while(!last);
  return out.subarray(0,n);}
async function unzip(bytes){
  if(typeof DecompressionStream==='function'){
    try{const s=new Blob([bytes]).stream().pipeThrough(new DecompressionStream('deflate'));
      return new Uint8Array(await new Response(s).arrayBuffer());}catch(e){}}
  return inflateZlib(bytes);}
async function decryptWith(raw,p){
  const key=await crypto.subtle.importKey('raw',raw,{name:'AES-GCM'},false,['decrypt']);
  const pt=new Uint8Array(await crypto.subtle.decrypt({name:'AES-GCM',iv:b64u(p.iv),additionalData:enc.encode('v1|'+p.id)},key,b64u(p.ct)));
  if(p.z!=='deflate')return new TextDecoder().decode(pt);
  try{return new TextDecoder().decode(await unzip(pt));}catch(e){throw e.render?e:RenderError(String(e));}}
async function deriveReportKey(master,id){
  const base=await crypto.subtle.importKey('raw',master,'HKDF',false,['deriveBits']);
  const bits=await crypto.subtle.deriveBits({name:'HKDF',hash:'SHA-256',salt:enc.encode(id),info:enc.encode('report-v1')},base,256);
  return new Uint8Array(bits);}
async function openReport(keyText,p){
  const raw=b64u(keyText.trim());
  try{return {html:await decryptWith(raw,p),key:keyText.trim()};}catch(e){if(e.render)throw e;}
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
    // adoptNode 直接搬过来，不再复制一份（报告大时手机内存吃紧）
    document.replaceChild(document.adoptNode(doc.documentElement),document.documentElement);
    // DOMParser 解析出来的脚本不会执行，重新创建一遍报告里的脚本（小窗等交互）
    Array.prototype.forEach.call(document.querySelectorAll('script'),function(old){
      var s=document.createElement('script');s.text=old.text;old.parentNode.replaceChild(s,old);});}
  function latestBar(){
    if(!p.latest||p.latest===p.id)return;
    const bar=document.createElement('div'), a=document.createElement('a');
    bar.style.cssText='margin:0 0 10px;padding:8px 12px;border:1px solid var(--line,#e4e7ec);'+
      'border-left:4px solid var(--accent,#1c6dd0);border-radius:10px;background:var(--card,#fff);'+
      'color:var(--muted,#667085);font-size:13px';
    a.href=encodeURIComponent(p.latest)+'.html';a.textContent='回到最新报告（'+p.latest+'）';a.style.fontWeight='600';
    bar.append('这是往期报告 · ',a);
    const wrap=document.querySelector('.wrap')||document.body;wrap.insertBefore(bar,wrap.firstChild);}
  async function attempt(k){
    const r=await openReport(k,p);
    try{sessionStorage.setItem('radar:'+p.id,r.key);}catch(e){}
    if(location.hash)history.replaceState(null,'',location.pathname+location.search);
    try{show(r.html);latestBar();}catch(e){throw RenderError(String(e));}}
  // 密钥不对和显示出错分开说：显示出错时换密钥没用，要换浏览器 / 升级系统
  const RENDER_MSG='报告已解开，但这个浏览器没能显示出来（可能版本太旧）。请用新版 Chrome / Safari / Edge 打开此链接。';
  if(!(window.crypto&&crypto.subtle)){msg.textContent='当前浏览器不支持解密，请用新版 Chrome / Safari / Edge 打开此链接。';return;}
  const fromHash=new URLSearchParams(location.hash.slice(1)).get('k');
  let saved=null;try{saved=sessionStorage.getItem('radar:'+p.id);}catch(e){}
  const k=fromHash||saved;
  if(k){try{await attempt(k);return;}catch(e){
    if(e&&e.render){msg.textContent=RENDER_MSG;return;}
    msg.textContent='链接中的密钥无法解密这份报告，请检查链接是否完整。';}}
  else{msg.textContent='这份报告已加密。请通过钉钉消息里的链接打开，或在下方粘贴密钥。';}
  form.style.display='block';
  form.addEventListener('submit',async ev=>{ev.preventDefault();
    try{await attempt(document.getElementById('key').value);}catch(e){msg.textContent=e&&e.render?RENDER_MSG:'密钥不正确。';}});
})();
"""

SHELL_CSS = """body{margin:0;font:15px/1.6 -apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif;
background:#f5f6f8;color:#1c2230}.box{max-width:520px;margin:12vh auto;padding:24px 16px;text-align:center}
h1{font-size:20px}#form{display:none;margin-top:14px}input{width:100%;padding:10px;border:1px solid #d0d5dd;
border-radius:8px;font-size:14px;box-sizing:border-box}button{margin-top:10px;padding:9px 18px;border:0;border-radius:8px;
background:#1c6dd0;color:#fff;font-size:14px}.sub{color:#667085;font-size:13px}
@media (prefers-color-scheme:dark){body{background:#0f1218;color:#e6e8ec}input{background:#181c24;color:#e6e8ec;border-color:#2a303b}}"""


PAYLOAD_RE = re.compile(r'<script id="payload" type="application/json">(.*?)</script>', re.S)


def encrypted_page(title: str, envelope: dict, report_id: str, latest_id: str) -> str:
    payload = json.dumps({**envelope, "id": report_id, "latest": latest_id}, separators=(",", ":"))
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow">
<meta name="referrer" content="no-referrer"><title>{html.escape(title)}</title><style>{SHELL_CSS}</style></head>
<body><div class="box"><h1>{html.escape(title)}</h1><p id="msg" class="sub">正在解密报告…</p>
<form id="form"><input id="key" type="password" autocomplete="off" placeholder="粘贴报告密钥或主密钥">
<button type="submit">打开报告</button></form></div>
<script id="payload" type="application/json">{payload}</script>
<script>{DECRYPT_JS}{BOOT_JS}</script></body></html>"""


def seal(master: bytes, report_id: str, page: str) -> dict:
    """报告 HTML → 压缩 → 用这份报告的密钥加密，返回信封（v / iv / ct / z）。"""
    key, aad = crypto.report_key(master, report_id), crypto.report_aad(report_id)
    return {**crypto.encrypt(key, zlib.compress(page.encode("utf-8"), 9), aad), "z": "deflate"}


def unseal(master: bytes, payload: dict) -> str:
    """外壳里的信封 → 报告 HTML（压缩过的解压；以前没压缩的直接读）。"""
    key, aad = crypto.report_key(master, payload["id"]), crypto.report_aad(payload["id"])
    raw = crypto.decrypt(key, envelope_of(payload), aad)
    return (zlib.decompress(raw) if payload.get("z") == "deflate" else raw).decode("utf-8")


def read_payload(page: str) -> dict:
    """从外壳页面取出密文信封（含 id / latest），不需要密钥。"""
    m = PAYLOAD_RE.search(page)
    if not m:
        raise ValueError("不是加密报告页面")
    return json.loads(m.group(1))


def envelope_of(payload: dict) -> dict:
    return {k: v for k, v in payload.items() if k not in ("id", "latest")}


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
