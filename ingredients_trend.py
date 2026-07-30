# -*- coding: utf-8 -*-
"""
================================================================
 KOVAS 성분 트렌드 집계  —  ingredients_trend.py
================================================================
 매일 수집된 뉴스(data/YYYY-MM-DD.json)에서 관심 성분이 몇 번
 언급됐는지 세어, 7일 이동평균과 함께 data/ingredient_trend.json 에
 누적 저장합니다. (웹 '성분 트렌드' 섹션이 읽음)

 실행 :  python ingredients_trend.py
 특징 :
   - 기본 성분(SEED)은 처음부터 추적.
   - 후보 사전(CANDIDATES)에 있는 성분이 뉴스에 처음 등장하면
     그날부터 자동으로 추적 목록에 편입(노이즈 방지: 사전에 있는 것만).
   - 이미 집계한 날짜는 건너뜀(누적). 매일 새 날짜만 추가.
   - 과거 소급 불가(뉴스가 없던 날은 셀 수 없음) → 오늘부터 곡선이 쌓임.
================================================================
"""

import os
import re
import json
import glob
from datetime import datetime, timezone, timedelta

# ----------------------------------------------------------------
# [1] 기본 추적 성분 (처음부터 추적) — 필요시 자유롭게 가감하세요
# ----------------------------------------------------------------
SEED = [
    "레티놀", "나이아신아마이드", "병풀", "PDRN", "히알루론산",
    "세라마이드", "비타민C", "콜라겐", "펩타이드", "아젤라익애씨드",
    "살리실산", "갈락토미세스", "프로바이오틱스", "엑소좀", "스쿠알란",
    "판테놀", "트라넥사믹애씨드", "아데노신",
    "코엔자임Q10", "레티날", "바쿠치올", "티트리", "마이크로바이옴",
]

# ----------------------------------------------------------------
# [2] 후보 사전 (뉴스에 '처음' 등장하면 자동 편입) — 여기에 한 줄씩 추가하면
#     다음 실행부터 자동 추적됩니다. (사전에 없는 임의 단어는 넣지 않음 = 노이즈 방지)
# ----------------------------------------------------------------
CANDIDATES = [
    "아쿠아포린", "리포좀", "글루타치온",
    "성장인자", "EGF", "FGF", "달팽이점액", "뮤신",
    "프로폴리스", "카페인", "아르부틴",
    "코직산", "글리콜산", "락틱애씨드", "만델산", "PHA", "AHA", "BHA",
    "스핑고지질", "판테인", "알란토인",
    "페룰산", "레스베라트롤",
    "아줄렌", "알로에", "폴리글루타믹애씨드", "베타글루칸",
    "타우린", "에르고티오네인", "폴리페놀", "프로테오글리칸",
    "포스트바이오틱스",
    "세라마이드엔피", "달팽이뮤신",
]

# 성분명 → 별칭(같은 성분의 다른 표기). 한쪽으로 합쳐 셉니다.
ALIAS = {
    "병풀": ["시카", "센텔라아시아티카", "센텔라", "마데카소사이드", "마데카식애씨드", "아시아티코사이드"],
    "비타민C": ["아스코르빈산", "아스코빅애씨드", "비타민씨", "아스코빌"],
    "PDRN": ["연어주사", "폴리뉴클레오타이드", "폴리뉴클레오티드"],
    "레티놀": ["레티노이드"],
    "프로바이오틱스": ["유산균"],
    "마이크로바이옴": ["포스트바이오틱스"],
}

DATA_DIR = "data"
KST = timezone(timedelta(hours=9))
OUT = os.path.join(DATA_DIR, "ingredient_trend.json")


# 별칭으로 흡수되는 단어들(대표가 아닌 쪽) — 추적 목록에서 자동 제외
ALIAS_MEMBERS = set()
for _rep, _alist in {}.items():
    pass

def alias_members():
    s = set()
    for rep, alist in ALIAS.items():
        for a in alist:
            if a != rep:
                s.add(a)
    return s


def norm(s):
    """비교용 정규화: 공백·가운뎃점·하이픈 제거, 소문자."""
    return re.sub(r"[\s·\-]", "", (s or "")).lower()


def daily_text(report):
    """하루 뉴스 JSON에서 성분을 셀 텍스트를 모읍니다(이슈 제목·본문·시사점·한줄·단신 제목)."""
    parts = []
    for it in report.get("issues", []):
        parts.append(it.get("title", ""))
        parts.append(it.get("body", ""))
        parts += it.get("implications", []) or []
    parts.append(report.get("oneLiner", ""))
    for b in report.get("briefs", []):
        parts.append(b.get("title", ""))
    return norm(" ".join(parts))


def count_in(text_norm, name):
    """성분(별칭 포함)이 텍스트에 등장한 횟수."""
    names = [name] + ALIAS.get(name, [])
    total = 0
    for n in names:
        nn = norm(n)
        if not nn:
            continue
        total += text_norm.count(nn)
    return total


def main():
    print("===== 성분 트렌드 집계 시작 =====")
    if not os.path.isdir(DATA_DIR):
        print(f"{DATA_DIR} 폴더가 없습니다. run.py를 먼저 실행하세요.")
        return

    # 기존 누적 로드
    store = {"updated": "", "tracked": list(SEED), "daily": {}}
    if os.path.exists(OUT):
        try:
            store = json.load(open(OUT, encoding="utf-8"))
            store.setdefault("tracked", list(SEED))
            store.setdefault("daily", {})
        except Exception:
            pass

    # 아직 집계 안 한 날짜의 뉴스 파일 찾기
    files = sorted(glob.glob(os.path.join(DATA_DIR, "20*-*-*.json")))
    members = alias_members()
    tracked = (set(store["tracked"]) | set(SEED)) - members

    for path in files:
        date = os.path.basename(path)[:-5]  # YYYY-MM-DD
        if date in store["daily"]:
            continue  # 이미 집계한 날
        try:
            report = json.load(open(path, encoding="utf-8"))
        except Exception:
            continue
        text = daily_text(report)
        if not text:
            continue

        # 후보 사전 중 오늘 처음 등장한 성분을 추적 목록에 편입 (별칭 멤버는 제외)
        for c in CANDIDATES:
            if c in tracked or c in members:
                continue
            if count_in(text, c) > 0:
                tracked.add(c)
                print(f"   [신규 편입] '{c}' — 뉴스에 처음 등장")

        # 추적 성분별 오늘 언급 수
        day_counts = {}
        for name in tracked:
            c = count_in(text, name)
            if c > 0:
                day_counts[name] = c
        store["daily"][date] = day_counts
        print(f"   {date}: {sum(day_counts.values())}회 언급 / 성분 {len(day_counts)}종")

    store["tracked"] = sorted(tracked)
    store["updated"] = datetime.now(KST).strftime("%Y-%m-%d")

    # 7일 이동평균 + 지난주 대비 증감 계산해서 함께 저장(웹이 바로 쓰게)
    store["series"], store["movers"] = build_views(store)

    os.makedirs(DATA_DIR, exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(store, f, ensure_ascii=False, indent=2)
    print(f"   → {OUT} 저장 (추적 {len(tracked)}종, 집계일 {len(store['daily'])}일)")
    print("===== 완료 =====")


def build_views(store):
    """일별 카운트 → 성분별 7일 이동평균 시계열 + 지난주 대비 증감 상위."""
    dates = sorted(store["daily"].keys())
    daily = store["daily"]

    # 성분별 일별 카운트 배열 (별칭 멤버 제외)
    members = alias_members()
    per = {}
    for name in store["tracked"]:
        if name in members:
            continue
        per[name] = [daily.get(d, {}).get(name, 0) for d in dates]

    # 30일(약 한 달) 이동평균 시계열
    series = {"dates": dates, "ma7": {}}
    W = 30
    for name, arr in per.items():
        ma = []
        for i in range(len(arr)):
            window = arr[max(0, i - W + 1): i + 1]
            ma.append(round(sum(window) / len(window), 2) if window else 0)
        # 전 구간 0인 성분은 시계열에서 제외(그릴 게 없음)
        if any(v > 0 for v in ma):
            series["ma7"][name] = ma

    # 최근 30일 대비 그 전 30일 언급 합 비교 → 증감
    movers = []
    if len(dates) >= 2:
        for name, arr in per.items():
            last30 = sum(arr[-30:])
            prev30 = sum(arr[-60:-30]) if len(arr) >= 31 else 0
            if last30 == 0 and prev30 == 0:
                continue
            movers.append({
                "name": name,
                "last7": last30,      # 웹 호환 위해 키 이름 유지(값은 최근 30일)
                "prev7": prev30,
                "delta": last30 - prev30,
            })
        movers.sort(key=lambda x: x["delta"], reverse=True)

    return series, movers


if __name__ == "__main__":
    main()
