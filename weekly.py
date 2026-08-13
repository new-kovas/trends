# -*- coding: utf-8 -*-
"""
================================================================
 KOVAS 주간 트렌드 생성  —  weekly.py
================================================================
 지난 한 주(토~금)의 데일리 리포트를 모아 Claude로 '주간 트렌드'를
 재요약하고, 그 주 토요일 날짜로 data/week-YYYY-MM-DD.json 저장.
 동시에 이메일에 붙여넣을 '초안'(제목+본문)도 만들어
 data/weekly_email.txt 로 남깁니다. (반자동: 사람이 확인 후 발송)

 실행 :  python weekly.py            (이번 주 토~금 자동 계산)
         python weekly.py 2026-08-01 (그 주에 속한 아무 날짜나 지정)
 필요 :  .env 에 ANTHROPIC_API_KEY
================================================================
"""

import os
import re
import sys
import json
import glob
from datetime import datetime, timezone, timedelta

from dotenv import load_dotenv
from anthropic import Anthropic

load_dotenv()

CLAUDE_MODEL = "claude-sonnet-5"
DATA_DIR = "data"
KST = timezone(timedelta(hours=9))
SITE_URL = "https://new-kovas.github.io/trends/"   # 도메인 연결 후 trend.kovas.co.kr 로 교체


def saturday_of(dt):
    """그 날이 속한 주(토~금)의 시작 토요일(date). 파이썬 weekday(): 월0…토5,일6."""
    # 토요일(5)로부터 며칠 지났는지: (weekday - 5) mod 7
    return dt - timedelta(days=(dt.weekday() - 5) % 7)


def target_week():
    """인자로 주 안의 아무 날짜나 받거나, 없으면 '이번에 막 끝난 토~금' 주를 계산."""
    if len(sys.argv) > 1:
        d = datetime.strptime(sys.argv[1], "%Y-%m-%d").date()
        return saturday_of(d)
    # 금요일에 실행 → 오늘(금)이 속한 토~금 주가 방금 끝난 그 주.
    today = datetime.now(KST).date()
    return saturday_of(today)


def load_week_reports(start_sat):
    """토~금 7일치 데일리 리포트를 모읍니다."""
    days = []
    for i in range(7):
        d = (start_sat + timedelta(days=i)).strftime("%Y-%m-%d")
        p = os.path.join(DATA_DIR, f"{d}.json")
        if os.path.exists(p):
            try:
                r = json.load(open(p, encoding="utf-8"))
                days.append((d, r))
            except Exception:
                pass
    return days


def build_prompt(days, start, end):
    """한 주치 데일리 리포트 → 주간 트렌드 요약 프롬프트. 각 이슈에 [날짜/번호] 표기."""
    blocks = []
    for d, r in days:
        lines = [f"[{d}]"]
        for it in r.get("issues", []):
            # 각 데일리 이슈를 (날짜, 번호)로 식별할 수 있게 표기
            lines.append(f"- (출처 {d}#{it.get('no','')}) {it.get('title','')}: {it.get('body','')}")
        if r.get("oneLiner"):
            lines.append(f"  (한줄: {r['oneLiner']})")
        blocks.append("\n".join(lines))
    daily_block = "\n\n".join(blocks)

    return f"""당신은 화장품 ODM 기업 코바스의 전략기획 담당자입니다.
아래는 {start}~{end} 한 주간(토~금)의 '데일리 트렌드 요약'들입니다.
각 항목 앞에 (출처 날짜#번호) 가 붙어 있습니다.
이것들을 종합해, 한 주를 관통하는 '주간 트렌드'로 재정리하세요.

작성 규칙:
- 한 주 전체에서 의미 있는 흐름을 10개의 '주간 이슈'로 정리하세요. (데이터가 부족하면 가능한 만큼)
- 각 주간 이슈에는 제목, 2~3문장 요약, 코바스(패치·시트마스크 ODM) 관점의 시사점 1~2개를 넣으세요.
- 각 주간 이슈에는, 그 이슈의 근거가 된 데일리 항목들의 출처를 sources 배열로 넣으세요.
  형식은 [{{"date":"YYYY-MM-DD","no":번호}}] 이며, 위 목록의 (출처 날짜#번호)에서 그대로 가져오세요.
  여러 날에 걸친 흐름이면 여러 출처를 넣어도 됩니다. 최소 1개는 반드시 넣으세요.
- 마지막에 이번 주를 한 문장으로 정리(weekOneLiner)하고, 핵심 키워드 해시태그 4~6개를 만드세요.
- 사실에 근거하고, 없는 내용을 지어내지 마세요.

반드시 아래 JSON 형식으로만 답하세요. 코드블록 표시(```)나 다른 설명 없이 JSON만.

{{
  "weekIssues": [
    {{ "title": "주간 이슈 제목", "body": "2~3문장 요약", "implications": ["시사점1"], "sources": [{{"date":"2026-07-11","no":3}}] }}
  ],
  "weekOneLiner": "이번 주 트렌드 한 문장",
  "hashtags": ["키워드1", "키워드2", "키워드3", "키워드4"]
}}

--- 이번 주 데일리 요약들 ---
{daily_block}
"""


def summarize_week(days, start, end):
    client = Anthropic()
    msg = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=8000,   # 이슈 10개 + 출처 매핑이라 넉넉히
        messages=[{"role": "user", "content": build_prompt(days, start, end)}],
    )
    text = "".join(b.text for b in msg.content if getattr(b, "type", None) == "text").strip()
    text = re.sub(r"^```(json)?", "", text).strip()
    text = re.sub(r"```$", "", text).strip()
    if msg.stop_reason == "max_tokens" or not text.endswith("}"):
        raise RuntimeError("AI 답변이 잘렸습니다. max_tokens를 키우세요. 끝부분: " + text[-80:])
    data = json.loads(text)
    # 출처가 실제 존재하는 (날짜,번호)인지 검증해서 잘못된 매핑은 제거
    valid = {}
    for d, r in days:
        valid[d] = {it.get("no") for it in r.get("issues", [])}
    for it in data.get("weekIssues", []):
        clean = []
        for s in it.get("sources", []) or []:
            if s.get("date") in valid and s.get("no") in valid[s["date"]]:
                clean.append({"date": s["date"], "no": s["no"]})
        it["sources"] = clean
    return data


def make_email(week, start, end):
    """주간 트렌드 → 이메일 초안(제목+본문). 요약만 담고 상세는 웹으로 유도."""
    subject = f"[코바스 주간 뷰티 트렌드] {start} ~ {end}"
    lines = []
    lines.append(f"한 주간의 기초화장품·스킨케어 트렌드를 정리했습니다.\n")
    lines.append(f"■ 이번 주 한 줄 요약\n{week.get('weekOneLiner','')}\n")
    lines.append("■ 주요 흐름")
    for i, it in enumerate(week.get("weekIssues", []), 1):
        lines.append(f"  {i}. {it.get('title','')}")
        lines.append(f"     {it.get('body','')}")
    tags = " ".join(f"#{str(t).lstrip('#')}" for t in week.get("hashtags", []))
    if tags:
        lines.append(f"\n{tags}")
    lines.append("\n───────────────────────────")
    lines.append(f"자세한 내용(경쟁사 동향·신제품·원료·수출지표 포함)은 웹사이트에서 확인하실 수 있습니다.")
    lines.append(f"▶ {SITE_URL}")
    lines.append("\nKOVAS 전략기획본부 전략경영팀")
    return subject, "\n".join(lines)


def main():
    start_d = target_week()                       # 그 주 토요일
    start = start_d.strftime("%Y-%m-%d")          # 주 시작(토)
    end = (start_d + timedelta(days=6)).strftime("%Y-%m-%d")  # 주 끝(금)
    print(f"===== 주간 트렌드 생성: {start} ~ {end} (토~금) =====")

    days = load_week_reports(start_d)
    if not days:
        print("   해당 주에 데일리 리포트가 없습니다. 건너뜁니다.")
        return
    print(f"   데일리 리포트 {len(days)}일치 수집")

    week = summarize_week(days, start, end)
    week["weekLabel"] = f"{start} ~ {end}"
    week["weekStart"] = start                      # 그 주 토요일 (배지가 뜨는 날)
    week["days"] = [d for d, _ in days]

    os.makedirs(DATA_DIR, exist_ok=True)

    # 1) 주간 트렌드 JSON (그 주 토요일 날짜로)
    out = os.path.join(DATA_DIR, f"week-{start}.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(week, f, ensure_ascii=False, indent=2)
    print(f"   → {out} 저장")

    # 2) 주간 인덱스(웹이 어떤 주가 있는지 알도록)
    widx_path = os.path.join(DATA_DIR, "weeks.json")
    weeks = []
    if os.path.exists(widx_path):
        try:
            weeks = json.load(open(widx_path, encoding="utf-8"))
        except Exception:
            weeks = []
    if start not in weeks:
        weeks.append(start)
    weeks = sorted(set(weeks))
    json.dump(weeks, open(widx_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"   → {widx_path} 갱신 ({len(weeks)}주)")

    # 3) 이메일 초안 (사람이 확인 후 발송)
    subject, body = make_email(week, start, end)
    email_path = os.path.join(DATA_DIR, "weekly_email.txt")
    with open(email_path, "w", encoding="utf-8") as f:
        f.write("제목: " + subject + "\n\n" + body + "\n")
    print(f"   → {email_path} 저장 (이메일 초안: 확인 후 발송)")
    print("===== 완료 =====")


if __name__ == "__main__":
    main()
