#!/usr/bin/env python3
"""주봉 MA4 > MA13 > MA26 > MA52 스캐너 진입점."""

from __future__ import annotations

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

from .kiwoom_client import KiwoomClient, KiwoomConfig, KiwoomError, log
from .mailer import MailError, send_report
from .report import compare_with_previous, save_reports
from .screen import evaluate, last_friday
from .universe import DEFAULT_MIN_CAP_EOK, build_universe, read_exclusions


def scan(
    client: KiwoomClient,
    universe: pd.DataFrame,
    asof: date,
    years: int,
    workers: int,
    refresh: bool,
) -> tuple[pd.DataFrame, list[str]]:
    start = asof - timedelta(days=int(365.25 * years))
    rows: list[dict[str, object]] = []
    failures: list[str] = []

    def work(ticker: str) -> tuple[str, dict[str, object] | None, str | None]:
        try:
            weekly = client.weekly_close(ticker, start, asof, refresh=refresh)
            return ticker, evaluate(weekly, asof), None
        except Exception as exc:
            return ticker, None, f"{exc.__class__.__name__}: {exc}"

    total = len(universe)
    with ThreadPoolExecutor(max_workers=min(max(workers, 1), 4)) as executor:
        futures = {executor.submit(work, ticker): ticker for ticker in universe.index}
        for count, future in enumerate(as_completed(futures), 1):
            ticker, result, error = future.result()
            if error:
                failures.append(ticker)
                log(f"시세 실패 {ticker}: {error}")
            elif result is not None:
                rows.append({"티커": ticker, **result})
            if count % 50 == 0 or count == total:
                log(f"시세 수집 {count}/{total} (실패 {len(failures)})")

    if not rows:
        raise RuntimeError("판정 가능한 주봉을 한 건도 받지 못했습니다.")
    result_frame = pd.DataFrame(rows).set_index("티커")
    return universe.join(result_frame, how="inner"), sorted(failures)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m jbscan", description="주봉 정배열 스캐너 (키움 REST 수정주가)"
    )
    parser.add_argument("--date", help="기준일 YYYY-MM-DD (기본: 최근 금요일)")
    parser.add_argument("--min-cap", type=int, default=DEFAULT_MIN_CAP_EOK, help="시가총액 하한(억원)")
    parser.add_argument("--years", type=int, default=4, help="주봉 조회 기간(년)")
    parser.add_argument("--rps", type=float, default=8, help="키움 초당 요청 수")
    parser.add_argument("--workers", type=int, default=4, help="동시 요청 수(최대 4)")
    parser.add_argument("--out-dir", default="reports")
    parser.add_argument("--cache-dir", default=".cache")
    parser.add_argument("--refresh", action="store_true", help="주봉 캐시를 무시하고 다시 조회")
    parser.add_argument("--exclude-file", default="exclude.txt")
    parser.add_argument("--strict-date", action="store_true", help="기준일 데이터 미반영 시 종료코드 2")
    parser.add_argument("--dry-run", action="store_true", help="유니버스만 구성하고 키움 호출 없이 종료")
    parser.add_argument("--no-mail", action="store_true", help="리포트만 만들고 메일은 보내지 않음")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.years < 2:
        raise ValueError("MA52와 유지 기간 계산을 위해 --years는 2 이상이어야 합니다.")
    if args.workers < 1 or args.workers > 4:
        raise ValueError("--workers는 1~4 범위여야 합니다.")
    if args.rps <= 0:
        raise ValueError("--rps는 0보다 커야 합니다.")

    requested = datetime.strptime(args.date, "%Y-%m-%d").date() if args.date else None
    asof = last_friday(requested)
    previous_friday = asof - timedelta(days=7)
    log(f"기준 주: {asof:%Y-%m-%d} (금)")

    universe_result = build_universe(
        asof=asof,
        min_cap_eok=args.min_cap,
        workers=args.workers,
        exclude_file=args.exclude_file,
    )
    if universe_result.trade_date != asof:
        message = f"기준일 KRX 데이터 미반영: {asof} 요청, {universe_result.trade_date} 사용"
        if args.strict_date:
            log("중단: " + message)
            return 2
        log("경고: " + message)

    if args.dry_run:
        log(f"dry-run 완료: 검토 대상 {len(universe_result.frame):,}종목")
        return 0

    config = KiwoomConfig.from_env()
    client = KiwoomClient(config, cache_dir=args.cache_dir, rps=args.rps)
    scanned, failures = scan(
        client, universe_result.frame, asof, args.years, args.workers, args.refresh
    )
    log(f"판정 완료: {len(scanned):,}종목 (주봉 53개 이상)")

    latest_week = max(scanned["week_end"]) if not scanned.empty else None
    if latest_week != asof:
        message = f"기준일 키움 주봉 미반영: 최신 주봉 {latest_week}, 기준일 {asof}"
        if args.strict_date:
            log("중단: " + message)
            return 2
        log("경고: " + message)

    passed = scanned[scanned["aligned"]].sort_values(
        ["streak", "시가총액_억"], ascending=[False, False]
    )
    history_dir = Path(args.out_dir) / "history"
    changes = compare_with_previous(history_dir, previous_friday, scanned, passed)
    context: dict[str, object] = {
        "asof": asof,
        "prev_friday": previous_friday,
        "generated": datetime.now(),
        "trade_date": universe_result.trade_date,
        "passed": passed,
        "scanned": scanned,
        "changes": changes,
        "min_cap": args.min_cap,
        "has_exclusions": bool(read_exclusions(args.exclude_file)),
    }
    paths = save_reports(context, args.out_dir)
    log(f"정배열 {len(passed)}종목 · 신규 {len(changes.new_df)} · 이탈 {len(changes.out_df)}")
    log(f"리포트 저장: {paths['html']}")

    if args.no_mail:
        log("메일 발송 생략(--no-mail)")
    else:
        try:
            log(f"메일 발송: {', '.join(send_report(context, paths))}")
        except MailError as exc:
            # 리포트는 이미 저장됐으므로 발송 실패로 스캔 전체를 되돌리지 않는다.
            log(f"경고: 메일 발송 실패 — {exc}")

    if failures:
        log(f"최종 실패 {len(failures)}종목: {', '.join(failures)}")
    else:
        log("최종 실패 종목 없음")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (KiwoomError, RuntimeError, ValueError) as exc:
        log(f"오류: {exc}")
        sys.exit(1)
