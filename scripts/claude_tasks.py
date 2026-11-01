"""큐레이션 보드 Claude 작업 (GitHub Actions에서 실행, 맥 불필요)

A. 스크립트 요청 대기열 처리
B. 오늘의 추천 중 스크립트 없는 글 → 스크립트 작성
C. 광고·공구 글 상품 정보 정리 (브랜드·제품·가격·컨택)
D. '[검증 중]' 채널 판정 (규칙 기반, Claude 미사용)

할 일이 없으면 Claude를 부르지 않고 바로 끝난다.
Claude 호출은 Claude Code CLI(`claude -p`) + CLAUDE_CODE_OAUTH_TOKEN(구독) 사용.
"""
import json, os, re, subprocess, sys, time, statistics, urllib.request
from datetime import datetime, timezone, timedelta

API = "https://script.google.com/macros/s/AKfycbx_bxTi39uwrssjbst255QjPBY_p1KNQwRA8KAgzj_h9DNHjcw3BUpfNZntsy8ItxEw/exec"
PASS = os.environ.get("BOARD_PASS", "bs2026")
KST = timezone(timedelta(hours=9))
NOW = datetime.now(KST)
MODEL = os.environ.get("CLAUDE_MODEL", "sonnet")
MAX_PICKS, MAX_ADS, ADS_BATCH, SCRIPT_BATCH = 12, 40, 20, 4
ONLY = set(filter(None, os.environ.get("ONLY", "").split(",")))  # 예: ONLY=A  (요청만)


def get(action):
    with urllib.request.urlopen(f"{API}?action={action}", timeout=60) as r:
        return json.loads(r.read().decode())


def post(body):
    body = dict(body, **{"pass": PASS})
    req = urllib.request.Request(API, data=json.dumps(body, ensure_ascii=False).encode(), headers={"Content-Type": "text/plain"})
    for i in range(3):
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                j = json.loads(r.read().decode())
                if j.get("ok"):
                    return j
                print("  POST not ok:", str(j)[:200])
        except Exception as e:
            print("  POST error:", e)
        time.sleep(3)
    return None


def claude(prompt, tools=None, turns=1):
    """Claude Code CLI 호출, 응답 텍스트 반환. tools 예: ["WebSearch", "WebFetch"]"""
    cmd = ["claude", "-p", "--model", MODEL, "--output-format", "json", "--max-turns", str(turns)]
    if tools:
        cmd += ["--allowedTools", ",".join(tools)]
    for attempt in range(2):
        try:
            p = subprocess.run(cmd,
                               input=prompt, capture_output=True, text=True, timeout=900)
            out = json.loads(p.stdout or "{}")
            if out.get("is_error") or p.returncode != 0:
                print("  claude error:", (out.get("result") or p.stderr or "")[:300])
                time.sleep(10); continue
            return out.get("result", "")
        except Exception as e:
            print("  claude exception:", e); time.sleep(10)
    return None


def parse_json(text, want=list):
    if not text:
        return None
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    s, e = (text.find("["), text.rfind("]")) if want is list else (text.find("{"), text.rfind("}"))
    try:
        return json.loads(text[s:e + 1])
    except Exception:
        return None


def item_text(it):
    series = it.get("series") or {"해설·인물": "왜 그럴까"}.get(it.get("cat"), it.get("cat")) or ("맘카페 화제" if it.get("kind") == "cafe" else "왜 그럴까")
    src = (f"{it.get('cafe')} 맘카페 인기글 (조회 {it.get('reads')}, 댓글 {it.get('comments')})" if it.get("kind") == "cafe"
           else f"구글 급상승 검색어 (검색 {it.get('traffic')})" if it.get("kind") == "trend" else f"@{it.get('handle')}")
    t = [f"시리즈: {series}", f"해설 각도 힌트: {it.get('angle') or '없음'}", f"원문 출처: {src}",
         f"게시일: {str(it.get('timestamp') or it.get('written_at') or '')[:10]}", f"오늘 날짜: {NOW:%Y-%m-%d}"]
    dc = it.get("date_check") or {}
    if dc.get("status") in ("확인됨", "원문과 다름"):
        t.append(f"웹 검색으로 확인한 일정({dc['status']}): {dc.get('event')} · {dc.get('period')} {dc.get('time')} · {dc.get('place')} · {dc.get('fee')} · 출처 {dc.get('source')} · {dc.get('note')}  ← 원문과 다르면 이 일정을 쓰고 checks에 '원문 일정과 다름'을 남긴다")
    elif dc.get("status") == "확인 못함":
        t.append("일정 웹 확인 실패: 날짜를 단정하지 말고 checks에 '일정 확인'을 넣는다")
    if it.get("gomong_angle"):
        t.append(f"고몽다운 풀이 힌트: {it['gomong_angle']}")
    if it.get("event_end"):
        t.append(f"행사 종료일(자동 추출): {str(it['event_end'])[:10]}")
    t.append("원문:\n" + str(it.get("caption") or it.get("title") or "")[:2500])
    if it.get("extra"):
        t.append("추가 요청: " + str(it["extra"]))
    return "\n".join(t)


def write_scripts(jobs, guide, label):
    """jobs: [(id, item)] → 묶어서 작성·저장. 저장 개수 반환."""
    saved = 0
    for i in range(0, len(jobs), SCRIPT_BATCH):
        chunk = jobs[i:i + SCRIPT_BATCH]
        body = "\n\n".join(f"### 글 {k + 1}\n{item_text(it)}" for k, (_, it) in enumerate(chunk))
        prompt = (guide + "\n\n아래 글 " + str(len(chunk)) + "개 각각에 대해 위 형식의 대본 JSON을 하나씩 써라. "
                  "lines[0].say는 hooks[0]과 같게. 출력은 JSON 배열만(글 순서대로, 설명·코드블록 없이).\n\n" + body)
        arr = parse_json(claude(prompt), list)
        if not isinstance(arr, list) or len(arr) != len(chunk):
            print(f"  [{label}] 파싱 실패, 건너뜀 ({len(chunk)}개)"); continue
        for (id_, it), sc in zip(chunk, arr):
            if isinstance(sc, dict) and sc.get("lines") and post({"action": "saveScript", "id": id_, "script": sc, "by": "Claude", "model": "claude-" + MODEL, "item": it}):
                saved += 1
    return saved


def pick_item(p):
    keys = ["kind", "series", "handle", "cafe", "caption", "title", "permalink", "url", "timestamp", "written_at", "angle", "gomong_angle", "event_end", "date_check", "reads", "comments", "cat", "traffic"]
    return {k: p.get(k) for k in keys if p.get(k) is not None}


def hours_ago(ts):
    try:
        return (NOW - datetime.fromisoformat(str(ts).replace("Z", "+00:00"))).total_seconds() / 3600
    except Exception:
        return 1e9


# ---------------------------------------------------------------- E. 오늘의 큐레이션 (이슈 묶기 + 적합도)
CURATE_PROMPT = """너는 유튜버 고몽(김웅현)의 **신규 채널** 숏폼 기획 PD다.
고몽: 영화·드라마 해설 10년차 '스토리 중독자', 3살·6살 두 딸 아빠. 관심사: 그림 컬렉팅, AI, 위스키, 맛집, 투자, 공간.
신규 채널은 일상·화제·트렌드·육아 정보를 쉽게 풀어주는 숏폼 채널이다(레퍼런스: @kangae_eco처럼 옆자리 사람에게 얘기하듯 해설).
시리즈: 아이랑 갈 곳 / 왜 그럴까(시사·경제·소비 해설) / 밈·트렌드 / 화제 / 육아 정보 / 맘카페 화제

아래 후보(인스타 글, 맘카페 인기글, 구글 급상승 검색어)를 보고 두 가지를 해라.

1) issues: 지금 뜨는 이슈 3~6개. 여러 후보가 같은 주제를 다루거나(채널 2곳 이상·맘카페·검색어가 겹침), 검색량이 큰 화제를 묶는다.
   각 이슈: title(15자 이내), summary(무슨 일인지 1~2문장, 후보 내용만 근거), ids(관련 후보 id 목록), why_now(왜 지금 다룰 만한지 1문장), fit(1~10, 우리 채널 적합도)
2) picks: 오늘 찍을 후보 10~12개. 시리즈 배분: 아이랑 갈 곳 2~3, 왜 그럴까 2~3, 밈·트렌드 2, 화제 1~2, 육아 정보 1~2, 맘카페 화제 1~2. 같은 계정은 1개까지.
   각 pick: id(후보 id 그대로), series, score(1~10), reason(왜 고몽 채널에 맞는지 1문장, 구체적으로), angle(시청자가 궁금해할 질문 하나), gomong_angle(고몽다운 풀이 한 줄: 이야기꾼·영화/드라마 비유, 딸 둘 아빠 시선, 자기 고백형 유머 중 자연스러운 것. 2년 넘은 옛날 밈은 쓰지 않는다. 억지면 빈 문자열. 고몽이 실제로 겪은 일(병원·펫로스·육아 에피소드 등)을 사실처럼 지어내지 말고, 필요하면 '고몽 본인 경험이 있다면 ~로'처럼 조건으로 쓴다)

[점수 기준] 시의성(지금 이야기되는가), 반응(평소 대비 배수 lift가 높을수록, 여러 곳에서 동시에 언급될수록), 해설할 거리(왜·어떻게를 풀 수 있는가), 3·6살 아이 키우는 집과의 연결, 고몽 캐릭터와의 궁합.
[화제·가십 원칙 — 렉카 금지] 공식 발표, 작품·방송 화제, 본인이 공개한 결혼·출산·육아 소식, 훈훈한 장면, 사회적으로 이야기할 거리는 OK.
사생활 폭로, 루머·열애설·이혼설, 사건·사고 피해자, 논란 몰이, 특정인 비난, 사망·범죄, 정치, 해외 스포츠 경기 결과는 고르지 않는다. 화제 픽은 사람보다 현상을 이야기할 수 있어야 한다.
[제외] 광고·공구, 지역 한정 정보, 이미 끝난 행사, 내용 없는 근황.

출력은 JSON 객체만: {"issues":[...], "picks":[...]}
"""


def build_pool(data):
    pool = {}
    posts = [p for p in data["instagram"]["posts"] if not p.get("flag") and not p.get("ad") and not p.get("ended")
             and (p.get("likes") or 0) >= 100 and len((p.get("caption") or "").strip()) >= 30 and hours_ago(p.get("timestamp")) <= 72]
    posts.sort(key=lambda p: ((p.get("ratio") or 0) * 2 + max(0, 3 - hours_ago(p.get("timestamp")) / 24)), reverse=True)
    for p in posts[:55]:
        pool[p["permalink"]] = {"kind": "instagram", **{k: p.get(k) for k in ("handle", "cat", "caption", "likes", "comments", "ratio", "timestamp", "permalink", "thumb", "media_type", "event_end")}}
    cafes = [r for r in data.get("cafe", []) if not r.get("flag") and (r.get("reads") or 0) >= 100 and hours_ago(r.get("written_at")) <= 72]
    cafes.sort(key=lambda r: (r.get("reads") or 0) + (r.get("comments") or 0) * 20, reverse=True)
    for r in cafes[:20]:
        pool[r["url"]] = {"kind": "cafe", **{k: r.get(k) for k in ("cafe", "title", "reads", "comments", "written_at", "url")}, "timestamp": r.get("written_at")}
    for t in ((data.get("trends") or {}).get("items") or [])[:25]:
        if hours_ago(t.get("at")) > 48:
            continue
        tid = "trend:" + t["title"]
        cap = f"구글 급상승 검색어 '{t['title']}' (검색 {t.get('traffic')})\n관련 기사: " + " / ".join(f"{n['title']} ({n['source']})" for n in t.get("news", []))
        pool[tid] = {"kind": "trend", "title": t["title"], "traffic": t.get("traffic"), "caption": cap, "news": t.get("news", []),
                     "picture": t.get("picture"), "timestamp": t.get("at"), "url": (t.get("news") or [{}])[0].get("url")}
    return pool


def pool_text(pool):
    out = []
    for i, (pid, x) in enumerate(pool.items()):
        if x["kind"] == "instagram":
            out.append(f"[id] {pid}\n인스타 @{x['handle']} ({x['cat']}) · 좋아요 {x['likes']} · 평소 대비 ×{x.get('ratio')} · {str(x['timestamp'])[:10]}\n{(x['caption'] or '')[:350]}")
        elif x["kind"] == "cafe":
            out.append(f"[id] {pid}\n맘카페 {x['cafe']} · 조회 {x['reads']} · 댓글 {x['comments']} · {str(x['timestamp'])[:10]}\n{x['title']}")
        else:
            out.append(f"[id] {pid}\n{x['caption']}")
    return "\n\n".join(out)


def curate(data):
    pool = build_pool(data)
    if not pool:
        return None
    res = parse_json(claude(CURATE_PROMPT + "\n\n오늘 날짜: " + NOW.strftime("%Y-%m-%d") + "\n\n[후보]\n" + pool_text(pool)), dict)
    if not isinstance(res, dict) or not res.get("picks"):
        print("  [E] 파싱 실패"); return None
    picks = []
    for pk in res["picks"]:
        x = pool.get(pk.get("id"))
        if not x:
            continue
        picks.append({**x, "id": pk["id"], "series": pk.get("series"), "score": pk.get("score"), "reason": pk.get("reason"),
                      "angle": pk.get("angle"), "gomong_angle": pk.get("gomong_angle", "")})
    issues = []
    for it in res.get("issues", []):
        ids = [i for i in it.get("ids", []) if i in pool]
        issues.append({**{k: it.get(k) for k in ("title", "summary", "why_now", "fit")}, "items": [{"id": i, **pool[i]} for i in ids]})
    return {"date": NOW.strftime("%Y-%m-%d"), "generated_at": NOW.isoformat(), "source_updated_at": data.get("updated_at"),
            "model": "claude-" + MODEL, "pool_size": len(pool), "issues": issues, "picks": picks}


# ---------------------------------------------------------------- 일정 확인 (웹 검색, Claude Code 내장 검색 · 추가 API 불필요)
DATE_RE = re.compile(r"\d{1,2}\s?[./월]\s?\d{1,2}|기간|까지|오픈|개막|축제|전시|팝업|행사|페스티벌|박람회|공연")


def verify_dates(items):
    """items: [{id, name, text}] → {id: {status, period, time, place, source, note}}"""
    if not items:
        return {}
    body = "\n\n".join(f"### {i + 1}\nid: {x['id']}\n{x['text'][:900]}" for i, x in enumerate(items))
    prompt = f"""오늘은 {NOW:%Y-%m-%d}이다. 아래 글들에 나오는 행사·전시·축제·팝업의 실제 일정을 웹 검색으로 확인해라.
공식 홈페이지, 주최 기관, 언론 보도를 우선한다. 글마다 행사가 여러 개면 대표 행사 1개만.
각 글에 대해 JSON 객체: id(그대로), event(행사 이름), period(확인된 기간, 예 '2026-11-18~2026-11-20'), time(운영 시간, 모르면 ""),
place(장소), fee(요금·무료 여부, 모르면 ""), status('확인됨' | '원문과 다름' | '확인 못함'), source(근거 URL 1개), note(원문과 다른 점이나 주의점 한 줄, 없으면 "").
검색으로 확인되지 않으면 추측하지 말고 status를 '확인 못함'으로.
출력은 JSON 배열만(설명·코드블록 없이).

{body}"""
    arr = parse_json(claude(prompt, tools=["WebSearch", "WebFetch"], turns=25), list)
    if not isinstance(arr, list):
        print("  [일정] 파싱 실패"); return {}
    return {a["id"]: {k: a.get(k, "") for k in ("event", "period", "time", "place", "fee", "status", "source", "note")} for a in arr if isinstance(a, dict) and a.get("id")}


def date_targets(ai):
    out, seen = [], set()
    for x in ai.get("picks", []) + [it for i in ai.get("issues", []) for it in i.get("items", [])]:
        xid = x.get("id") or x.get("permalink") or x.get("url")
        text = x.get("caption") or x.get("title") or ""
        if xid in seen or x.get("kind") == "trend":
            continue
        if x.get("series") == "아이랑 갈 곳" or x.get("cat") == "아이랑 갈 곳" or (DATE_RE.search(text) and re.search(r"축제|전시|팝업|행사|페스티벌|박람회|공연|체험|개막|오픈", text)):
            seen.add(xid); out.append({"id": xid, "text": f"출처: {x.get('handle') or x.get('cafe') or ''}\n{text}"})
    return out[:10]


CATS = "먹거리·간식/교구·도서/장난감·놀이/패션·잡화/생활·주방/여행·숙소·체험/뷰티·건강/가전·디지털/기타"
ADS_PROMPT = f"""아래는 육아·키즈 인스타 계정의 광고·공구·이벤트 글이다. 각 글의 캡션만 근거로 상품 정보를 정리해라.
캡션에 없는 내용은 빈 문자열로 둔다(추측 금지).
필드: id(그대로), handle(그대로), type('공구'|'광고·협찬'|'이벤트'), brand(업체·브랜드 이름, @계정이 있으면 괄호로 같이. 예: '베베쿡 (@bebecook_official)'),
product(대표 제품 1개, 여러 개면 '대표 제품 외 N종'), category({CATS} 중 하나), price(대표 판매가·공구가, 원 단위 정수. 없으면 ""),
discount(할인율 숫자만), period(예 '11/3~11/7'), channel(판매처 단서: 스마트스토어·자사몰·카톡 채널·프로필 링크·댓글 DM 등), contact(캡션에 적힌 문의처: 이메일·카톡ID·전화·'DM 문의' 등).
출력은 JSON 배열만(글 순서대로, 설명·코드블록 없이).
"""

AD_RE = re.compile(r"광고|협찬|제작지원|공구|공동구매|이벤트|당첨|댓글.{0,10}(링크|DM)|최저가|특가|내돈내산|쿠팡|할인")


def main():
    data = json.load(open("site/data.json", encoding="utf-8"))
    allj = get("all")
    queue = get("queue").get("queue", []) if (not ONLY or "A" in ONLY) else []
    have_scripts = {s["id"] for s in allj.get("scripts", [])}
    report = {}

    # E: 오늘의 큐레이션 (하루 1번, 그날 수집 데이터가 들어온 뒤)
    ai_path = "site/ai.json"
    ai = json.load(open(ai_path, encoding="utf-8")) if os.path.exists(ai_path) else {}
    data_today = str(data.get("updated_at", ""))[:10] == NOW.strftime("%Y-%m-%d")
    need_e = ("E" in ONLY) or (not ONLY and ai.get("date") != NOW.strftime("%Y-%m-%d") and (data_today or NOW.hour >= 12))
    if "F" in ONLY and ai.get("picks"):  # 기존 큐레이션에 일정 확인만 추가
        checks = verify_dates(date_targets(ai))
        for x in ai["picks"] + [it for i in ai.get("issues", []) for it in i.get("items", [])]:
            if x.get("id") in checks:
                x["date_check"] = checks[x["id"]]
        ai["date_checked"] = len(checks)
        json.dump(ai, open(ai_path, "w", encoding="utf-8"), ensure_ascii=False)
        open(".ai_updated", "w").write("1")
        report["일정 확인"] = len(checks)
    if need_e:
        new = curate(data)
        if new:
            checks = verify_dates(date_targets(new))
            for x in new["picks"] + [it for i in new["issues"] for it in i["items"]]:
                if x.get("id") in checks:
                    x["date_check"] = checks[x["id"]]
            new["date_checked"] = len(checks)
            ai = new
            json.dump(ai, open(ai_path, "w", encoding="utf-8"), ensure_ascii=False)
            open(".ai_updated", "w").write("1")
            report["큐레이션"] = f"이슈 {len(ai['issues'])} · 추천 {len(ai['picks'])} (후보 {ai['pool_size']}) · 일정 확인 {ai.get('date_checked', 0)}"

    # A + B: 스크립트
    jobsA = [(q["id"], q.get("item") or {}) for q in queue]
    jobsB = []
    if not ONLY or "B" in ONLY:
        qids = {i for i, _ in jobsA}
        src_picks = ai.get("picks") if ai.get("date") == NOW.strftime("%Y-%m-%d") else (data.get("picks") or [])
        for p in (src_picks or [])[:MAX_PICKS]:
            pid = p.get("id") or p.get("permalink") or p.get("url")
            if pid and pid not in have_scripts and pid not in qids:
                jobsB.append((pid, pick_item(p)))

    # C: 광고 정리
    jobsC = []
    if not ONLY or "C" in ONLY:
        have_ads = {a["id"] for a in get("ads").get("ads", [])}
        posts = [p for p in data["instagram"]["posts"] if p.get("ad") and p.get("permalink") not in have_ads and hours_ago(p.get("timestamp")) <= 30 * 24]
        posts.sort(key=lambda p: p.get("timestamp") or "", reverse=True)
        jobsC = posts[:MAX_ADS]

    # D: 채널 검증 대상
    pending = [c for c in allj.get("channels", []) if "[검증 중]" in str(c.get("note", ""))] if (not ONLY or "D" in ONLY) else []

    print(f"할 일: 요청 {len(jobsA)} · 추천 {len(jobsB)} · 광고 {len(jobsC)} · 검증 {len(pending)}")
    if not (jobsA or jobsB or jobsC or pending):
        print("할 일 없음", json.dumps(report, ensure_ascii=False)); return

    guide = open("prompts/script_guide.md", encoding="utf-8").read() if os.path.exists("prompts/script_guide.md") else (get("guide").get("guide", "") if (jobsA or jobsB) else "")
    if jobsA:
        report["요청"] = write_scripts(jobsA, guide, "A")
    if jobsB:
        report["추천"] = write_scripts(jobsB, guide, "B")

    if jobsC:
        n = 0
        for i in range(0, len(jobsC), ADS_BATCH):
            chunk = jobsC[i:i + ADS_BATCH]
            body = "\n\n".join(f"### 글 {k + 1}\nid: {p['permalink']}\nhandle: {p['handle']}\n자동 분류: {(p.get('ad') or {}).get('type', '')}\n캡션:\n{(p.get('caption') or '')[:1500]}" for k, p in enumerate(chunk))
            arr = parse_json(claude(ADS_PROMPT + "\n" + body), list)
            if not isinstance(arr, list):
                print("  [C] 파싱 실패"); continue
            ok = [a for a in arr if isinstance(a, dict) and a.get("id")]
            if ok and post({"action": "saveAds", "by": "Claude", "ads": ok}):
                n += len(ok)
        report["광고"] = n

    if pending:
        acc = {a["handle"].lower(): a for a in data["instagram"]["accounts"]}
        keep, drop, wait = [], [], []
        for c in pending:
            h = c["handle"].lower(); a = acc.get(h)
            if not a or a.get("stale") or "한도" in str(a.get("error") or ""):
                wait.append(h); continue
            ps = [p for p in data["instagram"]["posts"] if p["handle"].lower() == h]
            reason = None
            if a.get("error") or not ps:
                reason = "수집 불가"
            else:
                newest = min(hours_ago(p.get("timestamp")) for p in ps) / 24
                limit = 7 if c.get("cat") == "아이랑 갈 곳" else 14
                ad_ratio = sum(1 for p in ps if AD_RE.search(p.get("caption") or "")) / len(ps)
                med = a.get("median_likes") or 0
                if newest > limit: reason = f"최근 {int(newest)}일 게시 없음"
                elif med < 100: reason = f"평소 좋아요 {med}"
                elif ad_ratio >= 0.5: reason = f"광고 비율 {round(ad_ratio * 100)}%"
            if reason:
                if post({"action": "removeChannel", "by": "자동 검증", "handle": h}): drop.append(f"{h}({reason})")
            else:
                note = str(c.get("note", "")).replace("[검증 중]", "").strip()
                note = f"{note} (팔로워 {a.get('followers')}, 평소 좋아요 {a.get('median_likes')}, 광고 {round(ad_ratio * 100)}%)".strip()
                if post({"action": "addChannel", "by": "자동 검증", "handle": h, "cat": c.get("cat"), "note": note}): keep.append(h)
        report["검증"] = f"유지 {keep} · 삭제 {drop} · 보류 {wait}"

    print("결과:", json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
