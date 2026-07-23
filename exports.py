# -*- coding: utf-8 -*-
"""
================================================================
 KOVAS 화장품 수출지표 수집  —  exports.py
================================================================
 관세청 '품목별 수출입실적(GW)' API로 HS 330499(기타 기초화장품 등)의
 월별 수출액을 2018년 1월부터 모아 data/exports.json 으로 저장합니다.

 실행 :  python exports.py
 필요 :  .env 에 DATA_GO_KR_KEY=공공데이터포털_일반인증키(Decoding)

 * 이미 받은 달은 건너뜁니다(누적). 매달 새 달만 추가돼요.
 * 첫 실행 때 API 응답 샘플을 화면에 찍습니다(필드/단위 확인용).
================================================================
"""

import os
import re
import json
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta

from dotenv import load_dotenv

# ----------------------------------------------------------------
# [설정]
# ----------------------------------------------------------------
load_dotenv()
API_KEY = os.environ.get("DATA_GO_KR_KEY", "").strip()

# 관세청 품목별 수출입실적(GW) — 데이터포털 15101609
API_URL = "https://apis.data.go.kr/1220000/Itemtrade/getItemtradeList"

HS_CODE = "330499"      # 화장품 (기타 기초화장품 등)
START_YM = "201801"     # 수집 시작 년월 (2018년 1월)

DATA_DIR = "data"
KST = timezone(timedelta(hours=9))


# 응답에서 '수출 금액' 필드를 찾기 위한 후보 이름들 (API마다 조금씩 달라서)
EXP_FIELDS = ["expDlr", "expUsdAmt", "expAmt", "exp_dlr"]


def month_list(start_ym):
    """start_ym(YYYYMM)부터 지난달까지 월 목록을 만듭니다."""
    y, m = int(start_ym[:4]), int(start_ym[4:])
    now = datetime.now(KST)
    # 관세청 통계는 보통 전월까지 확정 → 지난달까지
    last = now.replace(day=1) - timedelta(days=1)
    out = []
    while (y < last.year) or (y == last.year and m <= last.month):
        out.append(f"{y}{m:02d}")
        m += 1
        if m > 12:
            m = 1
            y += 1
    return out


def fetch_month(yymm, debug=False):
    """한 달치 HS 330499 수출액(달러)을 가져옵니다. 실패 시 None."""
    params = urllib.parse.urlencode({
        "serviceKey": API_KEY,
        "strtYymm": yymm,
        "endYymm": yymm,
        "hsSgn": HS_CODE,
    })
    url = f"{API_URL}?{params}"
    try:
        raw = urllib.request.urlopen(url, timeout=20).read()
    except Exception as e:
        print(f"   {yymm} 요청 실패:", e)
        return None

    try:
        root = ET.fromstring(raw)
    except Exception:
        print(f"   {yymm} 응답 파싱 실패. 원문 앞부분:", raw[:200])
        return None

    # 에러 메시지 확인
    msg = root.findtext(".//resultMsg") or root.findtext(".//returnAuthMsg")
    if msg and "NORMAL" not in msg.upper() and "정상" not in msg:
        print(f"   {yymm} API 메시지: {msg}")
        return None

    items = root.findall(".//item")
    if not items:
        return 0.0

    # 첫 성공 응답의 필드 목록을 찍어 확인용으로 남깁니다
    if debug:
        tags = {c.tag: (c.text or "")[:20] for c in items[0]}
        print("   [진단] 응답 필드 샘플:", tags)

    # 수출금액 필드 찾기 (품목 전체 합계 행 사용)
    total = 0.0
    for it in items:
        val = None
        for f in EXP_FIELDS:
            t = it.findtext(f)
            if t not in (None, ""):
                val = t
                break
        if val is None:
            continue
        try:
            total += float(str(val).replace(",", ""))
        except ValueError:
            pass
    return total


def main():
    print(f"===== 화장품 수출지표 수집 시작 (HS {HS_CODE}) =====")
    if not API_KEY:
        print("DATA_GO_KR_KEY 가 없습니다. .env(또는 GitHub Secrets)에 넣어주세요. 종료합니다.")
        return

    os.makedirs(DATA_DIR, exist_ok=True)
    path = os.path.join(DATA_DIR, "exports.json")

    # 기존 데이터 로드(누적) → 이미 있는 달은 건너뜀
    monthly = {}
    if os.path.exists(path):
        try:
            for row in json.load(open(path, encoding="utf-8")).get("monthly", []):
                monthly[row["ym"]] = row["exp"]
        except Exception:
            monthly = {}

    todo = [ym for ym in month_list(START_YM)
            if f"{ym[:4]}-{ym[4:]}" not in monthly]
    print(f"   가져올 달: {len(todo)}개 (이미 보유 {len(monthly)}개)")

    first = True
    for ym in todo:
        exp = fetch_month(ym, debug=first)
        first = False
        if exp is not None:
            monthly[f"{ym[:4]}-{ym[4:]}"] = exp
            print(f"   {ym[:4]}-{ym[4:]}  수출 {exp:,.0f} USD")

    # 정렬해서 저장
    rows = [{"ym": k, "exp": v} for k, v in sorted(monthly.items())]
    with open(path, "w", encoding="utf-8") as f:
        json.dump({
            "updated": datetime.now(KST).strftime("%Y-%m"),
            "hs": "3304.99",
            "monthly": rows,
        }, f, ensure_ascii=False, indent=2)
    print(f"   → {path} 저장 (총 {len(rows)}개월)")
    print("===== 완료 =====")


if __name__ == "__main__":
    main()
