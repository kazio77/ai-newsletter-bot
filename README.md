# AI Daily Newsletter Bot

매일 아침 AI 뉴스를 자동 수집하여 한글로 요약 후 텔레그램으로 발송하는 봇입니다.

## 동작 방식

1. **뉴스 수집** - 13개 소스에서 RSS/스크래핑으로 기사 수집
2. **중복 제거** - TF-IDF + AgglomerativeClustering으로 유사 기사 클러스터링
3. **AI 요약** - Claude API로 한글 뉴스레터 생성
4. **텔레그램 발송** - HTML 포맷으로 텔레그램 메시지 전송

## 뉴스 소스

| 카테고리 | 소스 |
|---------|------|
| 해외 테크미디어 | TechCrunch AI, The Verge AI, Ars Technica |
| AI 뉴스레터 | TLDR AI, The Batch (Andrew Ng), Ben's Bites |
| 논문/연구 | ArXiv AI/ML |
| 빅테크 블로그 | OpenAI, Anthropic, Google AI, Meta AI |
| 국내 | GeekNews, AI타임스, 테크M |

## 설정 방법

### 1. GitHub Secrets 등록

레포 Settings > Secrets and variables > Actions에서 추가:

| Secret | 설명 |
|--------|------|
| TELEGRAM_BOT_TOKEN | 텔레그램 BotFather에서 발급받은 토큰 |
| TELEGRAM_CHAT_ID | 메시지를 받을 채팅 ID |
| ANTHROPIC_API_KEY | Anthropic API 키 |

### 2. 자동 실행

GitHub Actions가 매일 KST 오전 7시 (UTC 22:00)에 자동 실행됩니다.
수동 실행은 Actions 탭 > Run workflow 버튼으로 가능합니다.

### 3. 로컬 테스트

```bash
cp .env.example .env
pip install -r scripts/requirements.txt
python scripts/newsletter.py
```

## 커스터마이징

- 뉴스 소스 추가/삭제: scripts/config.json 수정
- 발송 시간 변경: .github/workflows/newsletter.yml의 cron 수정

## 비용

- GitHub Actions: Public 레포 무료
- Claude API: 하루 약 $0.01~0.05 (월 $1~2 수준)
