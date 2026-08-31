"""pykrx 기반 KOSPI·KOSDAQ 스크리닝 유니버스 구성."""

from __future__ import annotations

import re
import io
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections.abc import Callable
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
from dotenv import load_dotenv

DEFAULT_MIN_CAP_EOK = 3_000
EXCLUDE_NAME_PATTERNS = re.compile(
    r"(?:스팩|제\d+호스팩|리츠|리츠운용|ETN|KODEX|TIGER|KBSTAR|ARIRANG|HANARO|SOL |ACE |PLUS |RISE )"
)
_PYKRX_OUTPUT_LOCK = threading.Lock()


def log(message: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {message}", flush=True)


def ymd(value: date) -> str:
    return value.strftime("%Y%m%d")


@dataclass(frozen=True)
class UniverseResult:
    trade_date: date
    frame: pd.DataFrame


def _quiet_call(function: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """pykrx 내부 인증·오류 출력(로그인 ID 포함 가능)을 외부 로그에서 숨긴다."""
    with _PYKRX_OUTPUT_LOCK:
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            return function(*args, **kwargs)


def read_exclusions(path: str | Path) -> set[str]:
    source = Path(path)
    if not source.exists():
        return set()
    return {
        value
        for line in source.read_text(encoding="utf-8").splitlines()
        if (value := line.split("#", 1)[0].strip())
    }


def _trading_snapshot(stock: object, target: date, max_back: int = 7) -> tuple[date, pd.DataFrame]:
    for offset in range(max_back + 1):
        current = target - timedelta(days=offset)
        try:
            frame = _quiet_call(stock.get_market_cap_by_ticker, ymd(current), market="ALL")
        except Exception as exc:
            log(f"KRX 시가총액 조회 재시도: {current} ({exc.__class__.__name__})")
            continue
        if frame is not None and not frame.empty and "시가총액" in frame.columns:
            if float(frame["시가총액"].sum()) > 0:
                return current, frame
    raise RuntimeError(f"{target} 기준 KRX 거래일 데이터를 찾지 못했습니다.")


def _market_map(stock: object, trade_date: date) -> dict[str, str]:
    result: dict[str, str] = {}
    for market in ("KOSPI", "KOSDAQ"):
        tickers = _quiet_call(stock.get_market_ticker_list, ymd(trade_date), market=market)
        result.update({str(ticker).zfill(6): market for ticker in tickers})
    return result


def _name_map(stock: object, tickers: list[str], workers: int) -> dict[str, str]:
    """pykrx로만 종목명을 모은다(외부 데이터 소스 없음).

    종목명은 스팩·리츠·ETN 제외 필터의 입력이다. 조회에 실패해 코드가 그대로
    남으면 제외됐어야 할 종목이 필터를 통과하므로, 한 번 더 시도하고 그래도
    실패하면 경고로 남긴다.
    """
    log(f"종목명 조회 {len(tickers):,}종목 (pykrx 개별 조회)")
    names: dict[str, str] = {}
    pending = list(tickers)

    for attempt in (1, 2):
        if not pending:
            break
        failed: list[str] = []
        done = 0
        with ThreadPoolExecutor(max_workers=min(workers, 4)) as executor:
            futures = {
                executor.submit(_quiet_call, stock.get_market_ticker_name, ticker): ticker
                for ticker in pending
            }
            for future in as_completed(futures):
                ticker = futures[future]
                try:
                    name = str(future.result()).strip()
                except Exception:
                    name = ""
                if name:
                    names[ticker] = name
                else:
                    failed.append(ticker)
                done += 1
                if done % 200 == 0:
                    log(f"  종목명 {done}/{len(pending)} (실패 {len(failed)})")
        if failed and attempt == 1:
            log(f"종목명 조회 재시도 {len(failed)}종목")
        pending = failed

    if pending:
        sample = ", ".join(sorted(pending)[:10]) + (" 외" if len(pending) > 10 else "")
        log(
            f"경고: 종목명 조회 실패 {len(pending)}종목 — 코드로 대체합니다. "
            f"이 종목들에는 스팩·리츠·ETN 제외 필터가 적용되지 않습니다: {sample}"
        )
    return {ticker: names.get(ticker, ticker) for ticker in tickers}


def build_universe(
    asof: date,
    min_cap_eok: int = DEFAULT_MIN_CAP_EOK,
    workers: int = 4,
    exclude_file: str | Path = "exclude.txt",
) -> UniverseResult:
    # KRX 인증 세션은 pykrx를 처음 import할 때 환경변수를 읽는다.
    # 키움 설정 로드는 이 함수보다 뒤에 실행되므로 여기서 .env를 먼저 읽어야 한다.
    load_dotenv(".env")
    # pykrx 1.2.x가 인증 과정에서 KRX ID를 stdout에 출력하므로 외부 로그 노출을 막는다.
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        from pykrx import stock

    trade_date, cap_frame = _trading_snapshot(stock, asof)
    log(f"시가총액 스냅샷: {trade_date} · 전체 {len(cap_frame):,}종목")

    frame = cap_frame.copy()
    frame.index = frame.index.astype(str).str.zfill(6)
    markets = _market_map(stock, trade_date)
    frame["시장"] = frame.index.map(markets)
    frame = frame[frame["시장"].isin(["KOSPI", "KOSDAQ"])]
    frame["시가총액_억"] = frame["시가총액"].astype(float) / 1e8
    frame = frame[frame["시가총액_억"] >= min_cap_eok]

    # 지시서 규칙: 보통주 코드는 끝자리가 0인 종목만 남긴다.
    frame = frame[[ticker.endswith("0") for ticker in frame.index]]
    if "거래량" in frame.columns:
        frame = frame[frame["거래량"].astype(float) > 0]

    names = _name_map(stock, list(frame.index), workers)
    frame["종목명"] = [names[ticker] for ticker in frame.index]
    frame = frame[~frame["종목명"].str.contains(EXCLUDE_NAME_PATTERNS, na=False)]
    frame = frame[~frame["종목명"].str.match(r".*[0-9]우[A-Z]?$", na=False)]

    excluded = read_exclusions(exclude_file)
    if excluded:
        frame = frame[~frame.index.isin(excluded)]
        log(f"추가 제외 {len(excluded)}종목 ({exclude_file})")

    columns = ["종목명", "시장", "시가총액_억", "종가"]
    frame = frame[columns].sort_values("시가총액_억", ascending=False)
    log(f"검토 대상: {len(frame):,}종목 (시총 {min_cap_eok:,}억 이상)")
    return UniverseResult(trade_date=trade_date, frame=frame)
