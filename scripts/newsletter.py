#!/usr/bin/env python3
"""
AI Daily Newsletter Bot
매일 아침 AI 뉴스를 수집 → 중복 제거 → 한글 요약 → 텔레그램 발송
"""

import os
import json
import time
import re
import feedparser
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

load_dotenv()

# ─── 환경변수 ───
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

KST = timezone(timedelta(hours=9))
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(SCRIPT_DIR, "config.json")


# ═══════════════════════════════════════════
# 1. 뉴스 수집
# ═══════════════════════════════════════════

def collect_rss(source, hours=28):
    """RSS 피드에서 최근 기사 수집. hours를 28로 넉넉하게 설정해 누락 방지."""
    try:
        feed = feedparser.parse(source["url"], request_headers={
            "User-Agent": "AI-Newsletter-Bot/1.0"
        })
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        articles = []

        for entry in feed.entries[:30]:  # 최대 30개
            # 발행일 파싱
            published = None
            if hasattr(entry, "published_parsed") and entry.published_parsed:
                published = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
            elif hasattr(entry, "updated_parsed") and entry.updated_parsed:
                published = datetime(*entry.updated_parsed[:6], tzinfo=timezone.utc)

            if published and published < cutoff:
                continue

            summary = ""
            if hasattr(entry, "summary"):
                summary = BeautifulSoup(entry.summary, "html.parser").get_text()[:500]

            articles.append({
                "title": entry.get("title", "제목 없음"),
                "link": entry.get("link", ""),
                "summary": summary,
                "source": source["name"],
                "published": published.isoformat() if published else ""
            })

        # AI 키워드 필터가 설정된 경우
        if source.get("filter_keywords"):
            keywords = [kw.lower() for kw in source["filter_keywords"]]
            articles = [
                a for a in articles
                if any(kw in a["title"].lower() or kw in a["summary"].lower() for kw in keywords)
            ]

        return articles
    except Exception as e:
        print(f"  ⚠️ RSS 수집 실패 [{source['name']}]: {e}")
        return []


def scrape_web(source, hours=28):
    """웹 스크래핑으로 기사 수집."""
    try:
        headers = {"User-Agent": "Mozilla/5.0 (compatible; AI-Newsletter-Bot/1.0)"}
        resp = requests.get(source["url"], headers=headers, timeout=15)
        resp.raise_for_status()
        resp.encoding = resp.apparent_encoding or "utf-8"
        soup = BeautifulSoup(resp.text, "lxml")

        articles = []
        selectors = source.get("selectors", {})
        items = soup.select(selectors.get("item", "article"))[:20]

        for item in items:
            title_el = item.select_one(selectors.get("title", "h2, h3, .title"))
            link_el = item.select_one(selectors.get("link", "a"))
            summary_el = item.select_one(selectors.get("summary", "p, .summary, .desc"))

            title = title_el.get_text(strip=True) if title_el else ""
            link = ""
            if link_el:
                link = link_el.get("href", "")
                if link and not link.startswith("http"):
                    from urllib.parse import urljoin
                    link = urljoin(source["url"], link)

            summary = summary_el.get_text(strip=True)[:500] if summary_el else ""

            if title:
                articles.append({
                    "title": title,
                    "link": link,
                    "summary": summary,
                    "source": source["name"],
                    "published": ""
                })

        # AI 키워드 필터
        if source.get("filter_keywords"):
            keywords = [kw.lower() for kw in source["filter_keywords"]]
            articles = [
                a for a in articles
                if any(kw in a["title"].lower() or kw in a["summary"].lower() for kw in keywords)
            ]

        return articles
    except Exception as e:
        print(f"  ⚠️ 웹 스크래핑 실패 [{source['name']}]: {e}")
        return []


def collect_arxiv(source, hours=28):
    """ArXiv API로 최신 AI 논문 수집."""
    try:
        resp = requests.get(source["url"], timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "xml")
        entries = soup.find_all("entry")[:15]

        articles = []
        for entry in entries:
            title = entry.find("title").text.strip().replace("\n", " ")
            link = entry.find("id").text.strip()
            summary = entry.find("summary").text.strip()[:400].replace("\n", " ")

            articles.append({
                "title": title,
                "link": link,
                "summary": summary,
                "source": source["name"],
                "published": entry.find("published").text if entry.find("published") else ""
            })

        return articles
    except Exception as e:
        print(f"  ⚠️ ArXiv 수집 실패: {e}")
        return []


def collect_all(config):
    """모든 소스에서 기사 수집."""
    all_articles = []
    for source in config["sources"]:
        print(f"📡 수집 중: {source['name']}...")
        time.sleep(1)  # 예의 바른 크롤링

        if source["type"] == "rss":
            articles = collect_rss(source)
        elif source["type"] == "arxiv":
            articles = collect_arxiv(source)
        elif source["type"] == "scrape":
            articles = scrape_web(source)
        else:
            print(f"  ⚠️ 알 수 없는 타입: {source['type']}")
            continue

        print(f"  ✅ {len(articles)}건 수집됨")
        all_articles.extend(articles)

    return all_articles


# ═══════════════════════════════════════════
# 2. 중복 제거 & 클러스터링
# ═══════════════════════════════════════════

def cluster_articles(articles, threshold=0.6):
    """
    TF-IDF 유사도 기반 기사 클러스터링.
    같은 이슈를 다루는 기사들을 하나의 토픽으로 묶는다.
    """
    if len(articles) <= 1:
        return {0: articles} if articles else {}

    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.cluster import AgglomerativeClustering
        import numpy as np

        texts = [f"{a['title']} {a['summary']}" for a in articles]

        tfidf = TfidfVectorizer(
            max_features=5000,
            stop_words="english",
            min_df=1,
            max_df=0.95
        )
        matrix = tfidf.fit_transform(texts)

        if matrix.shape[0] < 2:
            return {0: articles}

        clustering = AgglomerativeClustering(
            n_clusters=None,
            distance_threshold=threshold,
            metric="cosine",
            linkage="average"
        )
        labels = clustering.fit_predict(matrix.toarray())

        clusters = {}
        for idx, label in enumerate(labels):
            clusters.setdefault(int(label), []).append(articles[idx])

        # 클러스터 크기순 정렬 (중요도 대리 지표)
        sorted_clusters = dict(
            sorted(clusters.items(), key=lambda x: len(x[1]), reverse=True)
        )
        return sorted_clusters

    except ImportError:
        # sklearn이 없으면 단순 중복 제거만 수행
        print("⚠️ scikit-learn 미설치, 클러스터링 스킵")
        seen_titles = set()
        unique = []
        for a in articles:
            normalized = re.sub(r'\s+', ' ', a['title'].lower().strip())
            if normalized not in seen_titles:
                seen_titles.add(normalized)
                unique.append(a)
        return {i: [a] for i, a in enumerate(unique)}


# ═══════════════════════════════════════════
# 3. Claude API 요약
# ═══════════════════════════════════════════

def summarize_with_claude(clusters):
    """클러스터링된 뉴스를 Claude API로 한글 요약."""
    
    cluster_texts = []
    for cluster_id, articles in clusters.items():
        sources_text = "\n".join([
            f"- [{a['source']}] {a['title']}\n  요약: {a['summary'][:200]}\n  링크: {a['link']}"
            for a in articles
        ])
        cluster_texts.append(f"[토픽 {cluster_id + 1} - {len(articles)}개 기사]\n{sources_text}")

    all_clusters = "\n\n---\n\n".join(cluster_texts)
    today = datetime.now(KST).strftime("%Y년 %m월 %d일")

    prompt = f"""아래는 {today} 기준 최근 AI 관련 뉴스를 토픽별로 클러스터링한 결과입니다.
이것을 한글 텔레그램 뉴스레터로 만들어주세요.

## 형식 (텔레그램 HTML 형식 사용)

### PART 1: <b>🔥 3분 AI 브리핑</b>
- 가장 중요한 5-8개 토픽을 한 줄씩 요약
- 각 줄: 이모지 + <b>핵심 키워드</b> + 설명 (1-2문장)
- 실용적 꿀팁은 💡로 강조

### PART 2: <b>📰 상세 리포트</b>
- 토픽별로 제목 + 3-5문장 상세 설명
- 왜 중요한지, 실무 영향 포함
- 각 토픽의 꿀팁/실용 인사이트는 ⭐ 표시
- 각 토픽 끝에 <a href="URL">출처명</a> 형태로 링크

### 규칙
- 텔레그램 HTML: <b>, <i>, <a href=""> 태그만 사용 (마크다운 아님!)
- 친근하지만 전문적인 톤
- 중복 내용은 하나로 통합하되, 각 소스의 고유한 인사이트는 보존
- 논문은 실무 적용 관점에서 설명

---

{all_clusters}"""

    headers = {
        "Content-Type": "application/json",
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01"
    }

    data = {
        "model": "claude-3-5-sonnet-20241022",
        "max_tokens": 4000,
        "messages": [{"role": "user", "content": prompt}]
    }

    resp = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers=headers,
        json=data,
        timeout=120
    )

    if not resp.ok:
        print(f"  ❌ Claude API 에러 [{resp.status_code}]: {resp.text}")
    resp.raise_for_status()
    result = resp.json()

    return result["content"][0]["text"]


# ═══════════════════════════════════════════
# 4. 텔레그램 발송
# ═══════════════════════════════════════════

def split_message(text, max_len=4000):
    """텔레그램 메시지 길이 제한 대응. 문단 단위로 분할."""
    if len(text) <= max_len:
        return [text]

    parts = []
    current = ""
    paragraphs = text.split("\n\n")

    for para in paragraphs:
        if len(current) + len(para) + 2 > max_len:
            if current:
                parts.append(current.strip())
            current = para
        else:
            current = current + "\n\n" + para if current else para

    if current.strip():
        parts.append(current.strip())

    return parts


def send_telegram(message):
    """텔레그램으로 메시지 발송."""
    parts = split_message(message)
    print(f"📤 텔레그램 발송: {len(parts)}개 메시지")

    for i, part in enumerate(parts):
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": part,
            "parse_mode": "HTML",
            "disable_web_page_preview": True
        }
        resp = requests.post(url, json=payload, timeout=30)
        if resp.ok:
            print(f"  ✅ 파트 {i+1}/{len(parts)} 발송 완료")
        else:
            print(f"  ❌ 파트 {i+1} 실패: {resp.text}")
            # parse_mode 문제 시 plain text로 재시도
            payload["parse_mode"] = None
            requests.post(url, json=payload, timeout=30)

        if i < len(parts) - 1:
            time.sleep(1)


# ═══════════════════════════════════════════
# 5. 메인 실행
# ═══════════════════════════════════════════

def load_config():
    """config.json 로드."""
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def main():
    print(f"{'='*50}")
    print(f"🤖 AI Daily Newsletter Bot")
    print(f"📅 {datetime.now(KST).strftime('%Y-%m-%d %H:%M KST')}")
    print(f"{'='*50}\n")

    # 환경변수 체크
    if not all([TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, ANTHROPIC_API_KEY]):
        print("❌ 환경변수가 설정되지 않았습니다.")
        print("필요: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, ANTHROPIC_API_KEY")
        return

    # 설정 로드
    config = load_config()

    # 1. 수집
    print("📡 Step 1: 뉴스 수집")
    all_articles = collect_all(config)
    print(f"\n📊 총 {len(all_articles)}건 수집 완료\n")

    if not all_articles:
        send_telegram("⚠️ 오늘은 수집된 AI 뉴스가 없습니다. 소스를 확인해주세요.")
        return

    # 2. 클러스터링
    print("🔄 Step 2: 중복 제거 & 클러스터링")
    clusters = cluster_articles(all_articles)
    print(f"📊 {len(clusters)}개 토픽으로 분류 완료\n")

    # 3. Claude 요약
    print("🧠 Step 3: Claude API 요약 생성")
    newsletter = summarize_with_claude(clusters)
    print("✅ 요약 생성 완료\n")

    # 4. 헤더 + 발송
    today = datetime.now(KST).strftime("%Y.%m.%d %A")
    header = (
        f"🤖 <b>AI 데일리 브리핑</b> | {today}\n"
        f"{'━'*25}\n\n"
    )
    footer = (
        f"\n\n{'━'*25}\n"
        f"📊 수집: {len(all_articles)}건 → {len(clusters)}개 토픽\n"
        f"🤖 Powered by Claude AI"
    )

    print("📤 Step 4: 텔레그램 발송")
    send_telegram(header + newsletter + footer)

    print(f"\n{'='*50}")
    print("✅ 뉴스레터 발송 완료!")
    print(f"{'='*50}")


if __name__ == "__main__":
    main()
