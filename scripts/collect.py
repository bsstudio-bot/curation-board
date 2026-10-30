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


def collect_instagram(channels, token, ig_user_id, prev_ig=None):
    """계정별로 실패하면(호출 한도·일시 오류) 직전 수집분을 그대로 쓴다."""
    accounts, posts = [], []
    prev_ig = prev_ig or {}
    prev_acc = {a["handle"]: a for a in prev_ig.get("accounts", []) if not a.get("error")}
    prev_posts = {}
    for p in prev_ig.get("posts", []):
        prev_posts.setdefault(p["handle"], []).append(p)
    limited = False

    def fallback(ch, err):
        u = ch["handle"]
        if u in prev_acc:
            a = dict(prev_acc[u]); a["stale"] = True
            accounts.append(a); posts.extend(prev_posts.get(u, []))
        else:
            accounts.append({"handle": u, "cat": ch["cat"], "note": ch.get("note", ""), "name": u,
                             "followers": None, "median_likes": None, "error": err})

    for ch in channels:
        u = ch["handle"]
        if limited:
            fallback(ch, "호출 한도로 이번엔 건너뜀")
            continue
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
            if '"code":4' in msg or '"code":17' in msg or '"code":32' in msg or "request limit" in msg:
                limited = True
                fallback(ch, "호출 한도로 이번엔 건너뜀")
            elif e.code >= 500:
                fallback(ch, f"HTTP {e.code}")
            else:
                accounts.append({"handle": u, "cat": ch["cat"], "note": ch.get("note", ""), "name": u,
                                 "followers": None, "median_likes": None, "error": f"HTTP {e.code}: {msg}"})
        except Exception as e:  # noqa: BLE001
            fallback(ch, str(e)[:200])
        time.sleep(1)
    return accounts, posts


def add_ratio(accounts, posts):
    med = {a["handle"]: a.get("median_likes") for a in accounts}
    for p in posts:
        m = med.get(p["handle"])
        p["ratio"] = round(p["likes"] / m, 1) if (m and p.get("likes") is not None) else None


# ---------------------------------------------------------------- 오늘의 추천 (규칙 기반)
import re

AD_RE = re.compile(r"#?광고|협찬|제작지원|공구|공동구매|이벤트|당첨|추첨|댓글[^\n]{0,10}(링크|DM)|링크[^\n]{0,10}(댓글|DM)|최저가|특가|할인코드|구매링크|체험단|서평단|내돈내산|쿠팡|구매처|스마트스토어|프로필 링크|오픈합니다|단독 혜택|할인")
GOSSIP_RE = re.compile(r"별세|사망|숨[져진]|살인|폭행|성폭|이혼|열애|루머|논란|의혹|구속|체포|흉기|참사|사고|북한|군사|전쟁|미사일|명단 발표|평가전|플레이오프|KBO|A매치|경기 결과")
POLITICS_RE = re.compile(r"정치|정당|국회|의원|대통령|대선|총선|지선|선거|투표|탄핵|여당|야당|민주당|국민의힘|조국혁신당|개혁신당|국정|특검|검찰|대법원|헌법재판|헌재|장관|총리|트럼프|시진핑|푸틴|정상회담|외교부|청와대|대통령실|이재명|윤석열|한동훈")
# 맘카페: 정보 가치가 있는 글만 (질문·후기·추천·행사·교육 등)
CAFE_INFO_RE = re.compile(r"추천|후기|정보|다녀|가볼|어디|오픈|개관|무료|축제|전시|체험|유치원|어린이집|학원|설명회|소아과|접종|키즈|놀이|나들이|방학|입학|교육|도서관|박물관|미술관|여행|숙소|캠핑|선물|비용|원비|\?")
CAFE_TRIVIAL_RE = re.compile(r"촬영|현상황|현 상황|상황 ?입니다|지금 .{0,8}(상황|어때|어떤가)|자리 ?많|사람 ?많|막히|시끄|사이렌|불꽃|무슨 일|날씨|점심|저녁|아침|폐업|폐원|화재|정전|주차장|ㅋㅋ|ㅎㅎ|ㅠㅠ|ㅜㅜ|가입인사|안녕하세|출석")
IG_TRIVIAL_RE = re.compile(r"근황|현상황|현 상황|실시간|지금 .{0,6}(상황|모습)|촬영 중|촬영중")
PICK_MAX_HOURS = 72      # 이보다 오래된 글은 추천하지 않는다
PICK_MIN_LIKES = 100     # 인스타 좋아요 100 미만은 추천하지 않는다
PICK_MIN_READS = 100     # 맘카페 조회 100 미만은 추천하지 않는다
PICK_MIN_CAPTION = 30    # 너무 짧은 캡션(정보 없음)은 제외


def flag_of(text):
    """추천·반응 탭에서 빼야 할 글이면 사유를 돌려준다."""
    if AD_RE.search(text):
        return "광고·공구"
    if POLITICS_RE.search(text):
        return "정치"
    if GOSSIP_RE.search(text):
        return "가십·사건·스포츠"
    return None
PLACE_RE = re.compile(r"전시|팝업|축제|공원|박물관|미술관|체험|가볼|나들이|숲|도서관|놀이터|카페|오픈|개관|코스|여행")
PLACE_BONUS_RE = re.compile(r"무료|최초|신규|새로 생|오픈|개관|예약|한정|숨은")
EXPLAIN_RE = re.compile(r"왜|이유|어떻게|비결|탄생|역사|가격|비싼|억|만 원|기업|창업|회장|대표이사|셰프|사장|원래|유래|비교|순위|TOP")
TREND_RE = re.compile(r"밈|유행|챌린지|화제|난리|품절|신상|요즘|트렌드|바이럴")
# 맘카페: 지역 한정 글 (특정 지역·특정 기관 이름) 은 추천에서 뺀다
LOCAL_RE = re.compile(r"운정|청라|동탄|분당|판교|수원|영통|광교|수지|기흥|용인|대전|세종|부산|부경|송도|대구|김해|양산|파주|일산|고양|평택|화성|인천|구미|창원|울산|광주|천안|청주|우리 ?동네|근처|이 동네|[가-힣]{1,4}동 |[가-힣]{1,4}역 ")
_INST_RE = re.compile(r"([가-힣A-Za-z]{2,})(유치원|어린이집|학원|소아과|병원|의원|치과|마트|센터)")
_GENERIC = set("영어 사립 공립 국공립 병설 단설 놀이 숲 발도르프 몬테소리 동네 근처 일반 대형 소형 가정 직장 민간 국립 시립 구립 유아 어린이 종합 대학 소아 동물 피부 수학 미술 태권도 피아노 수영 발레 축구 줄넘기 보습 영재 코딩 논술 한글 키즈 문화 체험 과학 아동 발달 언어 심리 상담 주민 보건 대형마트 동네마트".split())


class _LocalInst:
    def search(self, t):
        for m in _INST_RE.finditer(t or ""):
            if m.group(1) not in _GENERIC:
                return m
        return None


LOCAL_INST_RE = _LocalInst()
CAFE_AD_RE = re.compile(r"OPEN|오픈|모집|마감|선착순|공구|\d{1,3},\d{3}원|원~|할인|체험단|서평단|이벤트|\[[^\]]*(공구|핫딜|광고|홍보)[^\]]*\]")
JAMO_RE = re.compile(r"[ㄱ-ㅎ]{3,}")


def cafe_flag(title):
    f = flag_of(title)
    if f:
        return f
    if CAFE_AD_RE.search(title):
        return "광고·공구"
    if CAFE_TRIVIAL_RE.search(title):
        return "단순 현황"
    if LOCAL_RE.search(title) or LOCAL_INST_RE.search(title) or JAMO_RE.search(title):
        return "지역 한정"
    return None


# 엄마들 관심사 주제 사전
TOPICS = {
    "유치원·어린이집": r"유치원|어린이집|원비|설명회|입학|추첨|처음학교|교사|담임|등원|하원",
    "아이 건강": r"독감|감기|열이|고열|열나|해열|기침|콧물|접종|예방주사|수족구|장염|폐렴|소아과|병원|비염|아토피|중이염|마이코",
    "학습·교육": r"학습지|영어|수학|한글|학원|공부|교재|문제집|숙제|엄마표|책육아|독서|사고력",
    "놀이·나들이": r"아이(랑|와)|애들(이랑|과|하고)|아기랑|가볼|나들이|체험|키즈카페|어린이 ?(전시|공연|박물관)|가족 ?여행|아이 ?데리고|숙소 추천|캠핑",
    "육아 고민": r"훈육|떼쓰|떼를|짜증|수면교육|낮잠|밤잠|편식|형제|자매|사회성|발달|말이 늦|언어발달|분리불안|스마트폰|영상 ?노출|미디어|사춘기|아이가 .{0,12}(안 |못 |싫어)",
    "선물·쇼핑": r"선물|크리스마스|장난감|아이 ?옷|패딩|겨울옷|아이 ?신발|책상|카시트|유모차|킥보드|레고",
    "돈·지원금": r"지원금|수당|바우처|보험|적금|청약|대출|세금|연말정산|아동수당|부모급여",
    "먹거리": r"간식|반찬|식단|레시피|유아식|이유식|밥|급식|과일",
}
TOPIC_RES = {k: re.compile(v) for k, v in TOPICS.items()}
# 해설 각도 (캉가에처럼 '쉽게 설명'하는 숏폼으로 만들 수 있는 질문)
ANGLES = [
    (re.compile(r"창업|대표이사|회장|셰프|사장|CEO|임원|자퇴"), "이 사람은 어떻게 여기까지 왔을까"),
    (re.compile(r"탄생|처음|최초|역사|원래|유래"), "처음엔 어떻게 시작됐을까"),
    (re.compile(r"비교|순위|TOP|1위|가장 [가-힣]+ 곳"), "숫자로 줄 세우면 뭐가 보일까"),
    (re.compile(r"밈|유행|챌린지|난리|품절|바이럴"), "왜 하필 지금 이게 유행할까"),
    (re.compile(r"무료"), "왜 무료로 열까? 비용은 누가 낼까"),
    (re.compile(r"오픈|개관|새로 생|신규|팝업"), "이 공간은 누가, 왜 만들었을까"),
    (re.compile(r"가격|비싼|억 ?원|만 ?원|원짜리"), "이 가격은 어떻게 정해질까"),
    (re.compile(r"연구|발달|전문의|의사|과학"), "연구로 보면 실제로 어떨까"),
    (re.compile(r"면세|제도|법이|바뀐|개정|지원"), "뭐가 바뀌고 우리 집엔 어떤 영향일까"),
    (re.compile(r"왜|이유|어떻게"), "질문 그대로 풀어 설명하기"),
]


def angle_of(text):
    for rx, a in ANGLES:
        if rx.search(text or ""):
            return a
    return None


# 행사 종료일 찾기: "10.11~10.25", "~11/23", "11월 30일까지" 등
_RANGE_RE = re.compile(r"(?:(20\d\d)[.\-/년]\s?)?(\d{1,2})[.\-/월]\s?(\d{1,2})일?\s*(?:\([^)]{1,3}\))?\s*[~\-–]\s*(?:(20\d\d)[.\-/년]\s?)?(\d{1,2})[.\-/월]\s?(\d{1,2})")
_UNTIL_RE = re.compile(r"(?:~\s*|까지\s*)?(\d{1,2})[./월]\s?(\d{1,2})일?\s*(?:\([^)]{1,3}\))?\s*까지|~\s*(\d{1,2})[./](\d{1,2})")


def event_end(caption, posted_iso):
    try:
        posted = datetime.fromisoformat((posted_iso or "").replace("Z", "+00:00")).astimezone(KST)
    except Exception:
        return None
    cap = caption or ""
    ends = []
    for m in _RANGE_RE.finditer(cap):
        y = int(m.group(4) or m.group(1) or posted.year)
        mo, d = int(m.group(5)), int(m.group(6))
        if 1 <= mo <= 12 and 1 <= d <= 31:
            ends.append((y, mo, d))
    if not ends:
        for m in _UNTIL_RE.finditer(cap):
            mo, d = (int(m.group(1)), int(m.group(2))) if m.group(1) else (int(m.group(3)), int(m.group(4)))
            if 1 <= mo <= 12 and 1 <= d <= 31:
                ends.append((posted.year, mo, d))
    if not ends:
        return None
    y, mo, d = max(ends)
    if mo < posted.month - 6:  # 12월 글의 '~1/15' 같은 경우
        y += 1
    try:
        return datetime(y, mo, d, 23, 59, tzinfo=KST)
    except ValueError:
        return None


def topics_of(rows, now):
    """이번 주 맘카페 인기글에서 엄마들 관심사 주제를 집계한다."""
    res = []
    for k, rx in TOPIC_RES.items():
        hit = [r for r in rows if rx.search(r["title"]) and not r.get("flag") and len(r["title"]) >= 8]
        if not hit:
            continue
        hit.sort(key=lambda r: r["reads"] + r["comments"] * 20, reverse=True)
        res.append({"topic": k, "count": len(hit), "reads": sum(r["reads"] for r in hit),
                    "comments": sum(r["comments"] for r in hit),
                    "samples": [{"title": r["title"], "cafe": r["cafe"], "url": r["url"], "reads": r["reads"],
                                 "comments": r["comments"]} for r in hit[:5]]})
    res.sort(key=lambda x: x["reads"] + x["comments"] * 20, reverse=True)
    return res


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
        if p.get("flag") or len(cap.strip()) < PICK_MIN_CAPTION or IG_TRIVIAL_RE.search(cap[:80]):
            continue
        if (p.get("likes") or 0) < PICK_MIN_LIKES:
            continue
        h = hours_since(p.get("timestamp") or "", now)
        if h > PICK_MAX_HOURS:
            continue
        if p.get("ended"):
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
        if ratio < 1:
            continue
        # 해설·트렌드는 우리 포맷에 맞는 키워드가 있어야 후보로 본다
        if s == "explain" and not EXPLAIN_RE.search(cap):
            continue
        if s == "trend" and not TREND_RE.search(cap):
            continue
        ang = angle_of(cap)
        if ang:
            score += 0.5
        if p.get("event_end"):
            why.append("~" + p["event_end"][5:10].replace("-", "/") + "까지")
        cands.append({**p, "series": s, "score": round(score, 2), "why": why, "angle": ang})
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
                "handle", "caption", "likes", "comments", "timestamp", "permalink", "thumb", "ratio", "media_type", "why", "score", "angle")}})
    kid_re = re.compile("|".join(kid_keywords) if kid_keywords else KID_RE_DEFAULT)
    cafe_c = [r for r in cafe_rows if kid_re.search(r["title"]) and not r.get("flag")
              and r["reads"] >= PICK_MIN_READS and len(r["title"]) >= 8
              and CAFE_INFO_RE.search(r["title"]) and not CAFE_TRIVIAL_RE.search(r["title"])
              and not LOCAL_RE.search(r["title"]) and not LOCAL_INST_RE.search(r["title"])
              and hours_since(r.get("written_at") or "", now) <= PICK_MAX_HOURS]
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
                      "timestamp": r.get("written_at"), "why": [f"조회 {r['reads']:,}", f"댓글 {r['comments']}"],
                      "angle": angle_of(r["title"]) or "엄마들이 왜 이걸 궁금해할까"})
    return picks


API_URL = "https://script.google.com/macros/s/AKfycbx_bxTi39uwrssjbst255QjPBY_p1KNQwRA8KAgzj_h9DNHjcw3BUpfNZntsy8ItxEw/exec"


def load_channels():
    base = load(os.path.join(ROOT, "channels.json"))
    try:
        req = urllib.request.Request(API_URL + "?action=channels", headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=30) as r:
            shared = json.loads(r.read().decode("utf-8"))
        if shared.get("ok") and shared.get("channels"):
            base["instagram"] = [{"handle": c["handle"], "cat": c["cat"], "note": c.get("note", ""),
                                  "added_by": c.get("added_by", "")} for c in shared["channels"]]
            base["_source"] = "shared"
    except Exception as e:  # noqa: BLE001
        base["_source"] = f"channels.json (공유 저장소 실패: {str(e)[:80]})"
    return base


def main():
    channels = load_channels()
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
        accounts, posts = collect_instagram(channels["instagram"], token, ig_user_id, prev.get("instagram"))
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
    # 공유 목록에서 지운 채널은 화면에서도 뺀다
    active = {c["handle"] for c in channels["instagram"]}
    accounts = [a for a in accounts if a["handle"] in active]
    posts = [p for p in posts if p["handle"] in active]
    add_ratio(accounts, posts)
    for p in posts:
        p["flag"] = flag_of(p.get("caption") or "")
        end = event_end(p.get("caption"), p.get("timestamp"))
        p["event_end"] = end.isoformat() if end else None
        p["ended"] = bool(end and end < now)
    for r in cafe_rows:
        r["flag"] = cafe_flag(r["title"])

    picks = make_picks(posts, cafe_rows, channels.get("kid_keywords", []), now)
    topics = topics_of(cafe_rows, now)
    hist_path = os.path.join(ROOT, "site", "topics_history.json")
    hist = load(hist_path, {}) or {}
    hist[now.strftime("%Y-%m-%d")] = {t["topic"]: t["count"] for t in topics}
    hist = dict(sorted(hist.items())[-60:])
    with open(hist_path, "w", encoding="utf-8") as f:
        json.dump(hist, f, ensure_ascii=False)
    week_ago = (now - timedelta(days=7)).strftime("%Y-%m-%d")
    prev_day = max([d for d in hist if d <= week_ago], default=None)
    for t in topics:
        t["prev_count"] = hist[prev_day].get(t["topic"]) if prev_day else None

    data = {
        "updated_at": now.isoformat(),
        "picks": picks,
        "topics": topics,
        "channels_source": channels.get("_source", "channels.json"),
        "kid_keywords": channels.get("kid_keywords", []),
        "rules": {"max_hours": PICK_MAX_HOURS, "min_likes": PICK_MIN_LIKES, "min_reads": PICK_MIN_READS},
        "instagram": {"source": ig_source, "collected_at": ig_at, "accounts": accounts, "posts": posts},
        "cafe": cafe_rows,
        "errors": {"cafe": cafe_errors, "instagram": [a for a in accounts if a.get("error")]},
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    print(f"cafe rows={len(cafe_rows)} errors={len(cafe_errors)} | ig source={ig_source} accounts={len(accounts)} posts={len(posts)} | picks={len(picks)}")


if __name__ == "__main__":
    main()
