#!/usr/bin/env python3
"""
夸克网盘资源搜索器 v7 - 最终稳定版
搜索源: pansearch.me(主力) + 夸克搜索站 + 已知资源站直搜
"""
import asyncio, re, hashlib, time, os, threading
from datetime import datetime, timedelta
from urllib.parse import quote
from dataclasses import dataclass, field
from collections import OrderedDict, Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

from curl_cffi import requests as cffi_requests
from bs4 import BeautifulSoup
from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
import uvicorn, secrets

app = FastAPI(title="夸克网盘资源搜索器")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

ACCESS_PASSWORD = os.environ.get("ACCESS_PASSWORD", "").strip()
VALID_TOKENS = set()

class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if not ACCESS_PASSWORD: return await call_next(request)
        path = request.url.path
        if path in ("/api/health", "/api/login"): return await call_next(request)
        token = request.cookies.get("quark_token") or request.query_params.get("token")
        if token and token in VALID_TOKENS: return await call_next(request)
        if path.startswith("/api/"): return JSONResponse({"error": "unauthorized"}, 401)
        return HTMLResponse(LOGIN_HTML, 401)

app.add_middleware(AuthMiddleware)

QUARK_LINK_RE = re.compile(r'(https?://pan\.quark\.cn/s/[a-zA-Z0-9]+)')
HDRS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.5',
}

# 缓存
_cache, _cl = {}, threading.Lock()
def cget(k):
    with _cl:
        e = _cache.get(k)
        if e and datetime.now() < e['e']: return e['d']
def cset(k, d, t=5):
    with _cl:
        _cache[k] = {'d': d, 'e': datetime.now() + timedelta(minutes=t)}
        if len(_cache) > 50:
            old = min(_cache, key=lambda k: _cache[k]['e']); del _cache[old]

# 元数据
Q = [(r'\b(4K|2160[Pp]|UHD)\b','4K'),(r'\b(1080[Pp]|FHD)\b','1080P'),(r'\b(720[Pp])\b','720P'),
     (r'\b(蓝光|BluRay|BD|REMUX)\b','蓝光'),(r'\b(HDR|杜比视界|DV)\b','HDR')]
F = [(r'\.mkv\b','MKV'),(r'\.mp4\b','MP4'),(r'\.avi\b','AVI'),(r'\.iso\b','ISO'),(r'\.(rar|zip|7z)\b','压缩包')]
L = [(r'(粤语|广东话|Cantonese)','粤语'),(r'(英语|English)','英语'),(r'(日语|Japanese)','日语'),
     (r'(韩语|Korean)','韩语'),(r'(国语|普通话|中文配音|简中|繁中)','普通话')]
S = re.compile(r'(\d+\.?\d*)\s*(GB|TB|MB|G|T|M)\b', re.I)
DT = re.compile(r'(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})')
TG = [(r'(中字|内嵌|中文字幕)','中字'),(r'(杜比|Dolby|全景声)','杜比音效'),(r'(合集|全季)','合集'),
      (r'(完结|全\d+集)','已完结'),(r'(高码|BDRip|WEB-DL)','高清压制')]

def meta(text):
    return {
        "quality": next((l for p,l in Q if re.search(p,text,re.I)),""),
        "fmt": next((l for p,l in F if re.search(p,text,re.I)),""),
        "lang": next((l for p,l in L if re.search(p,text,re.I)),""),
        "size": (lambda m: f"{m.group(1)}{m.group(2).upper()}" if m else "")(S.search(text)),
        "date": (lambda m: f"{m.group(1)}-{m.group(2).zfill(2)}-{m.group(3).zfill(2)}" if m and 2020<=int(m.group(1))<=2027 else "")(DT.search(text)),
        "tags": list({l for p,l in TG if re.search(p,text,re.I)}),
        "film": any(re.search(k,text,re.I) for k in ['电影','电视剧','动漫','动画','纪录片','4K','1080P','蓝光','字幕'])
    }

@dataclass
class QR:
    title:str; link:str; quality:str=""; fmt:str=""; size:str=""; language:str=""
    publish_date:str=""; description:str=""; is_film:bool=False; score:int=50
    extra_tags:list=field(default_factory=list); source:str=""; source_url:str=""
    ref_count:int=1; valid:bool=True
    def td(self):
        return {"title":self.title,"link":self.link,"quality":self.quality,"format":self.fmt,
                "size":self.size,"language":self.language,"publish_date":self.publish_date,
                "description":self.description,"is_film":self.is_film,"score":self.score,
                "ref_count":self.ref_count,"valid":self.valid,"tags":self.extra_tags,
                "source":self.source,"source_url":self.source_url}

QB={k:v for k,v in [('4K',25),('蓝光',22),('HDR',20),('1080P',15),('720P',8)]}
FB={k:v for k,v in [('MKV',10),('MP4',8),('ISO',12)]}
TB={k:v for k,v in [('中字',8),('杜比音效',10),('合集',5),('已完结',5),('高清压制',8)]}

def sscore(r):
    s=50+QB.get(r.quality,0)+FB.get(r.fmt,0)+sum(TB.get(t,2) for t in r.extra_tags)
    if r.size: s+=3
    if r.language: s+=5
    if r.publish_date: s+=3
    if r.ref_count>1: s+=min(r.ref_count*3,15)
    return min(max(s,0),100)

# ════════════════ 核心: 提取夸克链接 ════════════════
def extract_links(url, query="", timeout=8):
    """从指定 URL 提取所有夸克链接"""
    rv = []
    try:
        resp = cffi_requests.get(url, headers=HDRS, impersonate='chrome120', timeout=timeout)
        if resp.status_code != 200 or len(resp.text) < 300: return rv
        soup = BeautifulSoup(resp.text, 'html.parser')
        page_title = soup.title.get_text(strip=True) if soup.title else ""

        # 从整个页面提取 Quark 链接的上下文
        for m in QUARK_LINK_RE.finditer(resp.text):
            link = m.group(1)
            start = max(0, m.start() - 100)
            end = min(len(resp.text), m.end() + 100)
            context = resp.text[start:end].replace('\n', ' ').strip()[:200]

            # 在 BeautifulSoup 中找上下文
            parent = None
            for a in soup.find_all('a', href=re.compile(re.escape(link[:30]))):
                p = a.find_parent(['div','li','td','p','article','section','h3','h4'])
                if p:
                    parent = p.get_text(strip=True)[:300]
                    break

            full_context = f"{page_title}\n{parent or context}"
            title = ""
            if parent:
                # 从 parent 中提取更好的标题
                for h_tag in ['h3', 'h2', 'h1', 'strong', 'b']:
                    h = soup.find(h_tag)
                    if not h: continue
                    htxt = h.get_text(strip=True)
                    for a in h.find_all('a', href=re.compile(re.escape(link[:30]))):
                        title = htxt; break
                    if title: break
            if not title:
                title = page_title or query

            rv.append({
                'link': link, 'context': full_context, 'title': title,
                'source': url.split('/')[2] if '//' in url else url,
                'source_url': url,
            })
        return rv
    except: return rv

# ════════════════ 搜索源 1: pansearch.me (主力) ════════════════
def search_pansearch(query):
    """pansearch.me 夸克搜索站"""
    rr, seen = [], set()
    url = f'https://www.pansearch.me/search?keyword={quote(query)}'
    page_results = extract_links(url, query, 12)
    for pr in page_results:
        cl = pr['link'].split('#')[0].rstrip('/')
        if cl not in seen: seen.add(cl); pr['link'] = cl; rr.append(pr)
    return rr

# ════════════════ 搜索源 2: 直接搜索已知资源站 ════════════════
def search_resource_sites(query):
    """直接搜索已知资源站首页/分类页"""
    rr, seen = [], set()
    enc = quote(query)

    # 这些站点有搜索功能，且页面是静态 HTML
    sites = [
        f'https://www.yunpanziyuan.xyz/search.php?mod=forum&searchsubmit=yes&kw={enc}',
        f'https://yunpans.com/search.php?mod=forum&searchsubmit=yes&kw={enc}',
        f'https://kkpans.com/search?keyword={enc}',
        f'https://www.aipanso.com/search?k={enc}',
        f'https://xiaokupan.com/search?keyword={enc}',
    ]

    with ThreadPoolExecutor(max_workers=5) as ex:
        futs = {}
        for url in sites:
            futs[ex.submit(extract_links, url, query, 8)] = url

        for f in as_completed(futs, timeout=12):
            try:
                for pr in f.result(timeout=10):
                    cl = pr['link'].split('#')[0].rstrip('/')
                    if cl not in seen: seen.add(cl); pr['link'] = cl; rr.append(pr)
            except: continue

    return rr

# ════════════════ 搜索源 3: 已知资源站内部搜索 ════════════════
def search_known_pages(query):
    """从已知资源站获取搜索页结果，然后抓取详情页"""
    rr, seen = [], set()
    enc = quote(query)

    # 直接搜索资源站的搜索结果列表，提取帖子链接，再抓帖子详情
    forum_searches = [
        (f'https://www.yunpanziyuan.xyz/search.php?mod=forum&searchsubmit=yes&kw={enc}', 'yunpanziyuan.xyz'),
        (f'https://www.yunpans.com/search.php?mod=forum&searchsubmit=yes&kw={enc}', 'yunpans.com'),
    ]

    for search_url, site_name in forum_searches:
        try:
            resp = cffi_requests.get(search_url, headers=HDRS, impersonate='chrome120', timeout=8)
            if resp.status_code != 200 or len(resp.text) < 500: continue

            soup = BeautifulSoup(resp.text, 'html.parser')
            thread_urls = []
            base_url = '/'.join(search_url.split('/')[:3])

            for a in soup.find_all('a', href=True):
                h = a['href']
                if any(p in h for p in ['thread-', 'viewthread', '/d/', '/t/', 'forum.php']):
                    full_url = h if h.startswith('http') else base_url + h
                    if full_url not in thread_urls:
                        thread_urls.append(full_url)

            # 抓取每个帖子详情页
            with ThreadPoolExecutor(max_workers=4) as ex:
                futs = {ex.submit(extract_links, tu, query, 6): tu for tu in thread_urls[:5]}
                for f in as_completed(futs, timeout=10):
                    try:
                        for pr in f.result(timeout=8):
                            cl = pr['link'].split('#')[0].rstrip('/')
                            if cl not in seen: seen.add(cl); pr['link'] = cl; rr.append(pr)
                    except: continue
        except: continue

    return rr

# ════════════════ 聚合 ════════════════
def search_all(query):
    all_r, seen = [], set()
    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = {
            ex.submit(search_pansearch, query): 'pansearch',
            ex.submit(search_resource_sites, query): 'direct',
            ex.submit(search_known_pages, query): 'known',
        }
        for f in as_completed(futs, timeout=25):
            try:
                for r in f.result(timeout=20):
                    cl = r['link'].rstrip('/')
                    if cl not in seen: seen.add(cl); all_r.append(r)
            except: pass
    return all_r

def process(raw, query):
    seen = OrderedDict(); ref = Counter()
    for item in raw:
        lk = item['link']
        if 'pan.quark.cn/s/' not in lk: continue
        lk = lk.split('?')[0].rstrip('/'); key = hashlib.md5(lk.encode()).hexdigest(); ref[key] += 1
        title = item.get('title',''); ctx = item.get('context','')
        src = item.get('source',''); surl = item.get('source_url','')
        full = f"{title} {query}\n{ctx}"; m = meta(full)
        desc = full[:200]
        qr = QR(title=title or full[:100] or query, link=lk, source=src, source_url=surl,
                quality=m['quality'], fmt=m['fmt'], size=m['size'], language=m['lang'],
                publish_date=m['date'], description=desc[:200], is_film=m['film'], extra_tags=m['tags'])
        if key in seen:
            ex = seen[key]
            for a in ['quality','fmt','size','language','publish_date','description']:
                if len(str(getattr(qr,a))) > len(str(getattr(ex,a))): setattr(ex, a, getattr(qr,a))
            for t in qr.extra_tags:
                if t not in ex.extra_tags: ex.extra_tags.append(t)
        else: seen[key] = qr
    for k, c in ref.items():
        if k in seen: seen[k].ref_count = c
    for r in seen.values(): r.score = sscore(r)
    return sorted(seen.values(), key=lambda x: x.score, reverse=True)

# ════════════════ API ════════════════
LOGIN_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no, viewport-fit=cover">
<title>夸克搜 - 登录</title>
<style>
  :root{--bg:#08080f;--card:#141428;--input:#1a1a30;--border:#28284a;--text:#d0d0e8;--text2:#7070a0;--acc:#6c5ce7;--acc2:#a29bfe;--rad:14px}
  *{margin:0;padding:0;box-sizing:border-box}
  body{font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif;background:var(--bg);color:var(--text);min-height:100vh;display:flex;align-items:center;justify-content:center;padding:20px}
  .box{background:var(--card);border:1px solid var(--border);border-radius:var(--rad);padding:32px 24px;width:100%;max-width:360px;text-align:center}
  .logo{font-size:28px;font-weight:800;background:linear-gradient(135deg,var(--acc),var(--acc2));-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;margin-bottom:8px}
  .sub{color:var(--text2);font-size:13px;margin-bottom:24px}
  input{width:100%;background:var(--input);border:2px solid var(--border);border-radius:var(--rad);padding:12px 16px;color:var(--text);font-size:16px;outline:none;text-align:center;margin-bottom:16px}
  input:focus{border-color:var(--acc);box-shadow:0 0 0 3px rgba(108,92,231,.2)}
  button{width:100%;background:linear-gradient(135deg,var(--acc),#5b4bd5);color:#fff;border:none;border-radius:var(--rad);padding:12px;font-size:15px;font-weight:600;cursor:pointer}
  button:active{transform:scale(.97)}
  .err{color:#f66;font-size:12px;margin-top:12px;display:none}
</style>
</head>
<body>
<div class="box">
  <div class="logo">夸克搜</div>
  <div class="sub">请输入访问密码</div>
  <input type="password" id="pwd" placeholder="访问密码" autofocus>
  <button onclick="login()">验证</button>
  <div class="err" id="err">密码错误</div>
</div>
<script>
async function login(){
  const p=document.getElementById('pwd').value;
  if(!p)return;
  const r=await fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({password:p})});
  if(r.ok){window.location.href='/'}else{document.getElementById('err').style.display='block'}
}
document.getElementById('pwd').addEventListener('keydown',e=>{if(e.key==='Enter')login()});
</script>
</body>
</html>"""

@app.post("/api/login")
async def login(request:Request):
    try: body=await request.json(); pwd=body.get("password","")
    except: return JSONResponse({"error":"invalid"},400)
    if not ACCESS_PASSWORD or pwd!=ACCESS_PASSWORD: return JSONResponse({"error":"密码错误"},403)
    token=secrets.token_urlsafe(32); VALID_TOKENS.add(token)
    resp=JSONResponse({"ok":True})
    resp.set_cookie("quark_token",token,httponly=True,max_age=86400*365,samesite="lax")
    return resp

@app.get("/api/search")
async def api_search(q:str=Query(...,min_length=1)):
    if not q.strip(): return JSONResponse({"error":"请输入"},400)
    query=q.strip(); t0=time.time()
    ck=hashlib.md5(f"v7_{query}".encode()).hexdigest()
    cached=cget(ck)
    if cached:
        cached['cached']=True; cached['elapsed']=round(time.time()-t0,3)
        return JSONResponse(cached)
    try:
        loop=asyncio.get_event_loop()
        raw=await asyncio.wait_for(loop.run_in_executor(None,search_all,query),timeout=28.0)
        results=process(raw,query)
    except: results=[]
    elapsed=round(time.time()-t0,2)
    films=[r for r in results if r.is_film]
    resp={"query":query,"total":len(results),"elapsed":elapsed,"cached":False,
          "summary":{"影视资源":len(films),"其他资源":len(results)-len(films),"总计":len(results)},
          "filters":{"qualities":sorted(set(r.quality for r in results if r.quality)),
                     "languages":sorted(set(r.language for r in results if r.language)),
                     "formats":sorted(set(r.fmt for r in results if r.fmt))},
          "results":[r.td() for r in results]}
    cset(ck,resp,ttl=5)
    return JSONResponse(resp)

@app.get("/api/health")
async def health():
    return {"status":"ok","time":datetime.now().isoformat()}

@app.get("/",response_class=HTMLResponse)
async def index():
    path=os.path.join(os.path.dirname(os.path.abspath(__file__)),'templates','index.html')
    with open(path,'r',encoding='utf-8') as f: return f.read()

if __name__=="__main__":
    port=int(os.environ.get("PORT",8899))
    uvicorn.run(app,host="0.0.0.0",port=port,log_level="info")
