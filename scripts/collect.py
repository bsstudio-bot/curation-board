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


# ---------------------------------------------------------------- 오늘의 추천 (규칙 기반)
import re

AD_RE = re.compile(r"#?광고|협찬|제작지원|공구|공동구매|이벤트|당첨|추첨|댓글[^\n]{0,10}(링크|DM)|링크[^\n]{0,10}(댓글|DM)|최저가|특가|할인코드|구매링크|체험단|서평단")
GOSSIP_RE = re.compile(r"별세|사망|숨[져진]|살인|폭행|성폭|이혼|열애|루머|논란|의혹|구속|체포|흉기|참사|사고|국정조사|대법원장|민주당|국민의힘|대통령|북한|군사|전쟁|미사일|명단 발표|평가전|플레이오프|KBO|A매치|경기 결과")
PLACE_RE = re.compile(r"전시|팝업|축제|공원|박물관|미술관|체험|가볼|나들이|숲|도서관|놀이터|카페|오픈|개관|코스|여행")
PLACE_BONUS_RE = re.compile(r"무료|최초|신규|새로 생|오픈|개관|예약|한정|숨은")
EXPLAIN_RE = re.compile(r"왜|이유|어떻게|비결|탄생|역사|가격|비싼|억|만 원|기업|창업|회장|대표이사|셰프|사장|원래|유래|비교|순위|TOP")
TREND_RE = re.compile(r"밈|유행|챌린지|화제|난리|품절|신상|요즘|트렌드|바이럴")
KID_RE_DEFAULT = "아이랑|아이와|가볼|나들이|놀이|유치원|키즈|체험|전시|공원|주말|축제|박물관|미술관|여행|어린이"

SERIES = {
    "place": ("아이랑 갈 곳", "주말 방문·조건 모음 후보"),
    "explain": ("왜 그럴까", "해설 숏폼 소재"),
    "trend": ("밈·트렌드", "48시간 안에 해설하면 좋은 소재"),
    "parent": ("육아 정보", "아빠 시선 육아 팁 소재"),
}
QUOTA = {"place": 3, "explain": 3, "trend": 2, "parent": 1}


def hours_since(iso, now):
    try:
        return (now - datetime.fromisoformat(iso.replace("Z", "+00:00"))).total_seconds() / 3600
    except Exception:
        return 1e9


def series_of(p):
    cap, cat = p.get("caption") or "", p.get("cat")
    if cat == "아이랑 갈 곳" or (cat == "육아 정보" and PLACE_RE.search(cap)):
        return "place"
    if cat == "육아 정보":
        return "parent"
    if cat == "밈·트렌드":
        return "explain" if EXPLAIN_RE.search(cap) and not TREND_RE.search(cap) else "trend"
    # 해설·인물
    return "trend" if TREND_RE.search(cap) and not EXPLAIN_RE.search(cap) else "explain"


def make_picks(posts, cafe_rows, kid_keywords, now):
    """우리 채널 기준으로 오늘 볼 만한 글을 시리즈별로 몇 개만 고른다."""
    cands = []
    for p in posts:
        cap = p.get("caption") or ""
        if AD_RE.search(cap) or GOSSIP_RE.search(cap):
            continue
        h = hours_since(p.get("timestamp") or "", now)
        if h > 24 * 7:
            continue
        s = series_of(p)
        ratio = p.get("ratio") or 0
        score = min(ratio, 10)
        why = []
        if ratio >= 1.5:
            why.append(f"평소 대비 ×{ratio}")
        if h <= 24:
            score += 2; why.append("24시간 이내")
        elif h <= 72:
            score += 1; why.append("3일 이내")
        if s == "place" and PLACE_BONUS_RE.search(cap):
            m = PLACE_BONUS_RE.search(cap).group(0); score += 1.5; why.append(f"'{m}' 정보")
        if s == "explain" and EXPLAIN_RE.search(cap):
            m = EXPLAIN_RE.search(cap).group(0); score += 1; why.append(f"'{m}' 해설각")
        if s == "trend" and TREND_RE.search(cap):
            m = TREND_RE.search(cap).group(0); score += 1; why.append(f"'{m}' 키워드")
        if ratio < 1 and h > 72:
            continue
        # 해설·트렌드는 우리 포맷에 맞는 키워드가 있어야 후보로 본다
        if s == "explain" and not EXPLAIN_RE.search(cap):
            continue
        if s == "trend" and not TREND_RE.search(cap):
            continue
        cands.append({**p, "series": s, "score": round(score, 2), "why": why})
    cands.sort(key=lambda x: x["score"], reverse=True)
    picks, used = [], set()
    for key, n in QUOTA.items():
        got = 0
        for c in cands:
            if got >= n:
                break
            if c["series"] != key or c["handle"] in used:
                continue
            used.add(c["handle"]); got += 1
            label, use = SERIES[key]
            picks.append({"kind": "instagram", "series": label, "use": use, **{k: c[k] for k in (
                "handle", "caption", "likes", "comments", "timestamp", "permalink", "thumb", "ratio", "media_type", "why", "score")}})
    kid_re = re.compile("|".join(kid_keywords) if kid_keywords else KID_RE_DEFAULT)
    cafe_c = [r for r in cafe_rows if kid_re.search(r["title"]) and not AD_RE.search(r["title"])
              and hours_since(r.get("written_at") or "", now) <= 72]
    cafe_c.sort(key=lambda r: r["reads"] + r["comments"] * 20, reverse=True)
    seen = set()
    for r in cafe_c:
        if len([x for x in picks if x["kind"] == "cafe"]) >= 2:
            break
        if r["cafe"] in seen:
            continue
        seen.add(r["cafe"])
        picks.append({"kind": "cafe", "series": "맘카페 화제", "use": "부모들이 지금 궁금해하는 것", "cafe": r["cafe"],
                      "title": r["title"], "reads": r["reads"], "comments": r["comments"], "url": r["url"],
                      "timestamp": r.get("written_at"), "why": [f"조회 {r['reads']:,}", f"댓글 {r['comments']}"]})
    return picks


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

    picks = make_picks(posts, cafe_rows, channels.get("kid_keywords", []), now)

    data = {
        "updated_at": now.isoformat(),
        "picks": picks,
        "kid_keywords": channels.get("kid_keywords", []),
        "instagram": {"source": ig_source, "collected_at": ig_at, "accounts": accounts, "posts": posts},
        "cafe": cafe_rows,
        "errors": {"cafe": cafe_errors, "instagram": [a for a in accounts if a.get("error")]},
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    print(f"cafe rows={len(cafe_rows)} errors={len(cafe_errors)} | ig source={ig_source} accounts={len(accounts)} posts={len(posts)} | picks={len(picks)}")


if __name__ == "__main__":
    main()
