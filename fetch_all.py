# -*- coding: utf-8 -*-
"""懂车帝全量车系抓取：all_brand -> brand/m/v1/series 遍历"""
import urllib.request, json, ssl, time, sys

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE
UA = {"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
      "Referer":"https://www.dongchedi.com/"}

def get_json(url, retries=3):
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=UA)
            r = urllib.request.urlopen(req, timeout=25, context=ctx)
            return json.loads(r.read().decode('utf-8','ignore'))
        except Exception as e:
            if i == retries-1:
                return None
            time.sleep(1.5)
    return None

def fetch_brands():
    d = get_json("https://www.dongchedi.com/motor/pc/car/brand/all_brand")
    if not d: return []
    brands = []
    for b in d["data"]["brand"]:
        info = b.get("info", {})
        bid = info.get("brand_id")
        name = info.get("brand_name")
        if bid and name:
            brands.append({"brand_id": bid, "brand_name": name,
                           "on_sale_series_count": info.get("on_sale_series_count", 0),
                           "pinyin": info.get("pinyin","")})
    return brands

def fetch_series(brand_id, brand_name):
    d = get_json(f"https://www.dongchedi.com/motor/brand/m/v1/series?brand_id={brand_id}")
    if not d or d.get("status") not in ("success", 0):
        return []
    out = []
    for it in d.get("data", []):
        if it.get("type") != "1002":
            continue
        info = it["info"]
        dcd = info.get("dcd_score", {}) or {}
        out.append({
            "series_id": info.get("series_id"),
            "series_name": info.get("series_name"),
            "brand_id": brand_id,
            "brand_name": brand_name,
            "sub_brand_name": info.get("sub_brand_name", ""),
            "price": info.get("price", ""),
            "official_price": info.get("official_price", ""),
            "new_energy": info.get("new_energy", ""),
            "level": dcd.get("outter_name", ""),  # 级别如 紧凑型轿车
            "score": dcd.get("score", ""),
            "image_url": info.get("image_url", ""),
            "car_name": info.get("car_name", ""),
        })
    return out

def main():
    brands = fetch_brands()
    print(f"[1/3] 品牌总数: {len(brands)}", flush=True)
    all_series = []
    seen = set()
    ok_brand = 0
    for i, b in enumerate(brands):
        bid, bname = b["brand_id"], b["brand_name"]
        ss = fetch_series(bid, bname)
        if ss:
            ok_brand += 1
        for s in ss:
            sid = s["series_id"]
            if sid and sid not in seen:
                seen.add(sid)
                all_series.append(s)
        if (i+1) % 50 == 0:
            print(f"  进度 {i+1}/{len(brands)} 品牌，累计车系 {len(all_series)}", flush=True)
        time.sleep(0.12)
    print(f"[2/3] 完成：有效品牌 {ok_brand}/{len(brands)}，去重后车系总数 {len(all_series)}", flush=True)
    with open("dongchedi_cache.json","w",encoding="utf-8") as f:
        json.dump({"brands": brands, "series": all_series, "series_count": len(all_series),
                   "fetch_time": time.strftime("%Y-%m-%d %H:%M:%S")}, f, ensure_ascii=False)
    print(f"[3/3] 已写入 dongchedi_cache.json", flush=True)

if __name__ == "__main__":
    main()
