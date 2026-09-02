#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
懂车帝车型库本地数据服务（AUTO PROMPT BOARD 配套）
实现依据：《懂车帝车型库抓取实现方法论.md》（2026-08 实测验证）

能力：
  - 全量车系库：按品牌 ID 1~40 遍历 App 接口（突破网页端 Top100 限制），去重合并
  - 品牌名补全：对每个有效品牌取任一车系调详情接口
  - 缓存：dongchedi_cache.json，7 天有效，冷启动才重建
  - 参考图：每车系 封面1 + 外观8 + 内饰5 + 细节4，缺图自动跳过，统一 750px
  - 图片代理 /imgproxy：解决浏览器跨域

接口：
  GET /api/ping                       健康检查 + 库统计
  GET /api/series?keyword=            全量搜索（车系名 + 品牌名双匹配）
  GET /api/series-by-brand?brand_id=  按品牌取车系
  GET /api/brands                     品牌列表
  GET /api/detail?id=                 车系详情
  GET /api/photos?id=                 参考图 {cover, exterior[], interior[], detail[], labels, pic_count}
  GET /api/rank?type=&limit=          懂车帝排行榜（sales/wholesale/hot/score/price_cut/range）
  GET /api/hotwords                   懂车帝热搜（热搜榜标题 + 滚动热搜词）
  GET /api/news?platform=             多平台车圈资讯（dongchedi/weibo/autohome/yiche/pcauto）
  GET /api/briefs[?date=YYYY-MM-DD]   每日车圈简报历史列表 / 读某天
  POST /api/brief-archive {date,content}  写入 briefs/YYYY-MM-DD.md
  GET /imgproxy?u=                    图片转发代理

用法：
  python dongchedi_server.py            # 默认 127.0.0.1:8765
  python dongchedi_server.py 9000       # 自定义端口

注意：所有请求间隔 >=0.05s 防限流；扫描范围可配置（默认 40）。
"""

import json
import os
import re
import sys
import time
import threading
import urllib.request
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# 关键：清掉可能继承的系统代理（用户工作环境可能有 HTTPS_PROXY，会被 urllib 自动使用）
# 但本服务需要直连懂车帝（CDN 不在代理白名单内），所以强制 no_proxy
for _k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
    os.environ.pop(_k, None)
os.environ["no_proxy"] = "*"
os.environ["NO_PROXY"] = "*"
# 同时把 urllib 的 proxy handler 清空
urllib.request.install_opener(urllib.request.build_opener(urllib.request.ProxyHandler({})))

# ---------------- 配置 ----------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BRIEFS_DIR = os.path.join(BASE_DIR, "briefs")
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
CITY = "北京"
CITY_ENC = urllib.parse.quote(CITY)
SCAN_RANGE = 40          # 品牌 ID 扫描范围（实测 1~40 覆盖全部有效品牌）
CACHE_FILE = os.path.join(BASE_DIR, "dongchedi_cache.json")
CACHE_TTL = 7 * 24 * 3600  # 7 天
SEARCH_LIMIT = 60        # 单次搜索返回上限
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://www.dongchedi.com/",
}

DCDAPP_SERIES = "https://www.dcdapp.com/motor/brand/m/v1/select/series/?city_name=" + CITY_ENC
DETAIL_URL = ("https://www.dongchedi.com/motor/car_page/m/v1/series_all_json/"
              "?series_id={sid}&city_name=" + CITY_ENC + "&show_city_price=1")
PICTURE_URL = ("https://www.dongchedi.com/motor/pc/car/series/get_series_picture"
               "?aid=1839&app_name=auto_web_pc&series_id={sid}&category={cat}&offset=0&count=1")

# ---------------- 全局库 ----------------
LOCK = threading.Lock()
LIB = {"built_at": 0, "brands": {}, "series": []}   # 内存缓存
PHOTO_CACHE = {}                                     # sid -> photos dict


def log(msg):
    print("[dcd] " + msg, flush=True)


def fetch(url, method="GET", data=None, timeout=12):
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, headers=HEADERS, method=method)
    return urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8")


def norm_img(url):
    """图片 URL 规范化：统一 750px 宽，保留扩展名（坑：正则吞 .jpg 后缀会导致代理 502）"""
    if not url:
        return ""
    if url.startswith("//"):
        url = "https:" + url
    m = re.search(r"~tplv-[^?]*?\.(jpg|jpeg|png|webp)", url)
    if m:
        url = re.sub(r"~tplv-[^?]*?\.(jpg|jpeg|png|webp)",
                     "~tplv-resize:750:0." + m.group(1), url)
    return url


# ---------------- Step 1+2：全量库构建 ----------------
def build_full_series(scan_range=SCAN_RANGE):
    series_map, valid_brands = {}, []
    for bid in range(1, scan_range + 1):
        try:
            obj = json.loads(fetch(DCDAPP_SERIES, "POST",
                                   {"offset": 0, "limit": 200, "is_refresh": 1,
                                    "city_name": CITY, "brand": bid}))
            items = (obj.get("data") or {}).get("series") or []
        except Exception:
            items = []
        if items:
            valid_brands.append(bid)
            for s in items:
                sid = str(s.get("id") or s.get("concern_id") or "")
                if not sid or sid in series_map:
                    continue
                series_map[sid] = {
                    "id": sid,
                    "name": s.get("outter_name") or "",
                    "brand_id": str(s.get("brand_id") or bid),   # 统一 str，与 brands 的 key 一致
                    "price": s.get("dealer_price") or "",
                    "min_price": s.get("min_price"),
                    "max_price": s.get("max_price"),
                    "cover": norm_img(s.get("cover_url") or ""),
                    "count": s.get("count") or 0,
                }
            log("brand %d: %d series (total %d)" % (bid, len(items), len(series_map)))
        time.sleep(0.1)

    brands = {}
    for bid in valid_brands:
        sid = next((s["id"] for s in series_map.values() if str(s["brand_id"]) == str(bid)), None)
        if not sid:
            continue
        try:
            d = json.loads(fetch(DETAIL_URL.format(sid=sid)))
            name = (d.get("data") or {}).get("brand_name") or ""
            if name:
                brands[str(bid)] = name
        except Exception:
            pass
        time.sleep(0.05)
    return {"built_at": time.time(), "brands": brands, "series": list(series_map.values())}


def load_library(force=False):
    """内存缓存 → 磁盘缓存(dongchedi_cache.json 全量 4534 车系)"""
    global LIB
    with LOCK:
        if LIB["series"] and not force:
            return LIB
    # 直接读取全量缓存（fetch_all.py 生成的 dongchedi_cache.json）
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                raw = json.load(f)
            series = raw.get("series") or []
            brands_list = raw.get("brands") or []
            # 转换为服务内部结构：series 用 id/name/cover/brand_id，brands 用 {id: name}
            series_map = []
            for s in series:
                sid = str(s.get("series_id") or s.get("id") or "")
                if not sid:
                    continue
                series_map.append({
                    "id": sid,
                    "name": s.get("series_name") or s.get("name") or "",
                    "brand_id": str(s.get("brand_id") or ""),
                    "brand": s.get("brand_name") or "",
                    "price": s.get("price") or "",
                    "min_price": None,
                    "max_price": None,
                    "level": s.get("level") or "",
                    "energy": s.get("new_energy") or "",
                    "cover": norm_img(s.get("image_url") or s.get("cover") or ""),
                    "count": 0,
                })
            brands = {}
            for b in brands_list:
                bid = str(b.get("brand_id") or "")
                name = b.get("brand_name") or ""
                if bid and name:
                    brands[bid] = name
            # 兜底：从 series 里补品牌映射
            for s in series_map:
                if s["brand_id"] and s["brand_id"] not in brands and s["brand"]:
                    brands[s["brand_id"]] = s["brand"]
            lib = {"built_at": time.time(), "brands": brands, "series": series_map}
            with LOCK:
                LIB = lib
            log("cache hit: %d series / %d brands (全量缓存)" % (len(series_map), len(brands)))
            return LIB
        except Exception as e:
            log("cache read failed: %s" % e)
    # 无缓存时回退到旧抓取逻辑（接口已失效，仅作兜底）
    log("no cache, building legacy library...")
    lib = build_full_series()
    with LOCK:
        LIB = lib
    return lib


def rebuild_async():
    t = threading.Thread(target=lambda: load_library(force=True), daemon=True)
    t.start()
    return t


# ---------------- Step 4：参考图 ----------------
def get_photos(sid):
    """按《懂车帝全量车型库与参考图获取实现方法论 v2》第 2.4 节索引规律取主视图。
    外观: wg[1]=正面, wg[3]=侧面, wg[5]=背面, 越界顺序补图，最多取 8 张
    内饰: ns[0]=前排全景, ns[1]=中控/方向盘，最多取 5 张
    细节: xg 类别取细节图，最多取 4 张
    返回: {cover, exterior[], interior[], detail[], labels{exterior[],interior[],detail[]}, pic_count}
    """
    with LOCK:
        if sid in PHOTO_CACHE:
            return PHOTO_CACHE[sid]
    result = {"cover": "", "exterior": [], "interior": [], "detail": [],
              "labels": {"exterior": [], "interior": [], "detail": []},
              "pic_count": 0}
    try:
        d = json.loads(fetch(DETAIL_URL.format(sid=sid)))
        data = d.get("data") or {}
        result["cover"] = norm_img(data.get("cover_url") or "")
        # 懂车帝返回 series_pic_count 字段表示该车系总图片数
        result["pic_count"] = data.get("series_pic_count") or data.get("pic_count") or 0
    except Exception:
        pass
    # 外观：按固定索引取主视图，不足按顺序补图，最多 8 张
    ext_labels = ["正面", "侧面", "背面", "45°前", "45°后", "车头", "车尾", "轮毂"]
    ext_indices = [1, 3, 5, 0, 2, 4, 6, 7]
    try:
        d = json.loads(fetch(PICTURE_URL.format(sid=sid, cat="wg")))
        pl = ((d.get("data") or {}).get("picture_list") or [])
        urls = (pl[0].get("large_pic_url") or pl[0].get("pic_url") or []) if pl else []
        taken = []
        for idx, lbl in zip(ext_indices, ext_labels):
            if len(result["exterior"]) >= 8:
                break
            if idx < len(urls):
                uu = norm_img(urls[idx])
                if uu and uu not in taken and uu != result["cover"]:
                    taken.append(uu)
                    result["exterior"].append(uu)
                    result["labels"]["exterior"].append(lbl)
        # 不足 8 张按剩余顺序补图
        for idx2, u in enumerate(urls):
            if len(result["exterior"]) >= 8:
                break
            uu = norm_img(u)
            if uu and uu not in taken and uu != result["cover"]:
                taken.append(uu)
                result["exterior"].append(uu)
                result["labels"]["exterior"].append("外观" + str(idx2 + 1))
    except Exception:
        pass
    time.sleep(0.1)
    # 内饰：ns[0]=前排全景, ns[1]=中控，最多取 5 张
    int_labels = ["前排全景", "中控/方向盘", "后排座椅", "挡把/扶手", "仪表盘"]
    try:
        d = json.loads(fetch(PICTURE_URL.format(sid=sid, cat="ns")))
        pl = ((d.get("data") or {}).get("picture_list") or [])
        urls = (pl[0].get("large_pic_url") or pl[0].get("pic_url") or []) if pl else []
        for idx3, lbl in zip(range(len(int_labels)), int_labels):
            if len(result["interior"]) >= 5:
                break
            if idx3 < len(urls):
                uu = norm_img(urls[idx3])
                if uu:
                    result["interior"].append(uu)
                    result["labels"]["interior"].append(lbl)
    except Exception:
        pass
    time.sleep(0.1)
    # 细节图（xg 类别）：最多取 4 张
    try:
        d = json.loads(fetch(PICTURE_URL.format(sid=sid, cat="xg")))
        pl = ((d.get("data") or {}).get("picture_list") or [])
        urls = (pl[0].get("large_pic_url") or pl[0].get("pic_url") or []) if pl else []
        for idx4 in range(min(4, len(urls))):
            uu = norm_img(urls[idx4])
            if uu:
                result["detail"].append(uu)
                result["labels"]["detail"].append("细节" + str(idx4 + 1))
    except Exception:
        pass
    with LOCK:
        PHOTO_CACHE[sid] = result
    return result


# ---------------- Step 6：搜索（车系名 + 品牌名双匹配） ----------------
def search_series(query):
    q = (query or "").strip().lower()
    if not q:
        return []
    with LOCK:
        series, brands = LIB["series"], LIB["brands"]
    hit_name, hit_brand = [], []
    for s in series:
        if q in (s.get("name") or "").lower():
            hit_name.append(s)
        elif q in (brands.get(s.get("brand_id")) or "").lower():
            hit_brand.append(s)
    hit_name.sort(key=lambda s: -(s.get("count") or 0))
    hit_brand.sort(key=lambda s: -(s.get("count") or 0))
    out = (hit_name + hit_brand)[:SEARCH_LIMIT]
    for s in out:
        s["brand"] = brands.get(s.get("brand_id")) or ""
    return out


def series_by_brand(bid):
    with LOCK:
        series, brands = LIB["series"], LIB["brands"]
    out = [dict(s, brand=brands.get(s.get("brand_id")) or "")
           for s in series if s.get("brand_id") == bid]
    out.sort(key=lambda s: -(s.get("count") or 0))
    return out[:200]


def get_series(sid):
    with LOCK:
        for s in LIB["series"]:
            if s["id"] == sid:
                return s
    return None


def get_brand_list():
    with LOCK:
        brands, series = LIB["brands"], LIB["series"]
    counts = {}
    for s in series:
        b = s.get("brand_id")
        counts[b] = counts.get(b, 0) + 1
    return [{"brand_id": b, "name": n, "series_count": counts.get(b, 0)}
            for b, n in sorted(brands.items(), key=lambda kv: -counts.get(kv[0], 0))]


# ---------------- 懂车帝排行榜 / 热搜 ----------------
RANK_DATA_URL = "https://www.dongchedi.com/motor/pc/car/rank_data"
HOTWORD_URL = "https://www.dongchedi.com/motor/searchpage/launcher/main/v1/"
RANK_LOCK = threading.Lock()
RANK_CACHE = {}          # key -> (ts, data)
RANK_TTL = 1800          # 30 分钟缓存（榜单按天更新，热搜变动较快）
HOT_CACHE = {"ts": 0, "data": None}

# type -> (rank_data_type, sub_rank_data_type, needs_month, value_field)
# 实测：rank_data_type=11 + month=YYYYMM 返回真实月销量（如星愿32306台）
#        rank_data_type=20000 返回的是关注度指数（如小米SU7 1440117），并非销量
RANK_TYPES = {
    "sales":     (11, None, True, "count"),    # 销量榜-零售量（需 month 参数）
    "wholesale": (12, None, True, "count"),    # 销量榜-批发量
    "hot":       (1, None, False, "count"),    # 热门榜（关注度指数）
    "score":     (3, None, False, "score"),    # 懂车分榜
    "price_cut": (10002, None, False, "count"),# 降价榜
    "range":     (10001, None, False, "count"), # 新能源续航榜
}


def _last_month():
    """上个月 YYYYMM（当月销量数据通常下月初才出齐）"""
    import datetime
    now = datetime.datetime.now()
    first = now.replace(day=1)
    last = first - datetime.timedelta(days=1)
    return last.strftime("%Y%m")


def get_rank(rank_type="sales", limit=30):
    """排行榜。返回 {items:[...], month:''}"""
    if rank_type not in RANK_TYPES:
        return {"items": [], "month": ""}
    rt, sub, needs_month, vfield = RANK_TYPES[rank_type]
    ck = "%s_%s_%s" % (rank_type, sub, limit)
    now = time.time()
    with RANK_LOCK:
        hit = RANK_CACHE.get(ck)
        if hit and now - hit[0] < RANK_TTL:
            return hit[1]
    params = {"rank_data_type": rt, "count": limit}
    if sub is not None:
        params["sub_rank_data_type"] = sub
    if needs_month:
        params["month"] = _last_month()
    try:
        obj = json.loads(fetch(RANK_DATA_URL + "?" + urllib.parse.urlencode(params), timeout=15))
        d = obj.get("data") or {}
        items = []
        for it in (d.get("list") or []):
            items.append({
                "rank": it.get("rank"),
                "last_rank": it.get("last_rank"),
                "series_id": it.get("series_id"),
                "series_name": it.get("series_name") or "",
                "brand_name": it.get("brand_name") or "",
                "image": norm_img(it.get("image") or ""),
                "price": it.get("price") or "",
                "dealer_price": it.get("dealer_price") or "",
                "value": it.get(vfield) or it.get("count") or it.get("sells") or 0,
                "descender": it.get("descender_price") or 0,
                "tag": it.get("tag") or "",
            })
        month = d.get("month")
        if isinstance(month, list):
            month = month[0] if month else ""
        if not month and needs_month:
            month = _last_month()
        result = {"items": items[:limit], "month": month or d.get("sells_rank_month") or ""}
    except Exception as e:
        log("rank %s failed: %s" % (rank_type, e))
        result = {"items": [], "month": ""}
    with RANK_LOCK:
        RANK_CACHE[ck] = (now, result)
    return result


def get_hotwords():
    """懂车帝热搜：热搜榜标题 + 滚动热搜词。返回 {board:[{name,tops:[...]}], roll:[str]}"""
    now = time.time()
    with RANK_LOCK:
        if HOT_CACHE["data"] and now - HOT_CACHE["ts"] < RANK_TTL:
            return HOT_CACHE["data"]
    try:
        obj = json.loads(fetch(HOTWORD_URL, timeout=15))
        d = obj.get("data") or {}
        board, roll = [], []
        for b in (d.get("rank_board") or []):
            tops = []
            for t in (b.get("tops") or []):
                title = t.get("title") or ""
                if not title:
                    continue
                tops.append({
                    "title": title,
                    "desc": t.get("description") or "",
                    "hot_value": t.get("hot_value") or t.get("value") or "",
                    "url": t.get("open_url") or "",
                })
            if tops:
                board.append({"name": b.get("rank_name") or "", "tops": tops})
        for r in (d.get("hot_search_roll_info_v2") or []):
            for w in (r.get("text") or "").split("|"):
                if w.strip():
                    roll.append(w.strip())
        result = {"board": board, "roll": roll[:30]}
    except Exception as e:
        log("hotwords failed: %s" % e)
        result = {"board": [], "roll": []}
    with RANK_LOCK:
        HOT_CACHE["ts"], HOT_CACHE["data"] = now, result
    return result


# ---------------- 多平台车圈资讯 ----------------
NEWS_CACHE = {}          # {platform: {"ts":0,"data":None}}
NEWS_TTL = 600           # 10 分钟缓存

def _news_fetch(url, headers=None, timeout=10):
    """带自定义 headers 的 fetch"""
    h = dict(HEADERS)
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h, method="GET")
    return urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8")

def get_weibo_hot():
    """微博热搜（汽车相关优先，返回全部热搜）"""
    try:
        raw = _news_fetch("https://weibo.com/ajax/side/hotSearch",
                          headers={"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})
        obj = json.loads(raw)
        items = []
        for it in (obj.get("data") or {}).get("realtime") or []:
            title = it.get("note") or it.get("word") or ""
            if not title:
                continue
            items.append({
                "title": title,
                "desc": it.get("category") or "",
                "hot_value": str(it.get("num") or ""),
                "url": "https://s.weibo.com/weibo?q=" + urllib.parse.quote(title),
            })
        return items
    except Exception as e:
        log("weibo hot failed: %s" % e)
        return []

def get_autohome_news():
    """汽车之家资讯 - 通过搜索接口获取汽车新闻"""
    try:
        url = "https://www.autohome.com.cn/ashx/search/search.ashx?type=1&keyword=%E6%B1%BD%E8%BD%A6%E6%96%B0%E9%97%BB&pagesize=20&page=1"
        raw = _news_fetch(url, headers={"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36","Referer":"https://www.autohome.com.cn/"})
        obj = json.loads(raw)
        items = []
        for it in (obj.get("result") or {}).get("list") or []:
            title = it.get("title") or ""
            if not title:
                continue
            items.append({
                "title": title,
                "desc": (it.get("summary") or "")[:80],
                "hot_value": "",
                "url": it.get("url") or ("https://www.autohome.com.cn" + (it.get("detailurl") or "")),
            })
        return items
    except Exception as e:
        log("autohome news failed: %s" % e)
        return []

def get_yiche_news():
    """易车资讯"""
    try:
        url = "https://www.yiche.com/ajax/search/getresult/?keyword=%E6%B1%BD%E8%BD%A6%E6%96%B0%E9%97%BB&type=news&page=1&pagesize=20"
        raw = _news_fetch(url, headers={"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36","Referer":"https://www.yiche.com/"})
        obj = json.loads(raw)
        items = []
        data = obj.get("data") or obj
        for it in (data.get("list") or data.get("items") or []):
            title = it.get("title") or it.get("name") or ""
            if not title:
                continue
            items.append({
                "title": title,
                "desc": (it.get("summary") or it.get("desc") or "")[:80],
                "hot_value": "",
                "url": it.get("url") or "",
            })
        return items
    except Exception as e:
        log("yiche news failed: %s" % e)
        return []

def get_pcauto_news():
    """太平洋汽车资讯"""
    try:
        url = "https://api.pcauto.com.cn/autorest/api/v1/cms/search?keyword=%E6%B1%BD%E8%BD%A6%E6%96%B0%E9%97%BB&pageNo=1&pageSize=20"
        raw = _news_fetch(url, headers={"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})
        obj = json.loads(raw)
        items = []
        for it in (obj.get("data") or {}).get("list") or []:
            title = it.get("title") or ""
            if not title:
                continue
            items.append({
                "title": title,
                "desc": (it.get("summary") or "")[:80],
                "hot_value": "",
                "url": it.get("url") or "",
            })
        return items
    except Exception as e:
        log("pcauto news failed: %s" % e)
        return []

NEWS_PLATFORMS = {
    "dongchedi": {"name":"懂车帝","fetch":lambda: _dcd_news_items(),"search_url":"https://www.dongchedi.com/search?keyword={kw}"},
    "weibo":     {"name":"微博","fetch":get_weibo_hot,"search_url":"https://s.weibo.com/weibo?q={kw}"},
    "autohome":  {"name":"汽车之家","fetch":get_autohome_news,"search_url":"https://www.autohome.com.cn/search/#kw={kw}"},
    "yiche":     {"name":"易车","fetch":get_yiche_news,"search_url":"https://www.yiche.com/search?keyword={kw}"},
    "pcauto":    {"name":"太平洋汽车","fetch":get_pcauto_news,"search_url":"https://search.pcauto.com.cn/searchResult.html?q={kw}"},
}

def _dcd_news_items():
    """懂车帝热搜 → 统一 items 格式"""
    hw = get_hotwords()
    items = []
    for b in hw.get("board", []):
        for t in b.get("tops", []):
            items.append({
                "title": t.get("title") or "",
                "desc": t.get("desc") or "",
                "hot_value": str(t.get("hot_value") or ""),
                "url": t.get("url") or ("https://www.dongchedi.com/search?keyword=" + urllib.parse.quote(t.get("title") or "")),
            })
    if not items:
        for w in hw.get("roll", [])[:20]:
            items.append({"title":w,"desc":"","hot_value":"","url":"https://www.dongchedi.com/search?keyword="+urllib.parse.quote(w)})
    return items

def get_multi_news(platform):
    """多平台车圈资讯统一入口"""
    now = time.time()
    with RANK_LOCK:
        c = NEWS_CACHE.get(platform)
        if c and c["data"] is not None and now - c["ts"] < NEWS_TTL:
            return c["data"]
    p = NEWS_PLATFORMS.get(platform)
    if not p:
        return {"platform":platform,"name":"","items":[],"error":"unknown platform"}
    try:
        items = p["fetch"]()
        result = {"platform":platform,"name":p["name"],"items":items,"search_url":p["search_url"]}
    except Exception as e:
        log("multi_news %s failed: %s" % (platform, e))
        result = {"platform":platform,"name":p["name"],"items":[],"error":str(e),"search_url":p["search_url"]}
    with RANK_LOCK:
        NEWS_CACHE[platform] = {"ts":now,"data":result}
    return result


# ---------------- 每日车圈简报归档 ----------------
def brief_list():
    if not os.path.isdir(BRIEFS_DIR):
        os.makedirs(BRIEFS_DIR, exist_ok=True)
    dates = [fn[:-3] for fn in os.listdir(BRIEFS_DIR) if fn.endswith(".md")]
    dates.sort(reverse=True)
    return dates


def brief_read(date):
    fpath = os.path.join(BRIEFS_DIR, date + ".md")
    if not os.path.exists(fpath):
        return None
    with open(fpath, "r", encoding="utf-8") as f:
        return f.read()


def brief_save(date, content):
    if not date or not content:
        return False
    os.makedirs(BRIEFS_DIR, exist_ok=True)
    with open(os.path.join(BRIEFS_DIR, date + ".md"), "w", encoding="utf-8") as f:
        f.write(content)
    log("brief archived: %s (%d chars)" % (date, len(content)))
    return True


# ---------------- HTTP 服务 ----------------
BUILDING = {"flag": False}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass  # 静默访问日志

    def _cors(self):
        # 本地交付物：file://（Origin 通常为 "null" 或缺失）+ 127.0.0.1/localhost 同源
        # 拒绝其他来源，防止绑 0.0.0.0 后被外部站点调用
        origin = self.headers.get("Origin", "")
        local_ok = (
            not origin or origin == "null" or
            origin.startswith(("http://127.0.0.1", "http://localhost",
                               "https://127.0.0.1", "https://localhost"))
        )
        self.send_header("Access-Control-Allow-Origin", origin if local_ok else "null")
        self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self._cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        try:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length).decode("utf-8") if length else "{}"
            body = json.loads(raw)
        except Exception:
            body = {}
        try:
            if path == "/api/brief-archive":
                date = (body.get("date") or "").strip()
                content = body.get("content") or ""
                if not date or not content:
                    return self._json({"ok": False, "error": "date/content required"}, 400)
                brief_save(date, content)
                return self._json({"ok": True, "date": date})

            if path == "/api/ai":
                return self._ai_proxy(body)

            return self._json({"ok": False, "error": "unknown route"}, 404)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass  # 浏览器中断请求，静默忽略

    def _ai_proxy(self, body):
        """AI 代理：前端因 CORS 无法直连大模型 API（DeepSeek/通义/智谱/豆包均不开放跨域），
        由本地服务转发。Key 由前端传入，仅本次请求使用，不落盘、不缓存、不记录。
        body: { baseUrl, apiKey, model, messages, temperature?, max_tokens? }"""
        base = (body.get("baseUrl") or "").strip()
        key = (body.get("apiKey") or "").strip()
        model = (body.get("model") or "").strip()
        messages = body.get("messages") or []
        if not base or not key:
            return self._json({"ok": False, "error": "缺少 baseUrl 或 apiKey"}, 400)
        if not model:
            return self._json({"ok": False, "error": "缺少 model"}, 400)
        # 只允许转发到大模型服务商，防止 SSRF 滥用
        # 精确根域匹配：取 host 最后两段作为根域，避免 endswith 被子域绕过
        # （evildeepseek.com 不应被允许为 deepseek.com 的子域）
        host = urllib.parse.urlparse(base).netloc.lower()
        if "@" in host:
            host = host.rsplit("@", 1)[-1]
        if ":" in host:
            host = host.rsplit(":", 1)[0]
        parts = host.split(".")
        root = ".".join(parts[-2:]) if len(parts) >= 2 else host
        # 拒绝内网/回环/链路本地 IP（即使伪装成域名也拦）
        if host in ("localhost",) or host.startswith(("127.", "10.", "192.168.", "169.254.")) or \
           (host.startswith("172.") and len(parts) == 4 and 16 <= int(parts[1]) <= 31):
            return self._json({"ok": False, "error": "internal host blocked: " + host}, 403)
        ALLOWED_AI_ROOTS = {
            "deepseek.com", "openai.com", "aliyuncs.com",
            "bigmodel.cn", "volces.com", "moonshot.cn",
            "zhipuai.cn", "siliconflow.cn",
        }
        if root not in ALLOWED_AI_ROOTS:
            return self._json({"ok": False, "error": "不允许的服务商地址: " + host}, 403)
        url = base.rstrip("/") + "/chat/completions"
        payload = {"model": model, "messages": messages}
        if body.get("temperature") is not None:
            payload["temperature"] = body["temperature"]
        if body.get("max_tokens"):
            payload["max_tokens"] = body["max_tokens"]
        req_headers = {
            "Content-Type": "application/json",
            "Authorization": "Bearer " + key,
        }
        try:
            req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                         headers=req_headers, method="POST")
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            content = ""
            try:
                content = data["choices"][0]["message"]["content"]
            except (KeyError, IndexError, TypeError):
                pass
            if not content:
                err = data.get("error") or {}
                return self._json({"ok": False, "error": "模型返回为空", "detail": err}, 502)
            return self._json({"ok": True, "content": content})
        except urllib.error.HTTPError as e:
            try:
                detail = e.read().decode("utf-8")
            except Exception:
                detail = ""
            return self._json({"ok": False, "error": "HTTP %s" % e.code, "detail": detail[:500]}, 502)
        except Exception as e:
            return self._json({"ok": False, "error": str(e)}, 502)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path, qs = parsed.path, urllib.parse.parse_qs(parsed.query)

        if path == "/api/ping":
            with LOCK:
                lib = LIB
            return self._json({
                "ok": True, "built_at": lib.get("built_at", 0),
                "series_count": len(lib.get("series", [])),
                "brand_count": len(lib.get("brands", {})),
                "building": BUILDING["flag"],
            })

        if path == "/api/series":
            kw = (qs.get("keyword") or [""])[0]
            return self._json({"ok": True, "results": search_series(kw)})

        if path == "/api/series-by-brand":
            try:
                bid = int((qs.get("brand_id") or ["0"])[0])
            except ValueError:
                bid = 0
            return self._json({"ok": True, "results": series_by_brand(bid)})

        if path == "/api/brands":
            return self._json({"ok": True, "results": get_brand_list()})

        if path == "/api/detail":
            sid = (qs.get("id") or [""])[0]
            s = get_series(sid)
            if not s:
                return self._json({"ok": False, "error": "not found"}, 404)
            return self._json({"ok": True, "series": s})

        if path == "/api/photos":
            sid = (qs.get("id") or [""])[0]
            if not sid:
                return self._json({"ok": False, "error": "missing id"}, 400)
            return self._json({"ok": True, "photos": get_photos(sid)})

        if path == "/api/rank":
            rt = (qs.get("type") or ["sales"])[0]
            try:
                limit = int((qs.get("limit") or ["30"])[0])
            except ValueError:
                limit = 30
            if rt not in RANK_TYPES:
                return self._json({"ok": False, "error": "bad type"}, 400)
            return self._json({"ok": True, **get_rank(rt, limit)})

        if path == "/api/hotwords":
            return self._json({"ok": True, **get_hotwords()})

        if path == "/api/news":
            platform = (qs.get("platform") or ["dongchedi"])[0]
            return self._json({"ok": True, **get_multi_news(platform)})

        if path == "/api/briefs":
            if "date" in qs:
                date = (qs.get("date") or [""])[0].strip()
                content = brief_read(date)
                if content is None:
                    return self._json({"ok": False, "error": "该日期无简报"}, 404)
                return self._json({"ok": True, "date": date, "content": content})
            return self._json({"ok": True, "dates": brief_list()})

        if path == "/imgproxy":
            u = (qs.get("u") or [""])[0]
            if not u.startswith(("http://", "https://")):
                return self._json({"ok": False, "error": "bad url"}, 400)
            # 白名单：只允许懂车帝/字节系 CDN 根域，防 SSRF 探内网
            phost = urllib.parse.urlparse(u).netloc.lower()
            if "@" in phost:
                phost = phost.rsplit("@", 1)[-1]
            if ":" in phost:
                phost = phost.rsplit(":", 1)[0]
            pparts = phost.split(".")
            proot = ".".join(pparts[-2:]) if len(pparts) >= 2 else phost
            # 拒绝内网/回环/链路本地
            if phost == "localhost" or phost.startswith(("127.", "10.", "192.168.", "169.254.")) or \
               (phost.startswith("172.") and len(pparts) == 4 and 16 <= int(pparts[1]) <= 31):
                return self._json({"ok": False, "error": "internal host blocked: " + phost}, 403)
            IMG_ALLOWED_ROOTS = {
                "dongchedi.com", "douyinpic.com", "byteimg.com", "bytetos.com",
                "bytecdn.cn", "pstatp.com", "toutiao.com", "ixigua.com",
                "snssdk.com", "bytego.cn",
            }
            if proot not in IMG_ALLOWED_ROOTS:
                return self._json({"ok": False, "error": "host not allowed: " + phost}, 403)
            try:
                req = urllib.request.Request(u, headers=HEADERS)
                raw = urllib.request.urlopen(req, timeout=15).read()
                ctype = "image/jpeg"
                if ".png" in u.lower():
                    ctype = "image/png"
                elif ".webp" in u.lower():
                    ctype = "image/webp"
                self.send_response(200)
                self._cors()
                self.send_header("Content-Type", ctype)
                self.send_header("Cache-Control", "public, max-age=86400")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
            except Exception as e:
                self._json({"ok": False, "error": str(e)}, 502)
            return

        return self._json({"ok": False, "error": "unknown route"}, 404)


def main():
    print("=" * 60)
    print(" 懂车帝车型库本地数据服务")
    print(" 接口: http://127.0.0.1:%d/api/ping" % PORT)
    print(" 文档: 懂车帝车型库抓取实现方法论.md")
    print("=" * 60)
    serve = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    log("listening on http://127.0.0.1:%d" % PORT)

    def warmup():
        BUILDING["flag"] = True
        try:
            load_library()
        finally:
            BUILDING["flag"] = False

    threading.Thread(target=warmup, daemon=True).start()  # 命中缓存秒开，否则后台构建
    try:
        serve.serve_forever()
    except KeyboardInterrupt:
        log("bye")


if __name__ == "__main__":
    main()
