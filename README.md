# 데일리 큐레이션 보드

매일 07:00(KST)에 GitHub Actions가 맘카페 주간 인기글과 인스타그램 벤치마크 계정의 최근 게시물을 모아 `site/data.json`을 갱신하고 GitHub Pages로 배포합니다.

- 채널 추가/삭제: `channels.json`
- 수집 스크립트: `scripts/collect.py` (표준 라이브러리만 사용)
- 인스타 수집: 저장소 Secrets `IG_TOKEN`, `IG_USER_ID`가 있으면 Meta 공식 API(Business Discovery)로 수집
