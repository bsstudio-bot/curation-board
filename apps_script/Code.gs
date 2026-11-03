/**
 * 데일리 큐레이션 보드 - 공유 저장소 (Google Sheet + Apps Script 웹앱)
 * - GET  ?action=all       : 채널 목록 + 찜 목록 (누구나 읽기)
 * - GET  ?action=channels  : 수집용 채널 목록
 * - POST {pass, action, ...}: 쓰기 (팀 비밀번호 필요) · setFeedback / deleteAds / deleteScripts 포함
 *   addChannel / removeChannel / restoreChannel / addBookmark / removeBookmark / collectNow / seedChannels / setGhToken
 */
const SHEET_TITLE = '큐레이션 보드 DB';
const REPO = 'bsstudio-bot/curation-board';
const CATS = ['아이랑 갈 곳', '육아 정보', '해설·인물', '밈·트렌드'];

function props_() { return PropertiesService.getScriptProperties(); }

function ss_() {
  let id = props_().getProperty('SS_ID');
  if (id) return SpreadsheetApp.openById(id);
  const s = SpreadsheetApp.create(SHEET_TITLE);
  props_().setProperty('SS_ID', s.getId());
  const ch = s.getSheets()[0];
  ch.setName('channels');
  ch.appendRow(['handle', 'cat', 'note', 'added_by', 'added_at', 'active', 'removed_by', 'removed_at']);
  const bm = s.insertSheet('bookmarks');
  bm.appendRow(['id', 'saved_at', 'saved_by', 'kind', 'item_json']);
  return s;
}

function sheet_(name) { return ss_().getSheetByName(name); }

function rows_(name) {
  const v = sheet_(name).getDataRange().getValues();
  const h = v.shift();
  return v.map(function (r, i) {
    const o = { _row: i + 2 };
    h.forEach(function (k, j) { o[k] = r[j] instanceof Date ? r[j].toISOString() : r[j]; });
    return o;
  });
}

function out_(o) {
  return ContentService.createTextOutput(JSON.stringify(o)).setMimeType(ContentService.MimeType.JSON);
}

function isActive_(r) { return !(r.active === false || String(r.active).toUpperCase() === 'FALSE'); }

function channels_(includeRemoved) {
  return rows_('channels').filter(function (r) { return r.handle && (includeRemoved || isActive_(r)); })
    .map(function (r) {
      return { handle: String(r.handle), cat: r.cat, note: r.note || '', added_by: r.added_by || '', added_at: r.added_at || '',
        active: isActive_(r), removed_by: r.removed_by || '', removed_at: r.removed_at || '' };
    });
}

function bookmarks_() {
  return rows_('bookmarks').filter(function (r) { return r.id; }).map(function (r) {
    let item = {};
    try { item = JSON.parse(r.item_json || '{}'); } catch (e) {}
    return { id: String(r.id), saved_at: r.saved_at, saved_by: r.saved_by || '', kind: r.kind, item: item };
  });
}

function normHandle_(s) {
  s = String(s || '').trim();
  const m = s.match(/instagram\.com\/([A-Za-z0-9._]+)/);
  if (m) s = m[1];
  s = s.replace(/^@/, '').replace(/[/?#].*$/, '').toLowerCase();
  if (['p', 'reel', 'reels', 'stories', 'explore'].indexOf(s) >= 0) return '';
  return /^[a-z0-9._]{1,30}$/.test(s) ? s : '';
}


// ---------------------------------------------------------------- 숏폼 스크립트 (Claude가 작성, GitHub Actions)
const GUIDE = "너는 3살·6살 두 아이를 키우는 아빠 크리에이터의 숏폼(인스타 릴스·유튜브 쇼츠) 대본 작가다.\n채널은 '아이랑 갈 곳', '왜 그럴까(시사·경제·소비 해설)', '밈·트렌드', '육아 정보', '맘카페 화제' 시리즈를 한 계정에서 운영한다.\n레퍼런스는 인스타 @kangae_eco(캉가에 경제)처럼 어려운 이야기를 옆자리 사람에게 얘기하듯 쉽게 풀어주는 방식이다.\n\n[말투]\n- 크리에이터 본인이 카메라를 보고 말한다. 친한 지인에게 얘기하듯 자연스러운 존댓말 구어체(\"~거든요\", \"~더라고요\", \"~잖아요\", \"~래요\"). 반말 금지.\n- 한 문장은 숨 한 번에 말할 길이(대략 25자 이내). 문어체, 보도자료체, 번역투, 광고 문구 금지.\n- 기계적인 표현 금지: \"알림으로 다시 부릅니다\", \"A 사러 와서 B도 삽니다\" 같은 어색한 직역·조어를 쓰지 않는다.\n- 감탄사·이모지 남발 금지. 과장(\"역대급\", \"미쳤다\") 최소화.\n\n[구조: 30~45초, 말하는 분량 150~220자]\n1. 훅(0~3초): 의외의 사실이나 궁금증 한 줄. 결론부터.\n2. 왜?: 그 일이 생긴 이유나 배경 1~2문장.\n3. 핵심 2~3개: 정보(날짜·가격·장소·숫자)는 화면 이미지로 띄우고, 말은 짧게.\n4. 마무리: \"우리 집이라면\"이나 크리에이터 본인 경험/계획 한마디. 구독·좋아요 요청 문구는 넣지 않는다.\n\n[시리즈별 필수]\n- 아이랑 갈 곳: 기간, 가격(무료 여부), 추천 나이, 예약·주차·휴무 같은 주의점.\n- 왜 그럴까: 질문 하나에 답 하나. 숫자는 최대 2개.\n- 밈·트렌드: 어디서 시작됐는지, 왜 퍼졌는지, 우리 집(아이 키우는 집) 버전.\n- 육아 정보: 출처(전문가·연구)가 있으면 화면에 표기. 의학적 단정 금지.\n- 맘카페 화제: \"요즘 엄마들이 이걸 많이 물어보더라고요\"처럼 시작해 핵심 쟁점을 정리. 특정 지역·기관 비방 금지.\n\n[사실 규칙]\n- 원문에 없는 이유나 배경을 \"~인가 봐요\", \"~일 거예요\"처럼 추측해서 말하지 않는다. 원문에 이유가 없으면 '왜?' 단계는 원문 속 다른 사실로 채우고 checks에 '배경·이유 확인'을 넣는다.\n- 원문에 있는 사실만 쓴다. 원문에 없는 날짜·가격·통계·인용을 지어내지 않는다.\n- 확인이 필요한 내용은 대본에 단정하지 말고 checks 항목에 적는다.\n- 정치 이슈, 가십, 출처 없는 통계는 다루지 않는다.\n\n[출력: JSON만]\n{\n \"title\": \"내부 관리용 제목 (15자 이내)\",\n \"hooks\": [\"훅 후보 1\", \"훅 후보 2\", \"훅 후보 3\"],\n \"lines\": [{\"say\": \"말할 문장\", \"screen\": \"화면에 띄울 이미지/자막 지시 (없으면 빈 문자열)\"}],\n \"seconds\": 예상 길이(초, 숫자),\n \"caption\": \"게시물 캡션 초안 (2~3문장, 자연스러운 말투)\",\n \"hashtags\": [\"#해시태그\", \"...\"],\n \"checks\": [\"촬영 전 확인할 것\"],\n \"source\": \"출처 표기 문구 (예: 원문 @계정)\"\n}\nlines의 첫 문장은 hooks[0]과 같게 쓴다. lines는 6~9줄.\n";

function scriptsSheet_() {
  const s = ss_();
  let sh = s.getSheetByName('scripts');
  if (!sh) { sh = s.insertSheet('scripts'); sh.appendRow(['id', 'created_at', 'by', 'model', 'script_json', 'item_json']); }
  return sh;
}

function scripts_() {
  const v = scriptsSheet_().getDataRange().getValues(); v.shift();
  return v.filter(function (r) { return r[0]; }).map(function (r) {
    let sc = {}, it = {};
    try { sc = JSON.parse(r[4] || '{}'); } catch (e) {}
    try { it = JSON.parse(r[5] || '{}'); } catch (e) {}
    return { id: String(r[0]), created_at: r[1] instanceof Date ? r[1].toISOString() : r[1], by: r[2], model: r[3], script: sc, item: it };
  });
}

function itemText_(it) {
  const src = it.kind === 'cafe' ? (it.cafe + ' 맘카페 인기글 (조회 ' + it.reads + ', 댓글 ' + it.comments + ')') : ('@' + it.handle);
  const series = it.series || ({'아이랑 갈 곳': '아이랑 갈 곳', '육아 정보': '육아 정보', '해설·인물': '왜 그럴까', '밈·트렌드': '밈·트렌드'})[it.cat] || (it.kind === 'cafe' ? '맘카페 화제' : '왜 그럴까');
  return '시리즈: ' + series + '\n해설 각도 힌트: ' + (it.angle || '없음') + '\n원문 출처: ' + src +
    '\n게시일: ' + String(it.timestamp || it.written_at || '').slice(0, 10) + '\n오늘 날짜: ' + Utilities.formatDate(new Date(), 'Asia/Seoul', 'yyyy-MM-dd') +
    (it.event_end ? '\n행사 종료일(자동 추출): ' + String(it.event_end).slice(0, 10) : '') +
    '\n원문:\n' + String(it.caption || it.title || '').slice(0, 2500) + (it.extra ? '\n\n추가 요청: ' + it.extra : '');
}

function feedbackSheet_() {
  const s = ss_();
  let sh = s.getSheetByName('feedback');
  if (!sh) { sh = s.insertSheet('feedback'); sh.appendRow(['id', 'at', 'by', 'status', 'item_json']); }
  return sh;
}

function feedback_() {
  const v = feedbackSheet_().getDataRange().getValues(); v.shift();
  return v.filter(function (r) { return r[0]; }).map(function (r) {
    let it = {}; try { it = JSON.parse(r[4] || '{}'); } catch (e) {}
    return { id: String(r[0]), at: r[1] instanceof Date ? r[1].toISOString() : r[1], by: r[2], status: r[3], item: it };
  });
}

function notesSheet_() {
  const s = ss_();
  let sh = s.getSheetByName('notes');
  if (!sh) { sh = s.insertSheet('notes'); sh.appendRow(['id', 'at', 'by', 'board', 'url', 'title', 'note']); }
  return sh;
}

function notes_(board) {
  const v = notesSheet_().getDataRange().getValues(); v.shift();
  return v.filter(function (r) { return r[0] && (!board || r[3] === board); }).map(function (r) {
    return { id: String(r[0]), at: r[1] instanceof Date ? r[1].toISOString() : r[1], by: r[2], board: r[3], url: r[4], title: r[5], note: r[6] };
  });
}

function deleteRowsById_(sh, ids) {
  const want = {}; (ids || []).forEach(function (x) { want[String(x)] = 1; });
  const rows = sh.getDataRange().getValues(); let n = 0;
  for (let i = rows.length - 1; i >= 1; i--) { if (want[String(rows[i][0])]) { sh.deleteRow(i + 1); n++; } }
  return n;
}

function queueSheet_() {
  const s = ss_();
  let sh = s.getSheetByName('script_queue');
  if (!sh) { sh = s.insertSheet('script_queue'); sh.appendRow(['id', 'requested_at', 'by', 'status', 'item_json', 'done_at', 'note']); }
  return sh;
}

function queue_(all) {
  const v = queueSheet_().getDataRange().getValues(); v.shift();
  return v.map(function (r, i) {
    let it = {};
    try { it = JSON.parse(r[4] || '{}'); } catch (e) {}
    return { _row: i + 2, id: String(r[0]), requested_at: r[1] instanceof Date ? r[1].toISOString() : r[1], by: r[2], status: r[3], item: it };
  }).filter(function (q) { return q.id && (all || q.status === 'queued'); });
}

function saveScript_(id, script, by, model, item) {
  const sh = scriptsSheet_();
  const rows = sh.getDataRange().getValues();
  for (let i = rows.length - 1; i >= 1; i--) { if (String(rows[i][0]) === String(id)) sh.deleteRow(i + 1); }
  sh.appendRow([String(id), new Date().toISOString(), by || 'Claude', model || 'claude', JSON.stringify(script || {}), JSON.stringify(item || {}).slice(0, 40000)]);
  const q = queueSheet_();
  queue_(true).forEach(function (x) { if (x.id === String(id) && x.status === 'queued') q.getRange(x._row, 4, 1, 3).setValues([['done', new Date().toISOString(), '']]); });
}

function adsSheet_() {
  const s = ss_();
  let sh = s.getSheetByName('ads');
  if (!sh) { sh = s.insertSheet('ads'); sh.appendRow(['id', 'saved_at', 'handle', 'type', 'brand', 'product', 'category', 'price', 'discount', 'period', 'channel', 'contact', 'json']); }
  return sh;
}

function ads_() {
  const v = adsSheet_().getDataRange().getValues(); v.shift();
  return v.filter(function (r) { return r[0]; }).map(function (r) { let j = {}; try { j = JSON.parse(r[12] || '{}'); } catch (e) {} j.id = String(r[0]); j.saved_at = r[1] instanceof Date ? r[1].toISOString() : r[1]; return j; });
}

function doGet(e) {
  const a = (e && e.parameter && e.parameter.action) || 'all';
  if (a === 'channels') return out_({ ok: true, channels: channels_(false) });
  if (a === 'queue') return out_({ ok: true, queue: queue_(false).map(function (q) { return { id: q.id, by: q.by, requested_at: q.requested_at, item: q.item }; }) });
  if (a === 'guide') return out_({ ok: true, guide: GUIDE });
  if (a === 'ads') return out_({ ok: true, ads: ads_() });
  if (a === 'feedback') return out_({ ok: true, feedback: feedback_() });
  if (a === 'notes') return out_({ ok: true, notes: notes_(e.parameter.board || '') });
  return out_({ ok: true, channels: channels_(true), bookmarks: bookmarks_(), scripts: scripts_(), feedback: feedback_(),
    queued: queue_(false).map(function (q) { return { id: q.id, by: q.by, requested_at: q.requested_at }; }) });
}

function doPost(e) {
  let b = {};
  try { b = JSON.parse(e.postData.contents); } catch (err) { return out_({ ok: false, error: '요청 형식 오류' }); }
  const pass = props_().getProperty('PASS');
  if (!pass || b.pass !== pass) return out_({ ok: false, error: '팀 비밀번호가 맞지 않아요' });
  const lock = LockService.getScriptLock();
  lock.waitLock(15000);
  try {
    const now = new Date().toISOString();
    const by = String(b.by || '').slice(0, 30);
    const ch = sheet_('channels');
    if (b.action === 'addChannel') {
      const h = normHandle_(b.handle);
      if (!h) return out_({ ok: false, error: '인스타 프로필 링크나 @아이디를 넣어주세요' });
      const cat = CATS.indexOf(b.cat) >= 0 ? b.cat : CATS[0];
      const ex = rows_('channels').filter(function (r) { return String(r.handle).toLowerCase() === h; })[0];
      if (ex) {
        ch.getRange(ex._row, 2, 1, 7).setValues([[cat, b.note || ex.note || '', by, now, true, '', '']]);
      } else {
        ch.appendRow([h, cat, b.note || '', by, now, true, '', '']);
      }
      return out_({ ok: true, handle: h, channels: channels_(true) });
    }
    if (b.action === 'removeChannel' || b.action === 'restoreChannel') {
      const h = normHandle_(b.handle);
      const ex = rows_('channels').filter(function (r) { return String(r.handle).toLowerCase() === h; })[0];
      if (!ex) return out_({ ok: false, error: '목록에 없는 채널이에요' });
      if (b.action === 'removeChannel') ch.getRange(ex._row, 6, 1, 3).setValues([[false, by, now]]);
      else ch.getRange(ex._row, 6, 1, 3).setValues([[true, '', '']]);
      return out_({ ok: true, channels: channels_(true) });
    }
    if (b.action === 'seedChannels') {
      const have = {};
      rows_('channels').forEach(function (r) { have[String(r.handle).toLowerCase()] = 1; });
      const add = (b.channels || []).filter(function (c) { return c.handle && !have[c.handle.toLowerCase()]; })
        .map(function (c) { return [c.handle, c.cat, c.note || '', 'seed', now, true, '', '']; });
      if (add.length) ch.getRange(ch.getLastRow() + 1, 1, add.length, 8).setValues(add);
      return out_({ ok: true, added: add.length });
    }
    const bm = sheet_('bookmarks');
    if (b.action === 'addBookmark') {
      const ex = rows_('bookmarks').filter(function (r) { return String(r.id) === String(b.id); })[0];
      if (!ex) bm.appendRow([String(b.id), now, by, b.item && b.item.kind || '', JSON.stringify(b.item || {}).slice(0, 45000)]);
      return out_({ ok: true, bookmarks: bookmarks_() });
    }
    if (b.action === 'removeBookmark') {
      const ex = rows_('bookmarks').filter(function (r) { return String(r.id) === String(b.id); })[0];
      if (ex) bm.deleteRow(ex._row);
      return out_({ ok: true, bookmarks: bookmarks_() });
    }
    if (b.action === 'collectNow') {
      const tok = props_().getProperty('GH_TOKEN');
      if (!tok) return out_({ ok: false, error: '즉시 수집이 아직 설정되지 않았어요' });
      const r = UrlFetchApp.fetch('https://api.github.com/repos/' + REPO + '/actions/workflows/daily.yml/dispatches', {
        method: 'post', contentType: 'application/json', muteHttpExceptions: true,
        headers: { Authorization: 'Bearer ' + tok, Accept: 'application/vnd.github+json' },
        payload: JSON.stringify({ ref: 'main' })
      });
      return out_({ ok: r.getResponseCode() === 204, code: r.getResponseCode() });
    }
    if (b.action === 'requestScript') {
      const ex = queue_(false).filter(function (q) { return q.id === String(b.id); })[0];
      if (!ex) queueSheet_().appendRow([String(b.id), now, by, 'queued', JSON.stringify(b.item || {}).slice(0, 40000), '', String(b.item && b.item.extra || '')]);
      // 바로 Claude 작업 실행 (GitHub Actions claude-tasks, 요청 처리만)
      let kicked = false;
      const gtok = props_().getProperty('GH_TOKEN');
      if (gtok) {
        try {
          const rr = UrlFetchApp.fetch('https://api.github.com/repos/' + REPO + '/actions/workflows/claude.yml/dispatches', {
            method: 'post', contentType: 'application/json', muteHttpExceptions: true,
            headers: { Authorization: 'Bearer ' + gtok, Accept: 'application/vnd.github+json' },
            payload: JSON.stringify({ ref: 'main', inputs: { only: 'A' } })
          });
          kicked = rr.getResponseCode() === 204;
        } catch (err) {}
      }
      return out_({ ok: true, queued: true, kicked: kicked });
    }
    if (b.action === 'saveAds') {
      const sh = adsSheet_();
      const have = {};
      const vals = sh.getDataRange().getValues();
      for (let i = 1; i < vals.length; i++) have[String(vals[i][0])] = i + 1;
      (b.ads || []).forEach(function (a) {
        const row = [String(a.id), now, a.handle || '', a.type || '', a.brand || '', a.product || '', a.category || '', a.price || '', a.discount || '', a.period || '', a.channel || '', a.contact || '', JSON.stringify(a).slice(0, 20000)];
        if (have[String(a.id)]) sh.getRange(have[String(a.id)], 1, 1, row.length).setValues([row]); else sh.appendRow(row);
      });
      return out_({ ok: true, saved: (b.ads || []).length });
    }
    if (b.action === 'saveScript') {
      saveScript_(b.id, b.script, b.by || 'Claude', b.model || 'claude', b.item);
      return out_({ ok: true });
    }
    if (b.action === 'setFeedback') {  // status: '제외' | '제작함' | '' (해제)
      const sh = feedbackSheet_();
      deleteRowsById_(sh, [b.id]);
      if (b.status) sh.appendRow([String(b.id), now, by, String(b.status), JSON.stringify(b.item || {}).slice(0, 20000)]);
      return out_({ ok: true, feedback: feedback_() });
    }
    if (b.action === 'addNote') {
      const id = 'n' + Date.now();
      notesSheet_().appendRow([id, now, by, String(b.board || 'popcorn').slice(0, 30), String(b.url || '').slice(0, 1000), String(b.title || '').slice(0, 200), String(b.note || '').slice(0, 3000)]);
      return out_({ ok: true, notes: notes_(String(b.board || 'popcorn')) });
    }
    if (b.action === 'removeNote') {
      deleteRowsById_(notesSheet_(), [b.id]);
      return out_({ ok: true, notes: notes_(String(b.board || 'popcorn')) });
    }
    if (b.action === 'deleteAds') {
      return out_({ ok: true, deleted: deleteRowsById_(adsSheet_(), b.ids) });
    }
    if (b.action === 'deleteScripts') {
      return out_({ ok: true, deleted: deleteRowsById_(scriptsSheet_(), b.ids) });
    }
    if (b.action === 'setGhToken') {
      props_().setProperty('GH_TOKEN', String(b.token || ''));
      return out_({ ok: true });
    }
    return out_({ ok: false, error: '알 수 없는 요청' });
  } catch (err) {
    return out_({ ok: false, error: String(err.message || err) });
  } finally {
    try { lock.releaseLock(); } catch (e) {}
  }
}

/** 최초 1회 실행: 시트 생성 + 팀 비밀번호 설정 (편집기에서 실행) */
function setup() {
  const s = ss_();
  if (!props_().getProperty('PASS')) props_().setProperty('PASS', 'bs2026');
  Logger.log('Sheet: ' + s.getUrl());
}
