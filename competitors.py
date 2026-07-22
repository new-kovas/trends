# -*- coding: utf-8 -*-
"""
================================================================
 KOVAS 경쟁사 동향 수집  —  competitors.py
================================================================
 추적 업체들의 '주요 경영사항' 공시(DART) + 관련 뉴스를 모아
 data/competitors.json 으로 저장합니다. (웹 경쟁사 섹션이 읽음)

 실행:  python competitors.py
 필요 :  .env 에 DART_API_KEY=발급받은40자키

 * DART에 상장/공시 대상이 아닌 회사는 공시가 없을 수 있습니다.
   그런 회사는 공시 없이 뉴스만 수집됩니다(정상).
================================================================
"""

import os
import re
import io
import json
import zipfile
import html
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta

import feedparser
from dotenv import load_dotenv

# ----------------------------------------------------------------
# [설정]
# ----------------------------------------------------------------
load_dotenv()
DART_KEY = os.environ.get("DART_API_KEY", "").strip()

# 추적할 경쟁사 (웹의 '추적 중인 업체'와 동일하게 유지)
TRACKED = [
    "한국콜마", "코스맥스", "코스메카코리아", "씨앤씨인터내셔널",
    "잉글우드랩", "제닉", "그린코스", "씨엔에프", "씨엔텍", "이미인", "엔코스",
]

# 제외할 공시(잡공시). 이 단어가 제목에 있으면 걸러냅니다.
# ※ '주요 경영사항만 골라내기'보다, '잡공시만 빼기'가 놓치는 게 적습니다.
EXCLUDE = [
    "주주총회소집", "임원ㆍ주요주주", "임원·주요주주", "특정증권등소유상황",
    "대량보유상황보고", "주식등의대량보유", "사업보고서", "분기보고서", "반기보고서",
    "감사보고서", "정정신고", "기업설명회", "IR", "결산실적공시예고",
    "최대주주변경", "주식명의개서", "전자증권",
]

DAYS_BACK = 90          # 최근 며칠 공시 (공시는 뜸해서 넉넉히)
NEWS_DAYS = 30          # 뉴스는 최근 30일
NEWS_PER_COMPANY = 2    # 회사당 뉴스 최대 건수
MAX_ITEMS = 60          # 피드에 실을 최대 건수

DATA_DIR = "data"
KST = timezone(timedelta(hours=9))
CORP_CACHE = "corpcodes.xml"   # 회사코드 파일(한번 받아 재사용)


# ----------------------------------------------------------------
# DART: 전체 회사코드 → {회사명: 고유번호}
# ----------------------------------------------------------------
def get_corp_map():
    """DART 회사코드 파일을 받아(또는 캐시에서 읽어) 이름→코드 사전을 만듭니다."""
    if not os.path.exists(CORP_CACHE):
        url = f"https://opendart.fss.or.kr/api/corpCode.xml?crtfc_key={DART_KEY}"
        print("   DART 회사코드 내려받는 중...")
        raw = urllib.request.urlopen(url, timeout=30).read()
        # 응답은 zip. 안에 CORPCODE.xml 이 들어 있음
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            with z.open(z.namelist()[0]) as f:
                data = f.read()
        with open(CORP_CACHE, "wb") as f:
            f.write(data)
    else:
        data = open(CORP_CACHE, "rb").read()

    root = ET.fromstring(data)
    corp_map = {}
    for item in root.iter("list"):
        name = (item.findtext("corp_name") or "").strip()
        code = (item.findtext("corp_code") or "").strip()
        if name and code:
            corp_map[name] = code
    return corp_map


def find_code(corp_map, company):
    """회사명으로 고유번호 찾기 (정확히 → 부분일치 순)."""
    if company in corp_map:
        return corp_map[company]
    for name, code in corp_map.items():
        if company in name:
            return code
    return None


# ----------------------------------------------------------------
# DART: 회사별 주요 경영사항 공시
# ----------------------------------------------------------------
def fetch_disclosures(corp_code, company):
    """최근 공시 목록에서 주요 경영사항 키워드가 든 것만 골라 돌려줍니다."""
    end = datetime.now(KST)
    bgn = end - timedelta(days=DAYS_BACK)
    params = urllib.parse.urlencode({
        "crtfc_key": DART_KEY,
        "corp_code": corp_code,
        "bgn_de": bgn.strftime("%Y%m%d"),
        "end_de": end.strftime("%Y%m%d"),
        "page_count": 100,
    })
    url = f"https://opendart.fss.or.kr/api/list.json?{params}"
    try:
        res = urllib.request.urlopen(url, timeout=20).read()
        data = json.loads(res)
    except Exception as e:
        print(f"   [{company}] 공시 조회 실패:", e)
        return []

    if data.get("status") != "000":
        # 013=데이터없음 은 정상(공시 없음)
        if data.get("status") not in ("013",):
            print(f"   [{company}] DART 메시지:", data.get("status"), data.get("message"))
        return []

    raw = data.get("list", [])
    out = []
    for it in raw:
        title = (it.get("report_nm") or "").strip()
        if any(k in title for k in EXCLUDE):
            continue   # 잡공시 제외
        dt = it.get("rcept_dt", "")
        out.append({
            "company": company,
            "type": "disclosure",
            "title": title,
            "date": f"{dt[:4]}-{dt[4:6]}-{dt[6:8]}" if len(dt) == 8 else dt,
            "link": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={it.get('rcept_no','')}",
        })
    # 진단용: 전체 몇 건 중 몇 건이 남았는지
    print(f"      공시 {len(raw)}건 중 {len(out)}건 채택")
    return out


# ----------------------------------------------------------------
# 뉴스: 회사별 최근 기사
# ----------------------------------------------------------------
def fetch_news(company):
    q = urllib.parse.quote(f"{company} 화장품 when:{NEWS_DAYS}d")
    url = f"https://news.google.com/rss/search?q={q}&hl=ko&gl=KR&ceid=KR:ko"
    out = []
    try:
        feed = feedparser.parse(url)
    except Exception as e:
        print(f"   [{company}] 뉴스 조회 실패:", e)
        return []
    for entry in feed.entries[:NEWS_PER_COMPANY]:
        title = (entry.get("title") or "").strip()
        if not title:
            continue
        # published 날짜
        d = ""
        if entry.get("published_parsed"):
            t = entry.published_parsed
            d = f"{t.tm_year}-{t.tm_mon:02d}-{t.tm_mday:02d}"
        out.append({
            "company": company,
            "type": "news",
            "title": html.unescape(title),
            "date": d,
            "link": entry.get("link", ""),
        })
    return out


# ----------------------------------------------------------------
# 전체 실행
# ----------------------------------------------------------------
def main():
    print("===== 경쟁사 동향 수집 시작 =====")
    if not DART_KEY:
        print("DART_API_KEY 가 없습니다. .env(또는 GitHub Secrets)에 넣어주세요. 종료합니다.")
        return

    corp_map = get_corp_map()
    print(f"   회사코드 {len(corp_map):,}건 로드")

    items = []
    for company in TRACKED:
        code = find_code(corp_map, company)
        if code:
            items += fetch_disclosures(code, company)
        items += fetch_news(company)
        print(f"   [{company}] 코드 {'있음' if code else '없음(뉴스만)'}")

    # 날짜 최신순 정렬 후 상한
    items.sort(key=lambda x: x.get("date", ""), reverse=True)
    items = items[:MAX_ITEMS]

    os.makedirs(DATA_DIR, exist_ok=True)
    path = os.path.join(DATA_DIR, "competitors.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"updated": datetime.now(KST).strftime("%Y-%m-%d"), "items": items},
                  f, ensure_ascii=False, indent=2)
    print(f"   → {path} 저장 ({len(items)}건)")
    print("===== 완료 =====")


if __name__ == "__main__":
    main()
