"""주봉 정규화와 이동평균 정배열 판정 로직."""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

MA_WINDOWS = (4, 13, 26, 52)


def last_friday(ref: date | None = None) -> date:
    """ref 이전(포함)의 가장 최근 금요일."""
    ref = ref or date.today()
    return ref - timedelta(days=(ref.weekday() - 4) % 7)


def week_monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def friday_label(value: date | pd.Timestamp) -> pd.Timestamp:
    """같은 월~금 주의 금요일 00:00 라벨로 정규화."""
    ts = pd.Timestamp(value).normalize()
    return pd.Timestamp(ts.date() + timedelta(days=int((4 - ts.weekday()) % 7)))


def daily_to_weekly(close: pd.Series) -> pd.Series:
    """일봉 종가를 금요일 라벨의 주봉 종가로 변환."""
    if close.empty:
        return close.astype("float64")
    out = close.astype(float).sort_index().dropna().copy()
    out.index = pd.DatetimeIndex([friday_label(value) for value in out.index])
    out = out.groupby(level=0).last()
    out.name = "close"
    return out


def normalize_weekly(close: pd.Series) -> pd.Series:
    """API 주봉 날짜가 주초/주말 어느 쪽이어도 금요일 라벨로 통일."""
    if close.empty:
        return close.astype("float64")
    normalized = close.astype(float).copy()
    normalized.index = pd.DatetimeIndex([friday_label(v) for v in normalized.index])
    normalized = normalized[~normalized.index.duplicated(keep="last")].sort_index()
    normalized.name = "close"
    return normalized


def evaluate(weekly: pd.Series, asof_friday: date) -> dict[str, object] | None:
    """정배열 여부, 직전 주 여부, 연속 유지 주수와 시작 주를 계산."""
    w = normalize_weekly(weekly)
    w = w[w.index <= pd.Timestamp(asof_friday)]

    # MA52와 직전 주 판정을 모두 확보하려면 최소 53개 주봉이 필요하다.
    if len(w) < max(MA_WINDOWS) + 1:
        return None

    mas = {window: w.rolling(window).mean() for window in MA_WINDOWS}
    ma4, ma13, ma26, ma52 = (mas[n] for n in MA_WINDOWS)
    aligned = (ma4 > ma13) & (ma13 > ma26) & (ma26 > ma52)
    aligned = aligned[ma52.notna()]
    if aligned.empty:
        return None

    now = bool(aligned.iloc[-1])
    previous = bool(aligned.iloc[-2]) if len(aligned) >= 2 else False
    streak = 0
    capped = False
    start_week: date | None = None

    if now:
        values = aligned.to_numpy()
        pos = len(values) - 1
        while pos >= 0 and bool(values[pos]):
            streak += 1
            pos -= 1
        capped = pos < 0
        first = aligned.index[len(values) - streak].date()
        start_week = week_monday(first)

    return {
        "aligned": now,
        "aligned_prev": previous,
        "streak": streak,
        "streak_capped": capped,
        "start_week": start_week,
        "close": float(w.iloc[-1]),
        "week_end": w.index[-1].date(),
    }
