# -*- coding: utf-8 -*-
"""
================================================================
 KOVAS 데일리 트렌드 자동화  —  run.py
================================================================
 이 스크립트 하나가 매일 아래 3단계를 자동으로 처리합니다.

   [1단계] 수집  : 구글 뉴스 RSS에서 기초화장품/스킨케어 기사를 긁어옵니다
   [2단계] 요약  : Claude API로 '주요 이슈 5개 + 한 줄 정리'로 요약합니다
   [3단계] 저장  : 웹사이트가 읽을 수 있는 JSON 파일로 저장합니다
                   (data/2026-07-14.json 형태)

 실행 방법 (터미널에서):
     python run.py

 처음 쓰시는 거라면 같은 폴더의 README.md 를 먼저 읽어주세요.
================================================================
"""

import os
import re
import json
import html
from datetime import datetime, timezone, timedelta

import feedparser                     # 뉴스 RSS를 읽는 도구
from anthropic import Anthropic       # Claude API 도구
from dotenv import load_dotenv        # .env 파일에서 API 키를 안전하게 불러오는 도구


# ----------------------------------------------------------------
# [설정]  ★ 바꾸고 싶은 값은 대부분 여기 모여 있습니다 ★
# ----------------------------------------------------------------

# .env 파일에 적어둔 API 키를 불러옵니다 (README 참고)
load_dotenv()

# 검색할 키워드. 필요하면 추가/삭제하세요.
SEARCH_KEYWORDS = [
    "기초화장품",
    "스킨케어",
    "화장품 트렌드",
    "화장품 신제품",
    "K뷰티",
]

# 며칠 이내 기사만 가져올지 (기본: 최근 2일)
DAYS_BACK = 2

# 요약에 사용할 Claude 모델
#   - "claude-sonnet-5"            : 품질 좋고 저렴 (권장)
#   - "claude-haiku-4-5-20251001"  : 더 저렴하고 빠름 (양이 많을 때)
CLAUDE_MODEL = "claude-sonnet-5"

# 결과 JSON을 저장할 폴더 (웹사이트가 이 폴더를 읽습니다)
DATA_DIR = "data"

# 오늘 날짜 (한국 시간 기준)
KST = timezone(timedelta(hours=9))
TODAY = datetime.now(KST).strftime("%Y-%m-%d")


# ----------------------------------------------------------------
# [1단계] 기사 수집
# ----------------------------------------------------------------
def collect_articles():
    """구글 뉴스 RSS에서 키워드별 기사를 모아 중복을 제거해 돌려줍니다."""
    print(f"[1단계] 기사 수집 시작 (최근 {DAYS_BACK}일)")
    articles = []
    seen_titles = set()   # 중복 기사 제거용

    for keyword in SEARCH_KEYWORDS:
        # 구글 뉴스 RSS 주소. when:2d = 최근 2일치만.
        # 한글·띄어쓰기가 주소에 그대로 들어가면 오류가 나므로 quote로 안전하게 변환합니다.
        from urllib.parse import quote
        query = quote(f"{keyword} when:{DAYS_BACK}d")
        url = f"https://news.google.com/rss/search?q={query}&hl=ko&gl=KR&ceid=KR:ko"
        feed = feedparser.parse(url)

        for entry in feed.entries:
            title = entry.get("title", "").strip()

            # 제목 앞부분만 비교해 사실상 같은 기사면 건너뜁니다
            norm = re.sub(r"\s+", "", title)[:25]
            if not title or norm in seen_titles:
                continue
            seen_titles.add(norm)

            # RSS 요약에는 HTML 태그가 섞여 있어 깨끗하게 정리합니다
            raw_summary = entry.get("summary", "")
            clean_summary = html.unescape(re.sub(r"<[^>]+>", "", raw_summary)).strip()

            # 언론사 이름 (있으면)
            source = ""
            if entry.get("source") and entry.source.get("title"):
                source = entry.source.title

            articles.append({
                "title": title,
                "summary": clean_summary,
                "link": entry.get("link", ""),
                "source": source,
                "keyword": keyword,
            })

    print(f"   → 중복 제거 후 {len(articles)}건 수집 완료")
    return articles


# ----------------------------------------------------------------
# [2단계] AI 요약
# ----------------------------------------------------------------
def build_prompt(articles, date_str):
    """Claude에게 보낼 지시문을 만듭니다."""
    # 기사를 '제목 위주'로 압축해 정리합니다.
    # (제목만 넣으면 가벼워서 200건 넘게 넣어도 한도에 안 걸리고,
    #  중복 사건은 AI가 알아서 하나로 묶으면서 '많이 보도된 = 중요한' 신호로 활용합니다)
    lines = []
    for i, a in enumerate(articles, 1):
        lines.append(f"{i}. [{a['source']}] {a['title']}")
    article_block = "\n".join(lines)

    # ↓↓↓ 요약 스타일을 바꾸고 싶으면 이 지시문을 수정하세요 ↓↓↓
    return f"""당신은 화장품 ODM 기업 KOVAS의 트렌드 애널리스트입니다.
아래는 {date_str}에 수집된 기초화장품·스킨케어 관련 뉴스 기사 제목 목록입니다.
각 기사 앞에는 번호가 붙어 있습니다. 같은 사건을 여러 매체가 다뤄 비슷한 제목이 여러 개 섞여 있습니다.

이 기사들을 분석해서, 오늘의 트렌드 리포트를 작성하세요. 규칙:
- 비슷한 내용의 기사는 하나의 '주요 이슈'로 묶으세요. 여러 매체가 반복해서 다룬 사건일수록 더 중요한 이슈이므로 우선 다루세요.
- 이렇게 묶어 '주요 이슈'를 5~8개로 정리하세요.
- 각 이슈마다 시장·기획 관점의 시사점을 1~2개 제시하세요.
- 각 이슈에는 그 이슈를 가장 잘 대표하는 기사 1개의 '번호'를 sourceNo에 넣으세요. (반드시 위 목록에 있는 번호)
- 주요 이슈에 묶이지 않았지만 참고할 만한 개별 기사 10~15개를 골라 그 '번호'를 briefNos에 넣으세요. 서로 다른 주제로 다양하게 고르세요(특정 원료·니치 채널·규제·작은 브랜드 소식 등).
- 마지막에 전체를 관통하는 '한 줄 정리'와 해시태그 3개를 만드세요.
- 사실에 근거해 작성하고, 기사에 없는 내용을 지어내지 마세요.

반드시 아래 JSON 형식으로만 답하세요. 다른 설명, 인사말, 코드블록 표시(```)는 절대 붙이지 마세요.
링크(URL)는 절대 직접 쓰지 말고, 오직 기사 '번호'만 사용하세요.

{{
  "issues": [
    {{
      "no": 1,
      "title": "이슈 제목",
      "body": "이슈 요약 (2~3문장)",
      "implications": ["시사점1", "시사점2"],
      "sourceNo": 12
    }}
  ],
  "briefNos": [3, 47, 88, 102, 150],
  "oneLiner": "오늘의 트렌드를 한 문장으로 정리",
  "hashtags": ["키워드1", "키워드2", "키워드3"]
}}

--- 기사 제목 목록 ---
{article_block}
"""


def summarize(articles, date_str):
    """Claude API를 호출해 기사들을 리포트 JSON으로 요약합니다."""
    print("[2단계] AI 요약 시작")

    client = Anthropic()  # API 키는 .env 의 ANTHROPIC_API_KEY 에서 자동으로 읽습니다

    message = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=8000,
        messages=[
            {"role": "user", "content": build_prompt(articles, date_str)}
        ],
    )

    # Claude의 답변에서 '텍스트' 블록만 골라 합칩니다.
    # (모델이 생각(thinking) 블록을 함께 반환할 수 있어, text 타입만 추립니다)
    text = "".join(
        b.text for b in message.content if getattr(b, "type", None) == "text"
    ).strip()

    # 혹시 코드블록 표시가 붙어 오면 제거 (안전장치)
    text = re.sub(r"^```(json)?", "", text).strip()
    text = re.sub(r"```$", "", text).strip()

    # 답변이 중간에 잘렸는지 확인 (잘리면 JSON이 }로 안 끝남)
    if message.stop_reason == "max_tokens" or not text.endswith("}"):
        raise RuntimeError(
            "AI 답변이 잘렸습니다. run.py 상단 근처의 max_tokens 값을 더 키우거나, "
            "기사 수를 줄여보세요. (현재 응답 끝부분: ..." + text[-80:] + ")"
        )

    report = json.loads(text)   # 텍스트 → 파이썬 데이터로 변환

    # AI가 고른 기사 '번호'를 실제 링크로 정확히 변환합니다.
    # (AI가 URL을 직접 쓰면 틀릴 수 있어, 번호만 받아 원본 기사에서 링크를 찾아 연결)
    def article_by_no(n):
        if isinstance(n, int) and 1 <= n <= len(articles):
            return articles[n - 1]
        return None

    # 주요 이슈: sourceNo → 대표 기사 링크
    for it in report.get("issues", []):
        a = article_by_no(it.pop("sourceNo", None))
        it["source"] = a["link"] if a else ""

    # 기타 단신: briefNos → 개별 기사(제목+링크) 목록
    briefs = []
    for n in report.pop("briefNos", []):
        a = article_by_no(n)
        if a:
            briefs.append({"title": a["title"], "link": a["link"], "source": a["source"]})
    report["briefs"] = briefs

    # 웹사이트가 기대하는 나머지 항목을 채워줍니다
    report["weekLabel"] = week_label(date_str)
    report["rankings"] = {}     # 랭킹(올리브영 지수)은 뉴스로 못 만들어 비워둡니다 (README 참고)

    print(f"   → 주요 이슈 {len(report.get('issues', []))}건 + 기타 단신 {len(briefs)}건 요약 완료")
    return report


def week_label(date_str):
    """'2026-07-14' → '7월 3주차' 같은 라벨을 만듭니다."""
    d = datetime.strptime(date_str, "%Y-%m-%d")
    week_of_month = (d.day - 1) // 7 + 1
    return f"{d.month}월 {week_of_month}주차"


# ----------------------------------------------------------------
# [3단계] 저장
# ----------------------------------------------------------------
def save(date_str, report):
    """리포트를 날짜별 JSON으로 저장하고, 날짜 목록(index.json)도 갱신합니다."""
    print("[3단계] 저장")
    os.makedirs(DATA_DIR, exist_ok=True)

    # (1) 그날의 리포트 파일
    day_path = os.path.join(DATA_DIR, f"{date_str}.json")
    with open(day_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"   → {day_path} 저장 완료")

    # (2) 리포트가 있는 날짜 목록 (웹 캘린더가 이걸 보고 어떤 날에 점을 찍을지 압니다)
    index_path = os.path.join(DATA_DIR, "index.json")
    dates = []
    if os.path.exists(index_path):
        with open(index_path, encoding="utf-8") as f:
            dates = json.load(f)
    if date_str not in dates:
        dates.append(date_str)
        dates.sort()
    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(dates, f, ensure_ascii=False, indent=2)
    print(f"   → index.json 갱신 (총 {len(dates)}일치 보관 중)")


# ----------------------------------------------------------------
# 전체 실행
# ----------------------------------------------------------------
def main():
    print(f"===== KOVAS 데일리 트렌드 자동화 시작 ({TODAY}) =====")

    articles = collect_articles()
    if not articles:
        print("수집된 기사가 없습니다. 키워드나 날짜 범위를 확인하세요. 종료합니다.")
        return

    report = summarize(articles, TODAY)
    save(TODAY, report)

    print("===== 완료! =====")


if __name__ == "__main__":
    main()
