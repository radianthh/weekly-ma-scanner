"""HTML/CSV/TXT 리포트와 주간 스냅샷 생성."""

from __future__ import annotations

import html
import json
import os
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import pandas as pd

STREAK_BUCKETS = (("1~4주", 1, 4), ("5~12주", 5, 12), ("13~26주", 13, 26), ("27주 이상", 27, 10**9))
CAP_BUCKETS = (
    ("10조 이상", 100_000, float("inf")),
    ("1조~10조", 10_000, 100_000),
    ("5천억~1조", 5_000, 10_000),
    ("3천억~5천억", 3_000, 5_000),
)

CSS = """
:root{--ink:#1a1a1a;--sub:#8a8a8a;--line:#e8e8e8;--bg:#fafafa;--card:#fff;--accent:#3b6cf6}
*{box-sizing:border-box}body{margin:0;padding:32px 20px 64px;background:var(--bg);color:var(--ink);
font-family:-apple-system,BlinkMacSystemFont,'Apple SD Gothic Neo','Malgun Gothic',sans-serif;font-size:15px;line-height:1.6}
.wrap{max-width:920px;margin:auto}.top{display:flex;justify-content:space-between;align-items:baseline;padding-bottom:14px;
border-bottom:1px solid var(--line);margin-bottom:36px}.top a{color:var(--accent);text-decoration:none;font:13px ui-monospace,monospace}
h1{font-size:30px;margin:0 0 10px}.lead,.note{color:var(--sub);font-size:13px}.lead{margin:0 0 28px}.note{margin:0 0 18px}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:26px 28px;margin-bottom:20px}
.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:8px}.stat .k{color:var(--sub);font-size:13px}.stat .v{font-size:28px;font-weight:700}
.stat small{font-size:13px;font-weight:400;color:var(--sub);margin-left:3px}h2{font-size:17px;margin:0 0 4px}
table{width:100%;border-collapse:collapse;font-size:14px}th,td{padding:9px 10px;border:1px solid var(--line);text-align:right}
th{color:var(--sub);font-weight:400;font-size:12.5px}th:first-child,td:first-child{text-align:left}td.code{font:13px ui-monospace,monospace;color:#b8b8b8}
td.mkt,td.wk,td.date{text-align:center}td.wk{font-weight:700}td.date{font:13px ui-monospace,monospace;color:#aaa}tr.new td:first-child{box-shadow:inset 3px 0 var(--accent)}
.bar{display:flex;align-items:center;gap:12px;padding:7px 0}.bar .lab{width:120px;color:var(--sub);font-size:13.5px}.track{flex:1;height:7px;background:#f0f0f0;border-radius:4px;overflow:hidden}.fill{height:100%;background:var(--accent)}.val{width:130px;text-align:right;font-weight:700}.val small{color:var(--sub);font-weight:400;margin-left:5px}
.quote{border-left:3px solid var(--accent);padding:2px 0 2px 14px;margin:14px 0;font-size:13.5px}.quote.grey{border-color:#ddd;color:#555}.foot{text-align:center;color:#bbb;font-size:12.5px;margin-top:34px}
@media(max-width:700px){.stats{grid-template-columns:repeat(2,1fr)}.card{padding:20px 16px;overflow-x:auto}}
@media print{body{background:#fff;padding:0}.card{break-inside:avoid}.top a{color:#333}}
"""


@dataclass(frozen=True)
class Changes:
    new_df: pd.DataFrame
    out_df: pd.DataFrame
    new_tickers: set[str]
    source: str


def fmt_cap(eok: float) -> str:
    return f"{eok / 10_000:.1f}조" if eok >= 10_000 else f"{eok:,.0f}억"


def fmt_price(value: float) -> str:
    return f"{value:,.0f}"


def compare_with_previous(
    history_dir: str | Path,
    prev_friday: date,
    scanned: pd.DataFrame,
    passed: pd.DataFrame,
) -> Changes:
    snapshot_path = Path(history_dir) / f"{prev_friday:%Y-%m-%d}.json"
    previous_detail: dict[str, dict[str, object]] = {}
    if snapshot_path.exists():
        try:
            snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
            previous = set(snapshot["tickers"])
            previous_detail = snapshot.get("detail", {})
            source = "직전 실행 스냅샷"
        except (OSError, KeyError, TypeError, json.JSONDecodeError):
            previous = set(scanned.index[scanned["aligned_prev"]])
            source = "이번 데이터로 재계산"
    else:
        previous = set(scanned.index[scanned["aligned_prev"]])
        source = "이번 데이터로 재계산"

    current = set(passed.index)
    new_tickers = [ticker for ticker in passed.index if ticker not in previous]
    out_tickers = [ticker for ticker in previous if ticker not in current]
    new_df = passed.loc[new_tickers, ["종목명", "시장", "시가총액_억"]]

    records: list[tuple[str, str, str, float]] = []
    for ticker in out_tickers:
        if ticker in scanned.index:
            row = scanned.loc[ticker]
            records.append((ticker, str(row["종목명"]), str(row["시장"]), float(row["시가총액_억"])))
        elif ticker in previous_detail:
            old = previous_detail[ticker]
            records.append((ticker, str(old.get("name", ticker)), str(old.get("market", "-")), float(old.get("cap_eok", 0))))
    out_df = pd.DataFrame(records, columns=["코드", "종목명", "시장", "시가총액_억"])
    if out_df.empty:
        out_df = pd.DataFrame(columns=["종목명", "시장", "시가총액_억"])
    else:
        out_df = out_df.set_index("코드").sort_values("시가총액_억", ascending=False)
    return Changes(new_df, out_df, set(new_tickers), source)


def _simple_table(frame: pd.DataFrame) -> str:
    if frame.empty:
        return '<p class="note">해당 종목 없음</p>'
    rows = "".join(
        f"<tr><td>{html.escape(str(row['종목명']))}</td><td class=\"code\">{ticker}</td>"
        f"<td class=\"mkt\">{row['시장']}</td><td>{fmt_cap(float(row['시가총액_억']))}</td></tr>"
        for ticker, row in frame.iterrows()
    )
    return f"<table><tr><th>종목</th><th>코드</th><th>시장</th><th>시가총액</th></tr>{rows}</table>"


def _bars(items: list[tuple[str, str, float]]) -> str:
    return "".join(
        f'<div class="bar"><div class="lab">{label}</div><div class="track"><div class="fill" style="width:{max(0, min(100, width)):.1f}%"></div></div><div class="val">{value}</div></div>'
        for label, value, width in items
    )


def render_html(context: dict[str, object]) -> str:
    passed: pd.DataFrame = context["passed"]  # type: ignore[assignment]
    scanned: pd.DataFrame = context["scanned"]  # type: ignore[assignment]
    changes: Changes = context["changes"]  # type: ignore[assignment]
    asof: date = context["asof"]  # type: ignore[assignment]
    prev_friday: date = context["prev_friday"]  # type: ignore[assignment]
    generated: datetime = context["generated"]  # type: ignore[assignment]
    min_cap = int(context["min_cap"])

    body = []
    for ticker, row in passed.iterrows():
        css_class = ' class="new"' if ticker in changes.new_tickers else ""
        plus = "+" if bool(row["streak_capped"]) else ""
        body.append(
            f"<tr{css_class}><td>{html.escape(str(row['종목명']))}</td><td class=\"code\">{ticker}</td>"
            f"<td class=\"mkt\">{row['시장']}</td><td class=\"wk\">{int(row['streak'])}주{plus}</td>"
            f"<td class=\"date\">{row['start_week']}</td><td>{fmt_cap(float(row['시가총액_억']))}</td>"
            f"<td>{fmt_price(float(row['close']))}</td></tr>"
        )

    count = len(passed)
    ratio = count / len(scanned) * 100 if len(scanned) else 0
    longest = int(passed["streak"].max()) if count else 0
    kospi = int((passed["시장"] == "KOSPI").sum())
    kosdaq = int((passed["시장"] == "KOSDAQ").sum())
    streak_bars = []
    for label, low, high in STREAK_BUCKETS:
        bucket_count = int(((passed["streak"] >= low) & (passed["streak"] <= high)).sum())
        pct = bucket_count / count * 100 if count else 0
        streak_bars.append((label, f"{bucket_count} <small>{pct:.0f}%</small>", pct))
    cap_bars = []
    for label, low, high in CAP_BUCKETS:
        total = int(((scanned["시가총액_억"] >= low) & (scanned["시가총액_억"] < high)).sum())
        hit = int(((passed["시가총액_억"] >= low) & (passed["시가총액_억"] < high)).sum())
        pct = hit / total * 100 if total else 0
        cap_bars.append((label, f"{pct:.1f}% <small>{hit}/{total}</small>", pct / 15 * 100))

    title_left = html.escape(os.getenv("REPORT_TITLE", "주봉 정배열 리포트"))
    link_text = html.escape(os.getenv("REPORT_LINK_TEXT", ""))
    link_url = html.escape(os.getenv("REPORT_LINK_URL", "#"), quote=True)
    exclude_note = " · 사용자 제외 목록" if context.get("has_exclusions") else ""

    return f"""<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>주봉 정배열 종목 {asof:%Y-%m-%d} 기준</title><style>{CSS}</style></head><body><div class="wrap">
<div class="top"><b>{title_left}</b><a href="{link_url}">{link_text}</a></div>
<h1>주봉 정배열 종목 {asof:%Y-%m-%d} 기준</h1><p class="lead">{asof:%Y-%m-%d} 마감 기준 · 주봉 MA4 &gt; MA13 &gt; MA26 &gt; MA52 · KOSPI·KOSDAQ 시가총액 {min_cap:,}억원 이상 · {generated:%Y-%m-%d %H:%M} 작성</p>
<div class="card stats"><div class="stat"><div class="k">정배열 종목</div><div class="v">{count}<small>종목</small></div></div><div class="stat"><div class="k">검토 대상</div><div class="v">{len(scanned)}<small>종목</small></div></div><div class="stat"><div class="k">비율</div><div class="v">{ratio:.1f}<small>%</small></div></div><div class="stat"><div class="k">최장 유지</div><div class="v">{longest}<small>주</small></div></div></div>
<div class="card"><h2>1. 판정 기준</h2><table><tr><th>기준 주</th><td style="text-align:left"><b>{asof:%Y-%m-%d} 마감 (금요일)</b></td></tr><tr><th>조건</th><td style="text-align:left"><b>주봉 MA4 &gt; MA13 &gt; MA26 &gt; MA52</b></td></tr><tr><th></th><td style="text-align:left;color:#aaa">4주·13주·26주·52주 = 1개월·1분기·반년·1년</td></tr><tr><th>대상</th><td style="text-align:left">KOSPI·KOSDAQ 시가총액 {min_cap:,}억원 이상, 상장 1년 이상</td></tr><tr><th>제외</th><td style="text-align:left">우선주 · 스팩 · 리츠 · ETF · ETN · 관리종목 · 거래정지{exclude_note}</td></tr><tr><th>정렬</th><td style="text-align:left">정배열 유지 기간 내림차순, 동률이면 시가총액 내림차순</td></tr></table></div>
<div class="card"><h2>2. 주봉 정배열 종목 {count}개</h2><p class="note">유지 기간이 긴 순 · KOSPI {kospi} / KOSDAQ {kosdaq} · + 는 보유 데이터 전 구간이 정배열이라 시작 시점이 더 이전임을 뜻합니다</p><table><tr><th>종목</th><th>코드</th><th>시장</th><th>유지</th><th>시작 주</th><th>시가총액</th><th>종가</th></tr>{''.join(body)}</table></div>
<div class="card"><h2>3. 지난주 대비 변화</h2><p class="note">직전 주({prev_friday:%Y-%m-%d}) 목록과 비교 · {changes.source}</p><p><b>▶ 신규 진입 {len(changes.new_df)}</b></p>{_simple_table(changes.new_df)}<div style="height:22px"></div><p><b>▼ 이탈 {len(changes.out_df)}</b></p>{_simple_table(changes.out_df)}</div>
<div class="card"><h2>4. 유지 기간 분포</h2><p class="note">정배열이 몇 주째 이어지고 있는가</p>{_bars(streak_bars)}</div>
<div class="card"><h2>5. 시가총액 구간별 비율</h2><p class="note">각 구간에서 몇 %가 정배열인가 · 가로축 0~15%</p>{_bars(cap_bars)}</div>
<div class="card"><h2>6. 산출 기준과 유의사항</h2><div class="quote"><b>이 보고서는 공개 시세를 기계적으로 집계한 결과이며 특정 종목의 매수·매도를 권유하지 않습니다. 이동평균 정배열은 과거 가격에서 계산되는 후행 지표이고, 투자 판단과 결과의 책임은 투자자 본인에게 있습니다.</b></div><div class="quote grey">종목·시가총액·상장 정보는 <b>KRX 정보데이터시스템</b>, 시세는 <b>키움증권 REST API</b>에서 받았습니다(KRX 정규장 기준, 수정주가). 액면분할·무상증자는 과거 가격에 소급 반영된 값을 사용합니다.</div><div class="quote grey"><b>정배열</b>은 단기 이동평균이 장기 이동평균보다 차례로 위에 있는 상태입니다. 이 보고서는 <b>주봉</b> 기준으로 수개월~1년 흐름을 보므로 일봉 정배열과 결과가 다릅니다.</div></div>
<div class="foot">기계적 스크리닝 결과이며 투자 권유가 아닙니다.</div></div></body></html>"""


def render_text(context: dict[str, object]) -> str:
    passed: pd.DataFrame = context["passed"]  # type: ignore[assignment]
    changes: Changes = context["changes"]  # type: ignore[assignment]
    asof: date = context["asof"]  # type: ignore[assignment]
    lines = [
        f"[주봉 정배열] {asof:%Y-%m-%d} 기준 · MA4>13>26>52",
        f"통과 {len(passed)}종목 (신규 {len(changes.new_df)} / 이탈 {len(changes.out_df)})",
        "", f"▶ 신규 진입 {len(changes.new_df)}",
    ]
    for ticker, row in changes.new_df.iterrows():
        lines.append(f"  {row['종목명']} ({ticker}) {float(row['시가총액_억']):,.0f}억")
    lines.extend(["", f"▼ 이탈 {len(changes.out_df)}"])
    for ticker, row in changes.out_df.iterrows():
        lines.append(f"  {row['종목명']} ({ticker}) {float(row['시가총액_억']):,.0f}억")
    lines.extend(["", "최장 유지"])
    for ticker, row in passed.head(3).iterrows():
        lines.append(f"  {row['종목명']} ({ticker}) {float(row['시가총액_억']):,.0f}억 {int(row['streak'])}주")
    return "\n".join(lines)


def save_reports(context: dict[str, object], out_dir: str | Path) -> dict[str, Path]:
    destination = Path(out_dir)
    history_dir = destination / "history"
    history_dir.mkdir(parents=True, exist_ok=True)
    asof: date = context["asof"]  # type: ignore[assignment]
    passed: pd.DataFrame = context["passed"]  # type: ignore[assignment]
    scanned: pd.DataFrame = context["scanned"]  # type: ignore[assignment]
    trade_date: date = context["trade_date"]  # type: ignore[assignment]
    stem = f"{asof:%Y%m%d}_주봉_정배열"
    paths = {
        "html": destination / f"{stem}.html",
        "csv": destination / f"{stem}.csv",
        "txt": destination / f"{stem}.txt",
        "history": history_dir / f"{asof:%Y-%m-%d}.json",
    }
    paths["html"].write_text(render_html(context), encoding="utf-8")
    csv_frame = passed[["종목명", "시장", "시가총액_억", "close", "streak", "start_week"]].copy()
    csv_frame["streak"] = [
        f"{int(row['streak'])}{'+' if bool(row['streak_capped']) else ''}"
        for _, row in passed.iterrows()
    ]
    csv_frame.rename(
        columns={"close": "종가", "streak": "유지주수", "start_week": "시작주"}
    ).to_csv(paths["csv"], encoding="utf-8-sig", index_label="코드")
    paths["txt"].write_text(render_text(context), encoding="utf-8")
    snapshot = {
        "asof": f"{asof:%Y-%m-%d}", "trade_date": f"{trade_date:%Y-%m-%d}",
        "min_cap_eok": int(context["min_cap"]), "universe": len(scanned),
        "tickers": list(passed.index),
        "detail": {
            ticker: {"name": row["종목명"], "market": row["시장"], "cap_eok": round(float(row["시가총액_억"]), 1), "streak": int(row["streak"])}
            for ticker, row in passed.iterrows()
        },
    }
    paths["history"].write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    return paths
