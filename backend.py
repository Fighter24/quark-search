#!/usr/bin/env python3
"""
夸克网盘资源搜索器 v5 - 高速+国际化版
优化: 缓存加速、多区域搜索、流式返回、缩短超时
"""
import asyncio, re, hashlib, time, json, os, threading
from datetime import datetime, timedelta
from urllib.parse import quote
from dataclasses import dataclass, field
from collections import OrderedDict, Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

from curl_cffi import requests as cffi_requests
from bs4 import BeautifulSoup
from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
import uvicorn, secrets

app = FastAPI(title="夸克网盘资源搜索器")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

ACCESS_PASSWORD = os.environ.get("ACCESS_PASSWORD", "").strip()
VALID_TOKENS = set()

class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if not ACCESS_PASSWORD:
            return await call_next(request)
        path = request.url.path
        if path in ("/api/health", "/api/login", "/api/debug"):
            return await call_next(request)
        token = request.cookies.get("quark_token") or request.query_params.get("token")
        if token and token in VALID_TOKENS:
            return await call_next(request)
        if path.startswith("/api/"):
            return JSONResponse({"error": "unauthorized", "need_password": True}, status_code=401)
        return HTMLResponse(LOGIN_HTML, status_code=401)

app.add_middleware(AuthMiddleware)

QUARK_LINK_RE = re.compile(r'https?://pan\.quark\.cn/s/[a-zA-Z0-9]+')
FETCH_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
}

# ══════════════════════════════════════════════════════════
#  内存缓存（TTL 30分钟，减少重复搜索）
# ══════════════════════════════════════════════════════════

_search_cache = {}
_cache_lock = threading.Lock()

def cache_get(key: str):
    with _cache_lock:
        entry = _search_cache.get(key)
        if entry and datetime.now() < entry['expires']:
            return entry['data']
    return None

def cache_set(key: str, data, ttl_minutes=30):
    with _cache_lock:
        _search_cache[key] = {'data': data, 'expires': datetime.now() + timedelta(minutes=ttl_minutes)}
        # 限制缓存大小
        if len(_search_cache) > 50:
            oldest = min(_search_cache, key=lambda k: _search_cache[k]['expires'])
            del _search_cache[oldest]

# ══════════════════════════════════════════════════════════
#  元数据提取
# ══════════════════════════════════════════════════════════

QUALITY = [
    (r'\b(4K|4k|2160[Pp]|UHD|超清|原画)\b', '4K'),
    (r'\b(1080[Pp]|FHD|全高清)\b', '1080P'),
    (r'\b(720[Pp]|HD)\b', '720P'),
    (r'\b(480[Pp]|SD)\b', '480P'),
    (r'\b(蓝光|BluRay|BD|蓝光原盘|REMUX)\b', '蓝光'),
    (r'\b(HDR|杜比视界|Dolby.?Vision|DV)\b', 'HDR'),
    (r'\b(IMAX)\b', 'IMAX'),
    (r'\b(TC|TS|枪版|CAM)\b', 'TC枪版'),
]

FMT_PAT = [
    (r'\.mkv\b', 'MKV'), (r'\.mp4\b', 'MP4'), (r'\.avi\b', 'AVI'),
    (r'\.rmvb\b', 'RMVB'), (r'\.iso\b', 'ISO'), (r'\.(rar|zip|7z)\b', '压缩包'),
]

LANG_PAT = [
    (r'(粤语|广东话|Cantonese|粵語)', '粤语'),
    (r'(英语|英文|English|英語)', '英语'),
    (r'(日语|日文|Japanese|日本語)', '日语'),
    (r'(韩语|韩文|Korean|한국어)', '韩语'),
    (r'(法语|法文|French)', '法语'),
    (r'(德语|德文|German)', '德语'),
    (r'(泰语|泰文|Thai)', '泰语'),
    (r'(国语|国配|普通话|中文配音|汉语|简中|繁中)', '普通话'),
]

DATE_PAT = [
    re.compile(r'(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})[日号]?'),
    re.compile(r'(\d{4})(\d{2})(\d{2})[-_]'),
    re.compile(r'(\d{4})[-/](\d{2})[-/](\d{2})'),
    re.compile(r'发布于\s*(\d{4}-\d{2}-\d{2})'),
    re.compile(r'(\d{4}-\d{2}-\d{2})\s*\d{2}:\d{2}'),
]

SIZE_RE = re.compile(r'(\d+\.?\d*)\s*(GB|TB|MB|G|T|M)\b', re.I)

TAG_PAT = [
    (r'(中字|中英|内嵌|内封|简中|繁中|中文字幕)', '中字'),
    (r'(杜比|Dolby|Atmos|DTS|全景声)', '杜比音效'),
    (r'(合集|全季|全系列|Complete)', '合集'),
    (r'(无水印|纯净版)', '纯净版'),
    (r'(高码|高码率|BDRip|WEB-DL)', '高清压制'),
    (r'(完结|全\d+集)', '已完结'),
    (r'(更新中|连载)', '更新中'),
]

FILM_KW = ['电影','剧集','电视剧','动漫','动画','综艺','纪录片','短剧',
           'Movie','TV','Season','季','集','4K','1080P','720P',
           '蓝光','BluRay','BD','中字','字幕','导演','主演','豆瓣']


def _first(text, patterns):
    for p, l in patterns:
        if re.search(p, text, re.I): return l
    return ""

def _all(text, patterns):
    s, r = set(), []
    for p, l in patterns:
        if re.search(p, text, re.I) and l not in s:
            s.add(l); r.append(l)
    return r

def _size(text):
    m = SIZE_RE.search(text)
    return f"{m.group(1)}{m.group(2).upper()}" if m else ""

def _date(text):
    for p in DATE_PAT:
        m = p.search(text)
        if m:
            try:
                y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
                if 2020 <= y <= 2027 and 1 <= mo <= 12 and 1 <= d <= 31:
                    return f"{y}-{mo:02d}-{d:02d}"
            except: continue
    return ""

def extract_meta(text):
    return {
        "quality": _first(text, QUALITY), "fmt": _first(text, FMT_PAT),
        "lang": _first(text, LANG_PAT), "size": _size(text),
        "date": _date(text), "tags": _all(text, TAG_PAT),
        "is_film": any(re.search(k, text, re.I) for k in FILM_KW),
    }

# ══════════════════════════════════════════════════════════
#  评分
# ══════════════════════════════════════════════════════════

QB = {'4K':25,'蓝光':22,'HDR':20,'1080P':15,'720P':8,'IMAX':20,'480P':3,'TC枪版':-10}
FB = {'MKV':10,'MP4':8,'ISO':12,'AVI':3,'RMVB':1,'压缩包':-5}
TB = {'中字':8,'杜比音效':10,'合集':5,'纯净版':5,'高清压制':8,'已完结':5,'更新中':-3}

def calc_score(r) -> int:
    s = 50
    s += QB.get(r.quality, 0) + FB.get(r.fmt, 0)
    for t in r.extra_tags: s += TB.get(t, 2)
    if r.size: s += 3
    if r.language: s += 5
    if r.publish_date: s += 3
    if r.ref_count > 1: s += min(r.ref_count * 3, 15)
    if r.valid: s += 10
    else: s -= 30
    return min(max(s, 0), 100)

@dataclass
class QR:
    title: str; link: str; source: str = ""; quality: str = ""; fmt: str = ""
    size: str = ""; language: str = ""; publish_date: str = ""
    description: str = ""; is_film: bool = False; score: int = 0
    extra_tags: list = field(default_factory=list)
    source_url: str = ""; ref_count: int = 1; valid: bool = True

    def to_dict(self):
        return {k: getattr(self, k) for k in [
            'title','link','source','source_url','quality','format','size',
            'language','publish_date','description','is_film','score','ref_count','valid'
        ]} | {'tags': self.extra_tags, 'format': self.fmt}

# ══════════════════════════════════════════════════════════
#  快速页面抓取（缩短超时）
# ══════════════════════════════════════════════════════════

def extract_title(el, page_title, query):
    t = el.get_text(strip=True)
    if t and len(t) > 5 and 'pan.quark.cn' not in t: return t
    for tag in ['h3','h2','h1','strong','b','dt','label']:
        prev = el.find_previous_sibling(tag)
        if not prev:
            p = el.find_parent(['div','li','td','article','section','tr'])
            if p: prev = p.find(tag)
        if prev:
            txt = prev.get_text(strip=True)
            if len(txt) > 3 and 'pan.quark.cn' not in txt: return txt
    return page_title if page_title and len(page_title) > 3 else query

def fetch_links(url, query="", timeout=5):
    """快速抓取页面夸克链接"""
    rv = []
    try:
        resp = cffi_requests.get(url, headers=FETCH_HEADERS, impersonate='chrome120', timeout=timeout)
        if resp.status_code != 200 or len(resp.text) < 300: return rv
        soup = BeautifulSoup(resp.text, 'html.parser')
        pt = soup.title.get_text(strip=True) if soup.title else ""
        for a in soup.find_all('a', href=QUARK_LINK_RE):
            lk = a['href']
            if 'pan.quark.cn/s/' not in lk: continue
            p = a.find_parent(['div','li','td','p','article','section','blockquote','dd','dt','tr'])
            ctx = p.get_text(strip=True)[:500] if p else a.get_text(strip=True)[:150]
            rv.append({'link': lk, 'context': f"{pt}\n{ctx}",
                       'title': extract_title(a, pt, query),
                       'source': url.split('/')[2], 'source_url': url})
        return rv
    except: return rv

# ══════════════════════════════════════════════════════════
#  搜索源 1: ddgs (提速：减少查询数+结果数)
# ══════════════════════════════════════════════════════════

PROMISING = ['网盘','夸克','quark','下载','资源','分享','链接','yunpan','pan.quark',
             'thread-','viewthread','/d/','4K','1080P','电影','电视剧','动漫','BT',
             'disk','pansearch','panso','so.','search']

def is_prom(result):
    return any(k in f"{result.get('href','')} {result.get('title','')} {result.get('body','')}".lower() for k in PROMISING)

def search_ddgs(query):
    try:
        from ddgs import DDGS
    except ImportError: return []

    rr, seen_l, fetched = [], set(), set()

    # 多语言搜索查询（中文 + 英文）
    queries = [
        (f'{query} 夸克网盘', 'cn-zh'),
        (f'{query} 夸克 下载', 'cn-zh'),
        (f'{query} pan.quark.cn', 'wt-wt'),  # 全球搜索
        (f'"{query}" quark drive download', 'wt-wt'),  # 英文搜索
        (f'{query} quark pan download', 'wt-wt'),
    ]

    all_res = []
    for q, region in queries:
        try:
            results = list(DDGS().text(q, max_results=5, region=region))
            all_res.extend(results)
        except: continue

    promising = [r for r in all_res if is_prom(r)]

    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {}
        for r in promising:
            url = r.get('href', '')
            if url not in fetched:
                fetched.add(url)
                futs[ex.submit(fetch_links, url, query, 5)] = url
        for f in as_completed(futs, timeout=15):
            try:
                for pr in f.result(timeout=10):
                    cl = QUARK_LINK_RE.search(pr['link'])
                    if cl:
                        cl = cl.group(0).rstrip('/')
                        if cl not in seen_l:
                            seen_l.add(cl); pr['link'] = cl; rr.append(pr)
            except: continue
    return rr

# ══════════════════════════════════════════════════════════
#  搜索源 2: 夸克专属搜索站（精简+加速）
# ══════════════════════════════════════════════════════════

QUARK_SITES = [
    'https://www.aipanso.com/search?k={q}&t=quark',
    'https://www.pansearch.me/search?keyword={q}',
    'https://xiaokupan.com/search?keyword={q}',
]

def search_quark_sites(query):
    rr, seen = [], set()
    enc = quote(query)
    with ThreadPoolExecutor(max_workers=3) as ex:
        futs = {ex.submit(fetch_links, u.replace('{q}', enc), query, 5): u for u in QUARK_SITES}
        for f in as_completed(futs, timeout=10):
            try:
                for pr in f.result(timeout=8):
                    cl = QUARK_LINK_RE.search(pr['link'])
                    if cl:
                        cl = cl.group(0).rstrip('/')
                        if cl not in seen: seen.add(cl); pr['link'] = cl; rr.append(pr)
            except: continue
    return rr

# ══════════════════════════════════════════════════════════
#  搜索源 3: 快速论坛搜索（只搜最可靠的来源）
# ══════════════════════════════════════════════════════════

def search_forums(query):
    enc = quote(query)
    rr, seen = [], set()

    # 只搜最可靠的论坛
    forums = [('https://www.yunpanziyuan.xyz/search.php?mod=forum&searchsubmit=yes&kw={q}', 'yunpanziyuan.xyz')]

    for tmpl, name in forums:
        try:
            url = tmpl.replace('{q}', enc)
            resp = cffi_requests.get(url, headers=FETCH_HEADERS, impersonate='chrome120', timeout=6)
            if resp.status_code != 200 or len(resp.text) < 500: continue
            soup = BeautifulSoup(resp.text, 'html.parser')
            threads = []
            for a in soup.find_all('a', href=True):
                h = a['href']
                if any(p in h for p in ['thread-','viewthread','/d/','forum.php']):
                    threads.append(h if h.startswith('http') else '/'.join(url.split('/')[:3]) + h)

            with ThreadPoolExecutor(max_workers=3) as ex:
                futs = {ex.submit(fetch_links, t, query, 5): t for t in threads[:3]}
                for f in as_completed(futs, timeout=8):
                    try:
                        for pr in f.result(timeout=6):
                            cl = QUARK_LINK_RE.search(pr['link'])
                            if cl:
                                cl = cl.group(0).rstrip('/')
                                if cl not in seen: seen.add(cl); pr['link'] = cl; rr.append(pr)
                    except: continue
        except: continue
    return rr

# ══════════════════════════════════════════════════════════
#  链接校验（极速版，2秒超时）
# ══════════════════════════════════════════════════════════

def check_valid(link, timeout=3):
    try:
        resp = cffi_requests.head(link, headers=FETCH_HEADERS, impersonate='chrome120',
                                   timeout=timeout, allow_redirects=True)
        return resp.status_code in (200,301,302,303,307,308,403)
    except: return True

def validate_quick(results):
    with ThreadPoolExecutor(max_workers=10) as ex:
        futs = {ex.submit(check_valid, r.link): i for i, r in enumerate(results)}
        for f in as_completed(futs, timeout=8):
            i = futs[f]
            try: results[i].valid = f.result(timeout=5)
            except: results[i].valid = True
    return results

# ══════════════════════════════════════════════════════════
#  聚合搜索
# ══════════════════════════════════════════════════════════

def search_all(query):
    all_r, seen = [], set()
    with ThreadPoolExecutor(max_workers=3) as ex:
        futs = {
            ex.submit(search_ddgs, query): 'ddgs',
            ex.submit(search_quark_sites, query): 'quark',
            ex.submit(search_forums, query): 'forums',
        }
        for f in as_completed(futs, timeout=20):
            try:
                for r in f.result(timeout=15):
                    cl = r['link'].rstrip('/')
                    if cl not in seen: seen.add(cl); all_r.append(r)
            except: pass
    return all_r

def process(raw, query):
    seen = OrderedDict(); ref = Counter()
    for item in raw:
        lk = item['link']
        if 'pan.quark.cn/s/' not in lk: continue
        lk = lk.split('?')[0].rstrip('/'); key = hashlib.md5(lk.encode()).hexdigest()
        ref[key] += 1
        title = item.get('title',''); ctx = item.get('context','')
        src = item.get('source',''); surl = item.get('source_url','')
        full = f"{title} {query} {ctx}"; meta = extract_meta(full)
        desc = ctx[max(0,ctx.lower().find('pan.quark.cn')-60):ctx.lower().find('pan.quark.cn')+100] if 'pan.quark.cn' in ctx.lower() else ctx[:150]
        qr = QR(title=title or ctx[:100] or query, link=lk, source=src, source_url=surl,
                quality=meta['quality'], fmt=meta['fmt'], size=meta['size'],
                language=meta['lang'], publish_date=meta['date'],
                description=desc[:200], is_film=meta['is_film'], extra_tags=meta['tags'])
        if key in seen:
            ex = seen[key]
            for a in ['quality','fmt','size','language','publish_date','description']:
                if len(str(getattr(qr,a))) > len(str(getattr(ex,a))): setattr(ex, a, getattr(qr,a))
            for t in qr.extra_tags:
                if t not in ex.extra_tags: ex.extra_tags.append(t)
        else: seen[key] = qr
    for k, c in ref.items():
        if k in seen: seen[k].ref_count = c
    for r in seen.values(): r.score = calc_score(r)
    ranked = sorted(seen.values(), key=lambda x: x.score, reverse=True)
    return ranked

# ══════════════════════════════════════════════════════════
#  API
# ══════════════════════════════════════════════════════════

@app.get("/api/search")
async def api_search(
    q: str = Query(..., min_length=1),
    validate: bool = Query(False, description="是否校验链接（默认关闭以加速）"),
    filter_expired: bool = Query(True),
    debug: bool = Query(False, description="返回调试信息"),
):
    if not q.strip(): return JSONResponse({"error": "请输入"}, 400)

    query = q.strip()
    t0 = time.time()

    # 检查缓存
    ck = hashlib.md5(f"{query}_{validate}".encode()).hexdigest()
    cached = cache_get(ck)
    if cached:
        cached['cached'] = True
        cached['elapsed'] = round(time.time() - t0, 3)
        return JSONResponse(cached)

    debug_info = {} if debug else None

    try:
        loop = asyncio.get_event_loop()

        # 测试 ddgs
        if debug:
            try:
                from ddgs import DDGS
                t1 = time.time()
                test_results = list(DDGS().text("test", max_results=2, region='wt-wt'))
                debug_info['ddgs_test'] = f"OK, {len(test_results)} results in {round(time.time()-t1,1)}s"
            except Exception as e:
                debug_info['ddgs_test'] = f"FAIL: {str(e)[:100]}"

        raw = await asyncio.wait_for(loop.run_in_executor(None, search_all, query), timeout=22.0)
        results = process(raw, query)
    except asyncio.TimeoutError: results = []
    except Exception as e:
        print(f"Err: {e}"); results = []

    if validate and results:
        results = await loop.run_in_executor(None, validate_quick, results)
        for r in results: r.score = calc_score(r)
        results.sort(key=lambda x: x.score, reverse=True)
    if filter_expired:
        results = [r for r in results if r.valid]

    elapsed = round(time.time() - t0, 2)
    films = [r for r in results if r.is_film]

    resp = {
        "query": query, "total": len(results), "elapsed": elapsed, "cached": False,
        "summary": {"影视资源": len(films), "其他资源": len(results) - len(films), "总计": len(results)},
        "filters": {
            "qualities": sorted(set(r.quality for r in results if r.quality)),
            "languages": sorted(set(r.language for r in results if r.language)),
            "formats": sorted(set(r.fmt for r in results if r.fmt)),
        },
        "results": [r.to_dict() for r in results],
    }
    if debug_info: resp['debug'] = debug_info
    cache_set(ck, resp, ttl_minutes=15)
    return JSONResponse(resp)


@app.get("/api/health")
async def health():
    return {"status": "ok", "time": datetime.now().isoformat(), "uptime": "running"}

@app.get("/api/debug")
async def debug():
    """测试外网访问能力"""
    results = {}
    for url in [
        "https://duckduckgo.com",
        "https://html.duckduckgo.com",
        "https://www.aipanso.com",
        "https://www.pansearch.me",
        "https://github.com",
    ]:
        try:
            resp = cffi_requests.head(url, impersonate='chrome120', timeout=8, allow_redirects=True)
            results[url] = {"ok": True, "status": resp.status_code}
        except Exception as e:
            results[url] = {"ok": False, "error": str(e)[:200]}
    return JSONResponse({
        "render_networks": results,
        "has_password": bool(ACCESS_PASSWORD),
        "uptime": "running",
    })

@app.post("/api/login")
async def login(request: Request):
    try:
        body = await request.json()
        pwd = body.get("password", "")
    except:
        return JSONResponse({"error": "invalid request"}, status_code=400)
    if not ACCESS_PASSWORD or pwd != ACCESS_PASSWORD:
        return JSONResponse({"error": "密码错误"}, status_code=403)
    token = secrets.token_urlsafe(32)
    VALID_TOKENS.add(token)
    resp = JSONResponse({"ok": True})
    resp.set_cookie("quark_token", token, httponly=True, max_age=86400*365, samesite="lax")
    return resp

LOGIN_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no, viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes">
<title>夸克搜 - 登录</title>
<style>
  :root{--bg:#08080f;--card:#141428;--input:#1a1a30;--border:#28284a;--text:#d0d0e8;--text2:#7070a0;--acc:#6c5ce7;--acc2:#a29bfe;--rad:14px}
  *{margin:0;padding:0;box-sizing:border-box}
  body{font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif;background:var(--bg);color:var(--text);min-height:100vh;display:flex;align-items:center;justify-content:center;padding:20px}
  .box{background:var(--card);border:1px solid var(--border);border-radius:var(--rad);padding:32px 24px;width:100%;max-width:360px;text-align:center}
  .logo{font-size:28px;font-weight:800;background:linear-gradient(135deg,var(--acc),var(--acc2));-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;margin-bottom:8px}
  .sub{color:var(--text2);font-size:13px;margin-bottom:24px}
  input{width:100%;background:var(--input);border:2px solid var(--border);border-radius:var(--rad);padding:12px 16px;color:var(--text);font-size:16px;outline:none;text-align:center;margin-bottom:16px;transition:.2s}
  input:focus{border-color:var(--acc);box-shadow:0 0 0 3px rgba(108,92,231,.2)}
  button{width:100%;background:linear-gradient(135deg,var(--acc),#5b4bd5);color:#fff;border:none;border-radius:var(--rad);padding:12px;font-size:15px;font-weight:600;cursor:pointer;transition:.15s}
  button:active{transform:scale(.97)}
  .err{color:#f66;font-size:12px;margin-top:12px;display:none}
  .hint{color:var(--text2);font-size:11px;margin-top:16px}
</style>
</head>
<body>
<div class="box">
  <div class="logo">夸克搜</div>
  <div class="sub">请输入访问密码</div>
  <input type="password" id="pwd" placeholder="访问密码" autofocus autocomplete="off">
  <button onclick="login()">验证</button>
  <div class="err" id="err">密码错误，请重试</div>
  <div class="hint">此服务为私有工具，需密码访问</div>
</div>
<script>
async function login(){
  const p=document.getElementById('pwd').value;
  if(!p)return;
  try{
    const r=await fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({password:p})});
    if(r.ok){window.location.href='/'}else{document.getElementById('err').style.display='block'}
  }catch(e){document.getElementById('err').style.display='block'}
}
document.getElementById('pwd').addEventListener('keydown',e=>{if(e.key==='Enter')login()});
</script>
</body>
</html>"""

@app.get("/", response_class=HTMLResponse)
async def index():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'templates', 'index.html')
    with open(path, 'r', encoding='utf-8') as f:
        return f.read()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8899))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
