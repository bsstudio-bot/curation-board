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


def claude(prompt):
    """Claude Code CLI 호출, 응답 텍스트 반환."""
    for attempt in range(2):
        try:
            p = subprocess.run(["claude", "-p", "--model", MODEL, "--output-format", "json", "--max-turns", "1"],
                               input=prompt, capture_output=True, text=True, timeout=600)
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
    src = (f"{it.get('cafe')} 맘카페 인기글 (조회 {it.get('reads')}, 댓글 {it.get('comments')})" if it.get("kind") == "cafe" else f"@{it.get('handle')}")
    t = [f"시리즈: {series}", f"해설 각도 힌트: {it.get('angle') or '없음'}", f"원문 출처: {src}",
         f"게시일: {str(it.get('timestamp') or it.get('written_at') or '')[:10]}", f"오늘 날짜: {NOW:%Y-%m-%d}"]
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
    keys = ["kind", "series", "handle", "cafe", "caption", "title", "permalink", "url", "timestamp", "written_at", "angle", "event_end", "reads", "comments", "cat"]
    return {k: p.get(k) for k in keys if p.get(k) is not None}


def hours_ago(ts):
    try:
        return (NOW - datetime.fromisoformat(str(ts).replace("Z", "+00:00"))).total_seconds() / 3600
    except Exception:
        return 1e9


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

    # A + B: 스크립트
    jobsA = [(q["id"], q.get("item") or {}) for q in queue]
    jobsB = []
    if not ONLY or "B" in ONLY:
        qids = {i for i, _ in jobsA}
        for p in (data.get("picks") or [])[:MAX_PICKS]:
            pid = p.get("permalink") or p.get("url")
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
        print("할 일 없음"); return

    guide = get("guide").get("guide", "") if (jobsA or jobsB) else ""
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
