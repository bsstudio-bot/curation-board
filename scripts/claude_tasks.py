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
MAX_PICKS, MAX_ADS, ADS_BATCH, SCRIPT_BATCH = 18, 40, 20, 4
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


def claude(prompt, tools=None, turns=1, timeout=900, retries=2):
    """Claude Code CLI 호출, 응답 텍스트 반환. tools 예: ["WebSearch", "WebFetch"]"""
    cmd = ["claude", "-p", "--model", MODEL, "--output-format", "json", "--max-turns", str(turns)]
    if tools:
        cmd += ["--allowedTools", ",".join(tools)]
    for attempt in range(retries):
        try:
            p = subprocess.run(cmd,
                               input=prompt, capture_output=True, text=True, timeout=timeout)
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
           else f"구글 급상승 검색어 (검색 {it.get('traffic')})" if it.get("kind") == "trend"
           else f"유튜브 {it.get('channel')} (조회 {it.get('views')})" if it.get("kind") == "youtube"
           else "요즘 뜨는 인물 (웹 검색으로 찾음)" if it.get("kind") == "person" else f"@{it.get('handle')}")
    t = [f"시리즈: {series}", f"해설 각도 힌트: {it.get('angle') or '없음'}", f"원문 출처: {src}",
         f"게시일: {str(it.get('timestamp') or it.get('written_at') or '')[:10]}", f"오늘 날짜: {NOW:%Y-%m-%d}"]
    dc = it.get("date_check") or {}
    if dc.get("status") in ("확인됨", "원문과 다름"):
        t.append(f"웹 검색으로 확인한 일정({dc['status']}): {dc.get('event')} · {dc.get('period')} {dc.get('time')} · {dc.get('place')} · {dc.get('fee')} · 출처 {dc.get('source')} · {dc.get('note')}  ← 원문과 다르면 이 일정을 쓰고 checks에 '원문 일정과 다름'을 남긴다")
    elif dc.get("status") == "확인 못함":
        t.append("일정 웹 확인 실패: 날짜를 단정하지 말고 checks에 '일정 확인'을 넣는다")
    if it.get("story"):
        t.append(f"이야기 뼈대: {it['story']}")
    if it.get("gomong_angle"):
        t.append(f"고몽다운 풀이 힌트: {it['gomong_angle']}")
    if it.get("event_end"):
        t.append(f"행사 종료일(자동 추출): {str(it['event_end'])[:10]}")
    t.append("원문:\n" + str(it.get("caption") or it.get("title") or "")[:2500])
    if it.get("extra"):
        t.append("추가 요청: " + str(it["extra"]))
    return "\n".join(t)


RESEARCH_TASK = """
[작업 순서]
1) 먼저 웹 검색으로 이 소재의 추가 정보를 조사한다: 배경과 전후 맥락, 관련 인물의 사연, 탄생 비화, 비슷한 사례나 비교, 사람들이 몰랐던 사실. 공식 사이트·언론 보도·백과사전 같은 믿을 만한 출처만 쓴다.
   화제 인물이면 검색을 세 갈래로 나눠 한다: (가) 이 사람이 뜬 과정, (나) 비슷하게 입담·캐릭터로 흥했던 사람들, (다) 반짝 화제 뒤 논란·태도 문제 등으로 실제 '나락'간 사례(보도된 것). 단순히 사람이 몰려 쉬었다는 건 나락 사례가 아니다.
   날짜·연도는 기사 날짜로 꼭 확인한다(올해 일을 '작년'이라고 쓰지 않는다).
2) 쓰기 전에 question_chain(질문 사다리 3~5개)을 먼저 설계한다. 그다음 원문 사실 + 조사한 사실 3~6개를 **인과로 이어서** 하나의 이야기로 엮는다. 사실 1, 사실 2, 사실 3을 나열하지 말고, 앞 문장이 다음 문장의 이유나 결과가 되게("근데 여기서", "알고 보니", "그래서") 쓴다.
3) 고몽의 시선으로 각색한다: 이야기꾼답게 긴장과 반전을 살리고, 마무리는 고몽다운 한 줄.
4) 출처로 확인되지 않은 내용은 쓰지 않는다. 애매하면 checks에 남긴다.
5) 출력 JSON에 "research": [{"fact": "조사로 더한 사실", "source": "출처 URL"}] 를 추가한다(대본에 실제로 쓴 것만).
출력은 대본 JSON 객체 하나만(설명·코드블록 없이).
"""


REVIEW_PROMPT = """너는 숏폼 대본 검수자다. 아래 대본(JSON)을 원문과 research(조사 사실·출처)에 대조해서 고쳐라.
1. 정확성: 각 문장이 원문·research와 맞는지 본다. 날짜·연도('작년', '얼마 전')가 research의 날짜와 맞는지 특히 확인한다. 과장·단정·왜곡(예: '잠시 중단 후 재개'를 '결국 문 닫았다'로 쓰는 것)은 사실대로 고친다. 근거 없는 문장은 빼거나 checks로 옮긴다.
2. 흐름: 가이드의 흐름 단계(화제 인물이면 왜 떴나→예전 사례→몰락 사례→열광 이유→사회의 바람→댓글 질문)가 빠지거나 억지로 이어진 곳이 있으면 다듬는다. 몰락 사례는 실제로 보도된 '반짝 뒤 무너진' 사례여야 하고, 맞는 사례가 research에 없으면 그 단계를 억지로 채우지 말고 checks에 '몰락 사례 조사 보강'을 남긴다.
3. 댓글: 마지막 질문과 comment_prompt가 공감 댓글과 다른 의견이 둘 다 나올 수 있는 질문인지 본다.
4. 말투·길이·형식은 유지한다. 출력은 고친 대본 JSON 객체 하나만(같은 필드 구조, 설명 없이)."""


def review_one(sc, it):
    fixed = parse_json(claude(REVIEW_PROMPT + "\n\n[원문]\n" + item_text(it) + "\n\n[대본]\n" + json.dumps(sc, ensure_ascii=False)), dict)
    return fixed if isinstance(fixed, dict) and fixed.get("lines") else sc


def write_one(id_, it, guide):
    ref = open("prompts/story_reference.md", encoding="utf-8").read() if os.path.exists("prompts/story_reference.md") else ""
    prompt = guide + "\n\n" + ref + "\n" + RESEARCH_TASK + "\n[소재]\n" + item_text(it)
    sc = parse_json(claude(prompt, tools=["WebSearch", "WebFetch"], turns=25), dict)
    if isinstance(sc, dict) and sc.get("lines"):
        sc = review_one(sc, it)
    if isinstance(sc, dict) and sc.get("lines") and post({"action": "saveScript", "id": id_, "script": sc, "by": "Claude", "model": "claude-" + MODEL + "+web", "item": it}):
        return 1
    print("  스크립트 실패:", str(id_)[:60])
    return 0


def write_scripts(jobs, guide, label):
    """jobs: [(id, item)] → 하나씩 웹 조사 후 작성·저장(3개 동시). 저장 개수 반환."""
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=3) as ex:
        return sum(ex.map(lambda j: write_one(j[0], j[1], guide), jobs))

def pick_item(p):
    keys = ["kind", "series", "handle", "cafe", "caption", "title", "permalink", "url", "timestamp", "written_at", "angle", "story", "gomong_angle", "event_end", "date_check", "reads", "comments", "cat", "traffic", "channel", "views", "who", "why", "sources", "story_seed", "caution", "character", "intro_point", "extra_value", "visuals", "debate"]
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
시리즈: 아이랑 갈 곳 / 왜 그럴까(시사·경제·소비 해설) / 화제 인물 / 밈·트렌드 / 화제 / 육아 정보 / 맘카페 화제

아래 후보(인스타 글, 맘카페 인기글, 유튜브 인기·육아 쇼츠, 구글 급상승 검색어)를 보고 두 가지를 해라.

1) issues: 지금 뜨는 이슈 3~6개. 이야기로 풀 수 있는 주제를 우선한다(랭킹·통계 묶음은 제외). 여러 후보가 같은 주제를 다루거나(채널 2곳 이상·맘카페·검색어가 겹침), 검색량이 큰 화제를 묶는다.
   각 이슈: title(15자 이내), summary(무슨 일인지 1~2문장, 후보 내용만 근거), ids(관련 후보 id 목록), why_now(왜 지금 다룰 만한지 1문장), fit(1~10, 우리 채널 적합도)
2) picks: 오늘 찍을 후보 15~18개. 시리즈 배분: 아이랑 갈 곳 4~5, 왜 그럴까 4~5, 화제 인물 2~4('요즘 뜨는 인물' 후보에서, 일반인 A와 유명인 선택·커리어 B를 섞어서), 밈·트렌드 2~3, 화제 1~2, 육아 정보 2~3, 맘카페 화제 1~2. 후보가 모자란 시리즈는 억지로 채우지 않는다. 같은 계정은 시리즈당 1개까지.
   각 pick: id(후보 id 그대로), series, score(1~10), reason(왜 고몽 채널에 맞는지 1문장, 구체적으로), story(이야기 뼈대 한 줄: '발단 → 전개 → 반전/결말', 후보 내용 안에서만), angle(시청자가 궁금해할 질문 하나), gomong_angle(고몽다운 풀이 한 줄: 이야기꾼·영화/드라마 비유, 딸 둘 아빠 시선, 자기 고백형 유머 중 자연스러운 것. 2년 넘은 옛날 밈은 쓰지 않는다. 억지면 빈 문자열. 고몽이 실제로 겪은 일(병원·펫로스·육아 에피소드 등)을 사실처럼 지어내지 말고, 필요하면 '고몽 본인 경험이 있다면 ~로'처럼 조건으로 쓴다)

[가장 중요한 기준 — 고몽이 '이야기'로 풀 수 있는가]
고몽은 정보를 나열하는 사람이 아니라 이야기꾼이다. 사람·사건·사연에 발단 → 전개 → 반전/결말이 있고, 고몽이 자기 말투로 30~45초 안에 이야기처럼 들려줄 수 있는 소재를 고른다.
- 좋은 소재: 인물의 선택과 사연, 탄생 비화·뒷이야기, 의외의 반전이 있는 현상, 사람들이 왜 열광하는지 감정이 보이는 트렌드, 아이 키우는 집에서 벌어질 법한 에피소드, 영화·드라마 한 장면처럼 풀 수 있는 일, 고몽 가족이 직접 가서 겪을 수 있는 장소(아이랑 갈 곳).
- 빼는 소재: 랭킹·순위표(학생 수 많은 학교 순위 등), 수익률·연봉·금액 계산(1억 투자하면 얼마 등), 통계·비교표 나열, 체크리스트형 정보 카드. 1차 정보로 단순히 흥미로운 것만으로는 고르지 않는다. 숫자는 이야기를 돕는 양념일 때만 OK.
- '왜 그럴까'는 숫자 해설이 아니라 '그 뒤에 숨은 이야기'(누가, 왜, 어떻게 그렇게 됐나)다.
[점수 기준] 스토리텔링 가능성(가장 중요) > 시의성 > 반응(평소 대비 배수, 여러 곳 동시 언급) > 고몽 캐릭터·가족과의 연결.
[화제·가십 원칙 — 렉카 금지] 공식 발표, 작품·방송 화제, 본인이 공개한 결혼·출산·육아 소식, 훈훈한 장면, 사회적으로 이야기할 거리는 OK.
사생활 폭로, 루머·열애설·이혼설, 사건·사고 피해자, 논란 몰이, 특정인 비난, 사망·범죄, 정치, 해외 스포츠 경기 결과는 고르지 않는다. 화제 픽은 사람보다 현상을 이야기할 수 있어야 한다.
@@CHANNEL_RULES@@
[제외] 광고·공구, 지역 한정 정보, 이미 끝난 행사, 내용 없는 근황. 유튜브는 음악·게임·스포츠 중계, 남의 영상 짜깁기('썰' 재업로드·AI 낭독)는 소재 참고만 하고, 원본 출처가 분명한 것을 우선한다.

3) trends: 후보 중 '구글 급상승' 검색어마다 우리 채널 카테고리(육아·가족, 아이랑 갈 곳, 생활 경제·소비, 밈·트렌드, 화제)와의 관련도를 매겨라.
   관련도 7 이상인 것만: title(검색어 그대로), fit(1~10), category(위 카테고리 중 하나), note(우리 채널에서 어떻게 다룰 수 있는지 1문장, 구체적으로). 사람 이름(연예인·기업인) 검색어는 관련 기사가 육아·가족·생활 소비와 직접 연결될 때만 넣는다. 기업·주가 뉴스, 스포츠 경기, 사건·사고, 정치, 연예 가십은 넣지 않는다. 억지로 채우지 말고 없으면 빈 배열.

출력은 JSON 객체만: {"issues":[...], "picks":[...], "trends":[...]}
"""


def build_pool(data):
    pool = {}
    def ok(p):
        lim = 168 if p.get("cat") == "아이랑 갈 곳" else 72
        return (not p.get("flag") and not p.get("ad") and not p.get("ended") and (p.get("likes") or 0) >= 100
                and len((p.get("caption") or "").strip()) >= 30 and hours_ago(p.get("timestamp")) <= lim)
    rank = lambda p: (p.get("ratio") or 0) * 2 + max(0, 3 - hours_ago(p.get("timestamp")) / 24)
    per_cat = {"아이랑 갈 곳": 30, "해설·인물": 25, "밈·트렌드": 20, "육아 정보": 20}
    for cat, n in per_cat.items():
        for p in sorted([p for p in data["instagram"]["posts"] if p.get("cat") == cat and ok(p)], key=rank, reverse=True)[:n]:
            pool[p["permalink"]] = {"kind": "instagram", **{k: p.get(k) for k in ("handle", "cat", "caption", "likes", "comments", "ratio", "timestamp", "permalink", "thumb", "media_type", "event_end")}}
    cafes = [r for r in data.get("cafe", []) if not r.get("flag") and (r.get("reads") or 0) >= 100 and hours_ago(r.get("written_at")) <= 72]
    cafes.sort(key=lambda r: (r.get("reads") or 0) + (r.get("comments") or 0) * 20, reverse=True)
    for r in cafes[:20]:
        pool[r["url"]] = {"kind": "cafe", **{k: r.get(k) for k in ("cafe", "title", "reads", "comments", "written_at", "url")}, "timestamp": r.get("written_at")}
    yt = (data.get("youtube") or {}).get("items") or []
    kids = sorted([v for v in yt if v["src"].startswith("육아") and v.get("shootable") and hours_ago(v.get("published")) <= 96], key=lambda v: v.get("views_per_hour", 0), reverse=True)[:8]
    for v in kids:
        pool["yt:" + v["id"]] = {"kind": "youtube", "title": v["title"], "channel": v["channel"], "views": v["views"], "likes": v["likes"],
                                 "comments": v["comments"], "short": v["short"], "src": v["src"], "thumb": v.get("thumb"), "url": v["url"],
                                 "timestamp": v.get("published"), "caption": f"{v['title']}\n{v.get('desc', '')[:300]}"}
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
        elif x["kind"] == "youtube":
            out.append(f"[id] {pid}\n유튜브 {'쇼츠' if x['short'] else '영상'} ({x['src']}) · {x['channel']} · 조회 {x['views']} · 좋아요 {x['likes']} · {str(x['timestamp'])[:10]}\n{x['caption'][:350]}")
        elif x["kind"] == "person":
            out.append(f"[id] {pid}\n요즘 뜨는 인물 · 적합도 {x.get('fit')}\n{x['caption'][:500]}")
        else:
            out.append(f"[id] {pid}\n{x['caption']}")
    return "\n\n".join(out)


def feedback_text():
    try:
        fb = get("feedback").get("feedback", [])
    except Exception:
        fb = []
    fb = sorted(fb, key=lambda f: f.get("at") or "", reverse=True)[:60]
    lines = []
    for f in fb:
        it = f.get("item") or {}
        src = it.get("handle") and "@" + it["handle"] or it.get("cafe") or it.get("channel") or ""
        txt = (it.get("caption") or it.get("title") or "")[:90].replace("\n", " ")
        lines.append(f"[{f.get('status')}] {it.get('series') or it.get('cat') or ''} · {src} · {txt}")
    return {f["id"] for f in fb}, "\n".join(lines)


def curate(data, people=None):
    pool = build_pool(data)
    for x in people or []:
        pool[x["id"]] = {k: v for k, v in x.items() if k != "id"}
    done_ids, fb_txt = feedback_text()
    for i in list(pool):
        if i in done_ids:  # 팀이 이미 제작했거나 제외한 글은 다시 올리지 않는다
            pool.pop(i)
    if not pool:
        return None
    fb_block = ("\n\n[팀 반응 — 최근 팀이 표시한 소재]\n'제작함'은 팀이 실제로 찍기로 한 방향이니 비슷한 소재·각도·시리즈에 가점, "
                "'제외'는 팀이 원치 않은 방향이니 비슷한 소재에 감점한다. 개별 글이 아니라 패턴(주제, 형식, 계정 성격)을 읽어 반영해라.\n" + fb_txt) if fb_txt else ""
    res = parse_json(claude(CURATE_PROMPT.replace("@@CHANNEL_RULES@@", CHANNEL_RULES) + fb_block + "\n\n오늘 날짜: " + NOW.strftime("%Y-%m-%d") + "\n\n[후보]\n" + pool_text(pool)), dict)
    if not isinstance(res, dict) or not res.get("picks"):
        print("  [E] 파싱 실패"); return None
    picks = []
    for pk in res["picks"]:
        x = pool.get(pk.get("id"))
        if not x:
            continue
        picks.append({**x, "id": pk["id"], "series": pk.get("series"), "score": pk.get("score"), "reason": pk.get("reason"),
                      "angle": pk.get("angle"), "story": pk.get("story", ""), "gomong_angle": pk.get("gomong_angle", "")})
    issues = []
    for it in res.get("issues", []):
        ids = [i for i in it.get("ids", []) if i in pool]
        issues.append({**{k: it.get(k) for k in ("title", "summary", "why_now", "fit")}, "items": [{"id": i, **pool[i]} for i in ids]})
    tmap = {t["title"]: t for t in ((data.get("trends") or {}).get("items") or [])}
    trends = []
    for t in res.get("trends", []) or []:
        src = tmap.get(t.get("title"))
        if src and (t.get("fit") or 0) >= 7:
            trends.append({**src, "fit": t.get("fit"), "category": t.get("category"), "note": t.get("note")})
    trends.sort(key=lambda t: t.get("fit") or 0, reverse=True)
    return {"date": NOW.strftime("%Y-%m-%d"), "generated_at": NOW.isoformat(), "source_updated_at": data.get("updated_at"),
            "model": "claude-" + MODEL, "pool_size": len(pool), "issues": issues, "picks": picks, "trends": trends}


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
        if x.get("series") == "아이랑 갈 곳" or x.get("cat") == "아이랑 갈 곳" or (x.get("series") in (None, "화제", "밈·트렌드") and DATE_RE.search(text) and re.search(r"축제|전시|팝업|페스티벌|박람회|공연|개막|기간", text)):
            seen.add(xid); out.append({"id": xid, "text": f"출처: {x.get('handle') or x.get('cafe') or ''}\n{text}"})
    return out[:10]


# ---------------------------------------------------------------- G. 육아 쇼츠 → 고몽 버전 포맷 (그대로 쓰지 않고 형식만 빌린다)
CHANNEL_RULES = """[신규 채널 촬영 원칙 — 아이 등장]
- 화자는 고몽. 고몽이 아이들과 노는 장면이 중심이다.
- 아이 얼굴이 나와도 된다: 일상에서 자연스럽게, 유머러스하게 함께 노는 장면이면 무방하다.
- 지양: 아이 얼굴을 크게 잡아 두고 그 위에 정보·설명 자막을 얹는 구성(아이 얼굴이 설명 영상의 배경이나 썸네일 소재가 되는 것), 아이가 제품을 들고 홍보하거나 광고·공구 등 상업적으로 쓰이는 것.
- 정보·설명 파트는 고몽이 직접 말하거나 화면 그래픽으로 처리하고, 아이는 그 앞뒤의 일상 장면에 자연스럽게 나온다.
- 아이 이름, 다니는 기관, 사는 동네 같은 개인 정보는 넣지 않는다. 아이를 놀리거나 우는 모습을 웃음 소재로 쓰지 않는다. 남의 영상을 퍼오거나 '썰' 낭독 형식은 쓰지 않는다."""

CLASSIFY_PROMPT = """아래는 유튜브 육아 쇼츠 목록이다. 우리 채널은 크리에이터(아빠 고몽)와 아이들이 영상 전체를 직접 끌고 가는 형식만 찍는다.
각 영상이 우리가 **같은 구도로 직접 찍을 수 있는 형식**인지 판정해라.
- shootable=true: 부모(크리에이터)가 직접 출연해 아이와 일상·놀이·상황극·꿀팁을 보여주는 영상
- shootable=false: 뉴스·방송 클립 재가공, CCTV·블랙박스, 남의 영상 짜깁기·모음, 썰·사연 낭독, AI 생성, 동물·연예인 방송 클립, 아이 단독 출연(부모 없음)
각 항목 JSON: id(그대로), shootable(true/false), format(형식 한 줄, 예: '아빠 시점 상황극', '엄마 꿀팁 토크+시연'), reason(판정 이유 짧게)
출력은 JSON 배열만."""

FORMAT_PROMPT = """너는 유튜버 고몽(김웅현)의 신규 채널 숏폼 기획 PD다. 고몽은 영화·드라마 해설 10년차 '스토리 중독자'이고 3살·6살 두 딸 아빠다.
유튜브 10년은 해설 채널 경력이고 육아는 첫째 기준 약 6년이다. '육아 10년차' 같은 경력이나 고몽이 실제 겪은 일을 지어내지 말고, 기획은 '이렇게 찍어보자'는 제안으로 쓴다.
아래는 부모가 직접 출연하는, 요즘 조회가 잘 나오는 육아 쇼츠들이다. 그대로 따라 찍지 않는다. **왜 잘 됐는지(형식·감정·훅)만 뽑아서** 고몽 채널 버전으로 다시 기획해라.
핵심: 고몽과 아이들이 영상 처음부터 끝까지 직접 끌고 간다. 뉴스 화면, CCTV, 남의 영상, 자료 화면에 기대는 구성은 쓰지 않는다. 집·차 안·놀이터·공원처럼 실제로 찍을 수 있는 장소와 구도로 짠다. 이웃·가게 주인 등 가족 외 사람이 나오거나 사는 동네가 드러나는 구성은 피한다.

""" + CHANNEL_RULES + """

좋은 레퍼런스 6~8개를 골라 각각 JSON 객체로:
id(그대로), why_it_works(잘 된 이유 1문장: 훅·공감 포인트·형식), gomong_title(고몽 버전 제목 15자 이내),
gomong_idea(고몽이 아이들과 직접 찍는 기획 1~2문장), hook(첫 2초 대사 또는 자막 한 줄, 고몽 말투, 옛날 밈 금지),
shots(촬영 구성 3~5컷 배열: 장소·구도·누가 무엇을 하는지. 정보 파트는 고몽 토크), privacy(아이 등장 방식 한 줄),
props(필요한 소품·장소, 없으면 ""), series(아이랑 갈 곳/육아 정보/밈·트렌드 중 가장 가까운 것)
출력은 JSON 배열만.
"""


def classify_shorts(data):
    yt = (data.get("youtube") or {}).get("items") or []
    cand = sorted([v for v in yt if v["src"].startswith("육아") and not v.get("skip") and hours_ago(v.get("published")) <= 96],
                  key=lambda v: v.get("views_per_hour", 0), reverse=True)[:45]
    if not cand:
        return {}
    body = "\n".join(f"[id] yt:{v['id']} | {v['channel']} | {v['title']} | {(v.get('desc') or '')[:120]}" for v in cand)
    arr = parse_json(claude(CLASSIFY_PROMPT + "\n\n" + body), list)
    if not isinstance(arr, list):
        print("  [분류] 파싱 실패"); return {}
    return {a["id"]: {"shootable": bool(a.get("shootable")), "format": a.get("format", ""), "reason": a.get("reason", "")} for a in arr if isinstance(a, dict) and a.get("id")}


def apply_review(data, review):
    for v in (data.get("youtube") or {}).get("items") or []:
        r = review.get("yt:" + v["id"])
        v["shootable"] = bool(r and r["shootable"])


def make_formats(data):
    yt = (data.get("youtube") or {}).get("items") or []
    kids = sorted([v for v in yt if v["src"].startswith("육아") and v.get("shootable") and hours_ago(v.get("published")) <= 96], key=lambda v: v.get("views_per_hour", 0), reverse=True)[:14]
    if not kids:
        return []
    body = "\n\n".join(f"[id] yt:{v['id']}\n{v['channel']} · 조회 {v['views']} · 좋아요 {v['likes']} · 댓글 {v['comments']} · {v.get('duration')}초\n제목: {v['title']}\n설명: {(v.get('desc') or '')[:200]}" for v in kids)
    arr = parse_json(claude(FORMAT_PROMPT + "\n\n" + body), list)
    if not isinstance(arr, list):
        print("  [G] 파싱 실패"); return []
    ref = {"yt:" + v["id"]: v for v in kids}
    out = []
    for f in arr:
        v = ref.get(f.get("id"))
        if isinstance(f, dict) and v:
            out.append({**f, "ref": {"title": v["title"], "channel": v["channel"], "views": v["views"], "url": v["url"], "thumb": v.get("thumb"), "published": v.get("published")}})
    return out


# ---------------------------------------------------------------- 계정 유형 (크리에이터 / 매거진·미디어)
TYPE_PATH = "site/account_types.json"


def account_types(data):
    at = json.load(open(TYPE_PATH, encoding="utf-8")) if os.path.exists(TYPE_PATH) else {"types": {}}
    types = at.setdefault("types", {})
    new = [a for a in data["instagram"]["accounts"] if a["handle"] not in types and not a.get("error")]
    if new:
        posts = data["instagram"]["posts"]
        body = "\n\n".join(f"[handle] {a['handle']} | 이름: {a.get('name','')} | 분류: {a.get('cat','')} | 팔로워 {a.get('followers')}\n" +
                           "\n".join("- " + (p.get("caption") or "")[:120].replace("\n", " ") for p in posts if p["handle"] == a["handle"])[:500] for a in new[:30])
        arr = parse_json(claude("""아래 인스타 계정이 '크리에이터'(개인이 이름·얼굴을 걸고 운영하는 엄마·아빠·전문가 계정)인지, '매거진·미디어'(편집부·큐레이션·뉴스·브랜드·기관 계정)인지 판정해라.
각 항목 JSON: handle, type('크리에이터'|'매거진·미디어'). 출력은 JSON 배열만.\n\n""" + body), list) or []
        for x in arr:
            if isinstance(x, dict) and x.get("handle") in {a["handle"] for a in new} and x.get("type") in ("크리에이터", "매거진·미디어"):
                types[x["handle"]] = x["type"]
        at["updated_at"] = NOW.strftime("%Y-%m-%d")
        json.dump(at, open(TYPE_PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
        open(".ai_updated", "w").write("1")
    return types


# ---------------------------------------------------------------- P. 요즘 뜨는 인물 (웹 검색)
PEOPLE_PROMPT = """오늘은 {today}이다. 지금 한국에서 이야기되는 '인물'을 웹 검색으로 찾아 두 종류로 후보를 넓게 모아라. 팀이 이 중에서 골라 다룬다.

[A. 일반인 화제 인물] 최근 4주 사이 SNS로 갑자기 뜬 시장 상인·자영업자·장인·평범한 사람.
 기준 예: 남대문 스카프 아저씨 — (1) 캐릭터(반복되는 말투·행동) (2) 볼거리(본인 영상 여러 개, 큰 조회수) (3) 손에 잡히는 정보(6장 1만원, 남대문시장)는 필수, (4) 확장성(비슷한 사례·사회 현상) (5) 진행 중인 판(후속 소식, 의견 갈림) 중 하나 이상.
 제외: 한 번의 선행·미담·감동 사연, 합격·성공 사연 단발, 기사 한 줄뿐인 사람, 논란·갈등·피해 호소로 화제가 된 사람(예: 방송 출연 논란).
 검색어 예: SNS 화제 사장님, 입담 상인, 인터넷 스타 된, 알고리즘 점령, 줄 서는 가게 사장, 화제의 할머니·할아버지.

[B. 유명인의 선택·커리어 이야기] 최근 2주 뉴스에 나온 셰프·기업인·운동선수·크리에이터·배우·작가 등의 '결정의 순간'.
 기준 예: 캉가에 경제 「손종원은 어떻게 신세계그룹 임원이 됐을까」 — 임원 발탁·이적·은퇴·창업·복귀·방송 거절·사업 전환처럼 결정이 있고, 그 뒤에 '어떻게 거기까지 갔나'를 파고들 배경(경력, 비하인드, 숫자)이 있으며, 시청자가 자기 삶에 대입해 의견을 낼 수 있는 것.
 제외: 열애·결혼·이혼 등 사생활, 사건·사고·논란·구설, 정치인, 단순 작품 홍보·컴백 소식, 해외 인물만의 소식.
 검색어 예: 임원 발탁 셰프, 은퇴 선언, 창업한 선수, 방송 거절, 연봉 포기, 깜짝 이적, 인생 2막.

A는 있는 만큼(없으면 0명, 최대 4명), B는 4~6명. 각 JSON 객체:
 type('A 일반인'|'B 선택·커리어'), name(호칭), who(누구인지 한 줄), character(이 사람다움 또는 결정의 핵심 한 줄), why(왜 지금 화제인지: 장면·숫자), since(언제부터),
 signals(화제 근거: 조회수·기사 수), visuals(쓸 수 있는 장면·자료), intro_point(정확히 무엇으로 소개하나), extra_value(시청자가 얻어갈 정보·재미·생각할 거리),
 sources(근거 URL 2~3개), story_seed(이야기 씨앗: A는 비슷하게 흥한 사례/반짝 뒤 무너진 사례/열광 이유/사회적 의미, B는 과거 경력·전환점/비슷한 선택을 한 사람들/이 결정이 말해주는 것),
 debate(의견이 갈릴 지점), caution(주의할 점), fit(1~10: 고몽 채널에서 이야기로 풀기 좋은 정도)
'intro_point'와 'extra_value'를 한 줄씩 못 쓰면 넣지 않는다. 검색으로 확인된 사실만 쓴다. 출력은 JSON 배열만."""


def hot_people(prev=None):
    raw = claude(PEOPLE_PROMPT.replace("{today}", NOW.strftime("%Y-%m-%d")), tools=["WebSearch", "WebFetch"], turns=30)
    arr = parse_json(raw, list)
    if not isinstance(arr, list):
        print("  [인물] 파싱 실패:", (raw or "")[:300]); return []
    out = []
    for x in arr:
        if isinstance(x, dict) and x.get("name"):
            src = x.get("sources") or []
            cap = (f"화제 인물: {x['name']} — {x.get('who','')}\n캐릭터: {x.get('character','')}\n왜 떴나: {x.get('why','')}\n언제부터: {x.get('since','')} · 화제 근거: {x.get('signals','')}\n"
                   f"소개 포인트: {x.get('intro_point','')}\n시청자가 얻어갈 것: {x.get('extra_value','')}\n쓸 수 있는 장면: {x.get('visuals','')}\n이야기 씨앗: {x.get('story_seed','')}\n의견 갈림: {x.get('debate','')}\n주의: {x.get('caution','')}\n출처: " + " ".join(src))
            out.append({"kind": "person", "ptype": x.get("type") or "", "id": "person:" + x["name"], "title": x["name"], "who": x.get("who"), "why": x.get("why"),
                        "since": x.get("since"), "signals": x.get("signals"), "sources": src, "story_seed": x.get("story_seed"),
                        "caution": x.get("caution"), "fit": x.get("fit"), "caption": cap, "character": x.get("character"), "intro_point": x.get("intro_point"),
                        "extra_value": x.get("extra_value"), "visuals": x.get("visuals"), "debate": x.get("debate"), "url": src[0] if src else "", "timestamp": NOW.isoformat()})
    print("  [인물] 후보:", [(x["title"], x.get("fit"), bool(x.get("intro_point")), bool(x.get("extra_value"))) for x in out])
    out = [x for x in out if (x.get("fit") or 0) >= 6 and x.get("intro_point") and x.get("extra_value")]
    def _key(n):
        return re.sub(r"[^가-힣A-Za-z0-9]", "", n or "")
    def _same(a, b):
        a, b = _key(a), _key(b)
        if not a or not b:
            return False
        if a in b or b in a:
            return True
        ga = {a[i:i + 2] for i in range(len(a) - 1)}; gb = {b[i:i + 2] for i in range(len(b) - 1)}
        return len(ga & gb) / max(1, min(len(ga), len(gb))) >= 0.6
    dedup = []
    for x in sorted(out, key=lambda x: x.get("fit") or 0, reverse=True):
        if not any(_same(x["title"], y["title"]) for y in dedup):
            dedup.append(x)
    out = dedup
    names = {x["title"] for x in out}
    for x in prev or []:  # 전에 기준을 통과한 인물은 2주 동안 유지 (검색 결과가 날마다 흔들려도 빠지지 않게)
        first = x.get("first_seen") or x.get("timestamp") or ""
        if not any(_same(x.get("title"), n) for n in names) and (x.get("fit") or 0) >= 6 and x.get("intro_point") and hours_ago(first) <= 14 * 24:
            out.append(dict(x, carried=True)); names.add(x["title"])
    for x in out:
        x.setdefault("first_seen", x.get("timestamp"))
    return sorted(out, key=lambda x: x.get("fit") or 0, reverse=True)


# ---------------------------------------------------------------- K. 팝콘 리서치 뉴스 (고몽 팝콘 PB, 3일마다)
POPCORN_PROMPT = """오늘은 {today}이다. 우리는 유튜버 고몽(영화 해설 10년차) 이름으로 PB 팝콘(봉지 125g급, 편의점 10~20대 판매·바이럴 목표, '영화 보는 사람의 팝콘')을 만든다.
이미 국내에 나온 맛·형식을 따라 하면 임팩트가 없다. 그래서 '국내에 아직 없는 것'을 찾는 게 핵심이다.
웹 검색으로 두 묶음을 찾아라.

[global] 해외(미국·일본·영국·유럽·동남아 등) 팝콘 레퍼런스 10~14개: 최근 1년 안의 새 맛·시즈닝, 패키지·라인업 디자인, 대용량·한정판, 극장·OTT·영화 콜라보, 캐릭터·굿즈, 제조 방식(케틀·에어팝·코팅), 판매·바이럴 방식.
 각 JSON: name, brand, country, when, point(무슨 일인지·숫자, 확인된 사실만), kr_status('국내 없음'|'비슷한 것 있음'|'이미 있음'), kr_note(국내에 비슷한 게 있으면 무엇인지, 없으면 ""), borrow(국내·고몽 팝콘에 어떻게 빌려올지 한 줄), tags(2~3개), src(근거 URL).
 kr_status는 국내 검색으로 확인한 뒤 매긴다. '국내 없음'을 우선해서 고른다.

[domestic] 국내 최근 60일 팝콘·스낵 새 소식 4~6개(편의점·극장·콜라보·시즈닝). 각 JSON: name, brand, when, point, learn(고몽 팝콘이 피하거나 배울 점), tags, src.
 곰표 팝콘(2020), 넷플릭스 점보팝콘(2023)처럼 오래된 건 빼라.

출력은 JSON 객체만: {"global":[...], "domestic":[...]}"""


KR_CHECK_PROMPT = """아래 해외 팝콘 사례마다 '한국에 이미 비슷한 게 나왔는지' 국내 검색으로 다시 확인해라.
검색은 한국어로 2~3번씩: 맛 이름 + 팝콘(예: '두바이 팝콘', '피스타치오 팝콘'), 형식 + 팝콘(예: '과자맛 팝콘', '시즈닝 동봉 팝콘', '멀티팩 팝콘', '팝콘통 굿즈'), 편의점·극장(CU, GS25, 세븐일레븐, CGV, 롯데시네마, 메가박스) 이름과 함께.
제품명·편의점·극장 기사나 공식 SNS가 하나라도 나오면 '이미 있음', 비슷한 형식이 있으면 '비슷한 것 있음', 정말 못 찾으면 '국내 없음'.
각 항목 JSON: name(그대로), kr_status, kr_note(찾은 국내 사례 이름·시점·출처, 없으면 ""). 출력은 JSON 배열만."""


def verify_kr(glob):
    if not glob:
        return glob
    body = "\n".join(f"- {x['name']} ({x.get('brand','')}, {x.get('country','')}): {str(x.get('point',''))[:150]}" for x in glob)
    from concurrent.futures import ThreadPoolExecutor
    chunks = [glob[i:i + 4] for i in range(0, len(glob), 4)]
    def run(ch):
        b = "\n".join(f"- {x['name']} ({x.get('brand','')}, {x.get('country','')}): {str(x.get('point',''))[:150]}" for x in ch)
        r = parse_json(claude(KR_CHECK_PROMPT + "\n\n" + b, tools=["WebSearch", "WebFetch"], turns=16, timeout=600, retries=1), list)
        return r if isinstance(r, list) else []
    with ThreadPoolExecutor(max_workers=4) as ex:
        arr = [a for r in ex.map(run, chunks) for a in r]
    fix = {a.get("name"): a for a in arr if isinstance(a, dict)}
    for x in glob:
        f = fix.get(x["name"])
        if f and f.get("kr_status") in ("국내 없음", "비슷한 것 있음", "이미 있음"):
            x["kr_status"], x["kr_note"], x["kr_checked"] = f["kr_status"], f.get("kr_note", ""), True
    return glob


def popcorn_news():
    from concurrent.futures import ThreadPoolExecutor
    base = POPCORN_PROMPT.replace("{today}", NOW.strftime("%Y-%m-%d"))
    parts = {"global": base + "\n\n이번에는 [global]만 찾아라. 출력은 {\"global\":[...]}",
             "domestic": base + "\n\n이번에는 [domestic]만 찾아라. 출력은 {\"domestic\":[...]}"}
    def run(k):
        r = parse_json(claude(parts[k], tools=["WebSearch", "WebFetch"], turns=22, timeout=1200, retries=1), dict)
        return (r or {}).get(k) if isinstance(r, dict) else None
    with ThreadPoolExecutor(max_workers=2) as ex:
        g, d = ex.map(run, ["global", "domestic"])
    res = {"global": g or [], "domestic": d or []}
    if not res["global"] and not res["domestic"]:
        return [], []
    ok = lambda arr: [x for x in arr or [] if isinstance(x, dict) and x.get("name")]
    order = {"국내 없음": 0, "비슷한 것 있음": 1, "이미 있음": 2}
    glob = verify_kr(ok(res.get("global")))
    return ok(res.get("domestic")), sorted(glob, key=lambda x: order.get(x.get("kr_status"), 3))


CATS = "먹거리·간식/교구·도서/장난감·놀이/패션·잡화/생활·주방/여행·숙소·체험/뷰티·건강/가전·디지털/기타"
ADS_PROMPT = f"""아래는 육아·키즈 인스타 계정의 광고·공구·이벤트 글이다. 각 글의 캡션만 근거로 상품 정보를 정리해라.
캡션에 없는 내용은 빈 문자열로 둔다(추측 금지).
필드: id(그대로), handle(그대로), type('공구'|'광고·협찬'|'이벤트'), brand(업체·브랜드 이름, @계정이 있으면 괄호로 같이. 예: '베베쿡 (@bebecook_official)'),
product(대표 제품 1개, 여러 개면 '대표 제품 외 N종'), category({CATS} 중 하나), price(대표 판매가·공구가, 원 단위 정수. 1천~50만 원 사이의 현실적인 소비자 가격만, 그 밖이거나 애매하면 ""),
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
    if "P" in ONLY and ai.get("picks"):  # 요즘 뜨는 인물만 찾아서 오늘 추천에 더하기
        people = hot_people(ai.get("people"))
        ai["people"] = people
        keep = [x for x in ai["picks"] if x.get("kind") != "person"]
        add = [dict(x, series="화제 인물", score=x.get("fit"), reason=x.get("intro_point") or x.get("why"), story=x.get("story_seed", "")) for x in people[:4]]
        ai["picks"] = add + keep
        json.dump(ai, open(ai_path, "w", encoding="utf-8"), ensure_ascii=False)
        open(".ai_updated", "w").write("1")
        report["인물"] = f"찾음 {len(people)} · 추천 추가 {len(add)}"
    last_pop = ai.get("popcorn_news_at") or ""
    if "K" in ONLY or (not ONLY and ai.get("picks") and hours_ago(last_pop) >= 72):
        news, glob = popcorn_news()
        if news or glob:
            ai["popcorn_news"] = news
            ai["popcorn_global"] = glob
            ai["popcorn_news_at"] = NOW.isoformat()
            json.dump(ai, open(ai_path, "w", encoding="utf-8"), ensure_ascii=False)
            open(".ai_updated", "w").write("1")
            report["팝콘 뉴스"] = f"국내 {len(news)} · 해외 {len(glob)}"
    if "G" in ONLY and ai.get("picks"):  # 육아 쇼츠 분류·포맷만 다시
        ai["yt_review"] = classify_shorts(data)
        apply_review(data, ai["yt_review"])
        ai["formats"] = make_formats(data)
        json.dump(ai, open(ai_path, "w", encoding="utf-8"), ensure_ascii=False)
        open(".ai_updated", "w").write("1")
        report["포맷"] = len(ai["formats"])
    if need_e:
        review = classify_shorts(data)
        apply_review(data, review)
        people = hot_people(ai.get("people"))
        new = curate(data, people)
        if new:
            checks = verify_dates(date_targets(new))
            for x in new["picks"] + [it for i in new["issues"] for it in i["items"]]:
                if x.get("id") in checks:
                    x["date_check"] = checks[x["id"]]
            new["date_checked"] = len(checks)
            for k in ("popcorn_news", "popcorn_global", "popcorn_news_at"):
                if k in ai:
                    new.setdefault(k, ai[k])
            new["yt_review"] = review
            new["people"] = people
            new["formats"] = make_formats(data)
            ai = new
            json.dump(ai, open(ai_path, "w", encoding="utf-8"), ensure_ascii=False)
            open(".ai_updated", "w").write("1")
            report["큐레이션"] = f"이슈 {len(ai['issues'])} · 추천 {len(ai['picks'])} (후보 {ai['pool_size']}) · 일정 확인 {ai.get('date_checked', 0)} · 포맷 {len(ai.get('formats', []))}"

    # A + B: 스크립트
    jobsA = [(q["id"], q.get("item") or {}) for q in queue]
    jobsB = []
    if not ONLY or "B" in ONLY or "R" in ONLY:
        qids = {i for i, _ in jobsA}
        src_picks = ai.get("picks") if ai.get("date") == NOW.strftime("%Y-%m-%d") else (data.get("picks") or [])
        for p in (src_picks or [])[:MAX_PICKS]:
            pid = p.get("id") or p.get("permalink") or p.get("url")
            if pid and (pid not in have_scripts or "R" in ONLY) and pid not in qids:
                jobsB.append((pid, pick_item(p)))

    # C: 광고 정리
    jobsC = []
    if not ONLY or "C" in ONLY:
        have_ads = {a["id"] for a in get("ads").get("ads", [])}
        types = account_types(data)
        posts = [p for p in data["instagram"]["posts"] if p.get("ad") and types.get(p["handle"]) == "크리에이터" and p.get("permalink") not in have_ads and hours_ago(p.get("timestamp")) <= 30 * 24]
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
