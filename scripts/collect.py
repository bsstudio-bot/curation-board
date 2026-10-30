"""매일 큐레이션 데이터 수집 스크립트.

- 네이버 맘카페: 주간 인기글(비로그인 공개 API)
- 인스타그램: Meta 공식 API(Business Discovery). IG_TOKEN, IG_USER_ID 환경변수가 있을 때만 수집.
  없으면 직전 data.json의 인스타 데이터를 유지하고, 그것도 없으면 seed 파일을 쓴다.

결과는 site/data.json 하나로 저장한다. 외부 패키지 없이 표준 라이브러리만 쓴다.
"""
import json
import os
import statistics
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KST = timezone(timedelta(hours=9))
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36"


def get_json(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def load(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


# ---------------------------------------------------------------- 맘카페
def collect_cafes(cafes):
    rows, errors = [], []
    for c in cafes:
        url = f"https://apis.naver.com/cafe-web/cafe2/WeeklyPopularArticleList.json?cafeId={c['id']}"
        try:
            res = get_json(url)["message"].get("result") or {}
            arts = res.get("popularArticleList") or []
            if not arts:
                errors.append({"cafe": c["name"], "error": f"인기글 없음(status={res.get('popularStatus')})"})
            for a in arts:
                rows.append({
                    "cafe": c["name"],
                    "slug": c["slug"],
                    "title": a.get("subject", ""),
                    "reads": a.get("readCount", 0),
                    "comments": a.get("commentCount", 0),
                    "likes": a.get("upCount", 0),
                    "written_at": datetime.fromtimestamp(a.get("writeDateTimestamp", 0) / 1000, KST).isoformat()
                    if a.get("writeDateTimestamp") else None,
                    "url": f"https://cafe.naver.com/{c['slug']}/{a.get('articleId')}",
                    "thumb": a.get("representImage"),
                })
        except Exception as e:  # noqa: BLE001
            errors.append({"cafe": c["name"], "error": str(e)[:200]})
        time.sleep(0.5)
    return rows, errors


# ---------------------------------------------------------------- 인스타그램
IG_FIELDS = (
    "business_discovery.username({u}){{username,name,followers_count,media_count,profile_picture_url,"
    "media.limit(12){{caption,like_count,comments_count,timestamp,permalink,media_type,media_product_type,"
    "media_url,thumbnail_url}}}}"
)


def collect_instagram(channels, token, ig_user_id):
    accounts, posts = [], []
    for ch in channels:
        u = ch["handle"]
        q = urllib.parse.urlencode({"fields": IG_FIELDS.format(u=u), "access_token": token})
        url = f"https://graph.facebook.com/{ig_user_id}?{q}"
        try:
            bd = get_json(url)["business_discovery"]
            media = (bd.get("media") or {}).get("data", [])
            likes = [m.get("like_count") for m in media if m.get("like_count") is not None]
            med = statistics.median(likes) if likes else None
            accounts.append({
                "handle": u, "cat": ch["cat"], "note": ch.get("note", ""), "name": bd.get("name") or u,
                "followers": bd.get("followers_count"), "median_likes": med,
                "avatar": bd.get("profile_picture_url"), "error": None,
            })
            for m in media:
                reel = m.get("media_product_type") == "REELS"
                posts.append({
                    "handle": u, "cat": ch["cat"], "caption": (m.get("caption") or "")[:300],
                    "likes": m.get("like_count"), "comments": m.get("comments_count"),
                    "timestamp": m.get("timestamp"), "permalink": m.get("permalink"),
                    "media_type": "REELS" if reel else m.get("media_type"),
                    "thumb": m.get("thumbnail_url") or m.get("media_url"),
                })
        except urllib.error.HTTPError as e:
            msg = e.read().decode("utf-8", "ignore")[:200]
            accounts.append({"handle": u, "cat": ch["cat"], "note": ch.get("note", ""), "name": u,
                             "followers": None, "median_likes": None, "error": f"HTTP {e.code}: {msg}"})
        except Exception as e:  # noqa: BLE001
            accounts.append({"handle": u, "cat": ch["cat"], "note": ch.get("note", ""), "name": u,
                             "followers": None, "median_likes": None, "error": str(e)[:200]})
        time.sleep(1)
    return accounts, posts


def add_ratio(accounts, posts):
    med = {a["handle"]: a.get("median_likes") for a in accounts}
    for p in posts:
        m = med.get(p["handle"])
        p["ratio"] = round(p["likes"] / m, 1) if (m and p.get("likes") is not None) else None


def main():
    channels = load(os.path.join(ROOT, "channels.json"))
    out_path = os.path.join(ROOT, "site", "data.json")
    prev = load(out_path, {}) or {}
    now = datetime.now(KST)

    cafe_rows, cafe_errors = collect_cafes(channels["naver_cafes"])
    if not cafe_rows and prev.get("cafe"):
        cafe_rows = prev["cafe"]  # 수집 실패 시 직전 데이터 유지
        cafe_errors.append({"cafe": "전체", "error": "이번 수집 실패, 직전 데이터 표시 중"})

    token, ig_user_id = os.environ.get("IG_TOKEN"), os.environ.get("IG_USER_ID")
    notes = {c["handle"]: c.get("note", "") for c in channels["instagram"]}
    if token and ig_user_id:
        accounts, posts = collect_instagram(channels["instagram"], token, ig_user_id)
        ig_source, ig_at = "meta-api", now.isoformat()
        if not posts and prev.get("instagram", {}).get("posts"):
            accounts, posts = prev["instagram"]["accounts"], prev["instagram"]["posts"]
            ig_source, ig_at = "previous (API 실패)", prev["instagram"].get("collected_at")
    elif prev.get("instagram", {}).get("posts"):
        ig = prev["instagram"]
        accounts, posts, ig_source, ig_at = ig["accounts"], ig["posts"], ig.get("source", "previous"), ig.get("collected_at")
    else:
        seed = load(os.path.join(ROOT, "scripts", "seed_instagram.json"), {"accounts": [], "posts": []})
        accounts, posts, ig_source, ig_at = seed["accounts"], seed["posts"], "seed (수동 수집)", seed.get("collected_at")
    for a in accounts:
        a["note"] = notes.get(a["handle"], a.get("note", ""))
    add_ratio(accounts, posts)

    data = {
        "updated_at": now.isoformat(),
        "kid_keywords": channels.get("kid_keywords", []),
        "instagram": {"source": ig_source, "collected_at": ig_at, "accounts": accounts, "posts": posts},
        "cafe": cafe_rows,
        "errors": {"cafe": cafe_errors, "instagram": [a for a in accounts if a.get("error")]},
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    print(f"cafe rows={len(cafe_rows)} errors={len(cafe_errors)} | ig source={ig_source} accounts={len(accounts)} posts={len(posts)}")


if __name__ == "__main__":
    main()
