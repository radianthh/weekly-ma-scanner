"""키움증권 REST API 인증, 차트 조회, 유량 제한과 주봉 캐시."""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
import requests
from dotenv import load_dotenv

from .screen import normalize_weekly

BASE_URL = "https://api.kiwoom.com"
TOKEN_PATH = "/oauth2/token"
CHART_PATH = "/api/dostk/chart"
DAILY_API_ID = "ka10081"
WEEKLY_API_ID = "ka10082"

# 키움 공식 ka10081/ka10082 예제는 1을 사용한다. verify_kiwoom.py가 삼성전자
# 2018년 액면분할 구간을 0/1로 실측하여 이 값이 수정주가인지 재확인한다.
ADJUSTED_PRICE_FLAG = "1"


def log(message: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {message}", flush=True)


class KiwoomError(RuntimeError):
    def __init__(self, message: str, code: str = ""):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class KiwoomConfig:
    app_key: str
    app_secret: str
    base_url: str = BASE_URL

    @classmethod
    def from_env(cls, env_file: str | Path = ".env") -> "KiwoomConfig":
        load_dotenv(env_file)
        app_key = os.getenv("KIWOOM_APP_KEY", "").strip()
        app_secret = os.getenv("KIWOOM_APP_SECRET", "").strip()
        base_url = os.getenv("KIWOOM_BASE_URL", BASE_URL).strip().rstrip("/")
        if not app_key or not app_secret:
            raise KiwoomError(
                "KIWOOM_APP_KEY/KIWOOM_APP_SECRET이 없습니다. "
                ".env 또는 환경변수에 설정하세요."
            )
        return cls(app_key=app_key, app_secret=app_secret, base_url=base_url)


class TokenBucket:
    """여러 작업자가 공유하는 스레드 안전 토큰 버킷."""

    def __init__(self, rate: float, capacity: float | None = None):
        if rate <= 0:
            raise ValueError("rps는 0보다 커야 합니다.")
        self.rate = float(rate)
        self.capacity = float(capacity or max(1.0, rate))
        self.tokens = self.capacity
        self.updated = time.monotonic()
        self.lock = threading.Lock()

    def acquire(self) -> None:
        while True:
            with self.lock:
                now = time.monotonic()
                self.tokens = min(
                    self.capacity, self.tokens + (now - self.updated) * self.rate
                )
                self.updated = now
                if self.tokens >= 1:
                    self.tokens -= 1
                    return
                wait = (1 - self.tokens) / self.rate
            time.sleep(wait)


def _number(value: Any) -> float:
    """키움 가격의 +/- 방향 표시는 제거하고 절댓값 가격으로 변환."""
    text = str(value or "0").replace(",", "").strip()
    return abs(float(text or 0))


def parse_price_rows(rows: list[dict[str, Any]]) -> pd.DataFrame:
    """키움 차트 문자열 배열을 날짜 인덱스 OHLCV 표로 변환."""
    parsed: list[dict[str, object]] = []
    for row in rows:
        try:
            parsed.append(
                {
                    "date": pd.to_datetime(str(row.get("dt", "")), format="%Y%m%d"),
                    "open": _number(row.get("open_pric")),
                    "high": _number(row.get("high_pric")),
                    "low": _number(row.get("low_pric")),
                    "close": _number(row.get("cur_prc")),
                    "volume": int(_number(row.get("trde_qty"))),
                }
            )
        except (TypeError, ValueError):
            continue
    if not parsed:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    frame = pd.DataFrame(parsed).set_index("date")
    frame = frame[frame["close"] > 0]
    return frame[~frame.index.duplicated(keep="last")].sort_index()


def merge_price_pages(pages: list[list[dict[str, Any]]]) -> pd.DataFrame:
    """페이지들을 합쳐 날짜 중복을 제거한다. 뒤 페이지 값이 우선한다."""
    frames = [parse_price_rows(page) for page in pages if page]
    if not frames:
        return parse_price_rows([])
    merged = pd.concat(frames)
    return merged[~merged.index.duplicated(keep="last")].sort_index()


def _chart_rows(body: dict[str, Any], api_id: str) -> list[dict[str, Any]]:
    key = {
        DAILY_API_ID: "stk_dt_pole_chart_qry",
        WEEKLY_API_ID: "stk_wk_pole_chart_qry",
    }.get(api_id, "")
    value = body.get(key) if key else None
    if isinstance(value, list):
        return [row for row in value if isinstance(row, dict)]
    # 문서 개정으로 키 이름이 바뀌더라도 명확한 차트 배열 하나는 안전하게 찾는다.
    candidates = [
        item
        for item in body.values()
        if isinstance(item, list)
        and (not item or (isinstance(item[0], dict) and "dt" in item[0] and "cur_prc" in item[0]))
    ]
    if len(candidates) == 1:
        return [row for row in candidates[0] if isinstance(row, dict)]
    raise KiwoomError(f"{api_id} 차트 배열을 응답에서 찾지 못했습니다.")


class KiwoomClient:
    def __init__(
        self,
        config: KiwoomConfig,
        cache_dir: str | Path = ".cache",
        token_cache: str | Path = ".kiwoom_token_cache.json",
        rps: float = 8,
        timeout: float = 30,
        session: requests.Session | None = None,
    ):
        self.config = config
        self.cache_dir = Path(cache_dir)
        self.weekly_cache = self.cache_dir / "kiwoom_weekly"
        self.weekly_cache.mkdir(parents=True, exist_ok=True)
        self.token_cache = Path(token_cache)
        self.timeout = timeout
        self.session = session or requests.Session()
        self.limiter = TokenBucket(rps)
        self._token: str | None = None
        self._expires_at = 0.0
        self._token_lock = threading.Lock()

    def _load_cached_token(self) -> bool:
        try:
            payload = json.loads(self.token_cache.read_text(encoding="utf-8"))
            token = str(payload["token"])
            expires_at = float(payload["expires_at"])
        except (FileNotFoundError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            return False
        if expires_at - time.time() <= 600:
            return False
        self._token, self._expires_at = token, expires_at
        return True

    def _save_token(self, token: str, expires_at: float) -> None:
        self.token_cache.parent.mkdir(parents=True, exist_ok=True)
        payload = {"token": token, "expires_at": expires_at}
        temp = self.token_cache.with_suffix(self.token_cache.suffix + ".tmp")
        temp.write_text(json.dumps(payload), encoding="utf-8")
        try:
            os.chmod(temp, 0o600)
        except OSError:
            pass
        temp.replace(self.token_cache)

    def _issue_token(self) -> str:
        try:
            response = self.session.post(
                self.config.base_url + TOKEN_PATH,
                headers={"content-type": "application/json;charset=UTF-8"},
                json={
                    "grant_type": "client_credentials",
                    "appkey": self.config.app_key,
                    "secretkey": self.config.app_secret,
                },
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise KiwoomError(
                f"키움 토큰 발급 네트워크 오류: {exc.__class__.__name__}"
            ) from exc
        if response.status_code != 200:
            raise KiwoomError(f"키움 토큰 발급 실패: HTTP {response.status_code}")
        try:
            body = response.json()
        except ValueError as exc:
            raise KiwoomError("키움 토큰 JSON 응답 해석 실패") from exc
        code = str(body.get("return_code", ""))
        if code not in {"0", ""}:
            raise KiwoomError(
                f"키움 토큰 발급 실패 {code}: {body.get('return_msg', '알 수 없는 오류')}",
                code=code,
            )
        try:
            token = str(body["token"])
        except (KeyError, TypeError) as exc:
            raise KiwoomError("키움 토큰 응답 형식이 올바르지 않습니다.") from exc
        expires_at = time.time() + 86400
        expires_text = str(body.get("expires_dt", ""))
        if expires_text:
            try:
                expires = datetime.strptime(expires_text, "%Y%m%d%H%M%S")
                expires_at = expires.replace(tzinfo=ZoneInfo("Asia/Seoul")).timestamp()
            except ValueError:
                pass
        self._token, self._expires_at = token, expires_at
        self._save_token(token, expires_at)
        log("키움 접근토큰 발급 및 캐시 완료")
        return token

    def preflight(self, force: bool = False) -> None:
        """스캔 전에 토큰을 한 번 받아 본다.

        인증 실패는 재시도로 풀리지 않는다. 이 검사가 없으면 종목 수만큼
        토큰 발급을 시도해 유량 제한(HTTP 429)까지 맞는다.

        force=True면 캐시를 무시하고 새로 발급받는다. 캐시된 토큰은 발급 시점의
        IP로 받은 것이라, 지금 IP가 등록돼 있는지 묻는 검사에서는 답이 되지 않는다.
        """
        try:
            self.access_token(force=force)
        except KiwoomError as exc:
            if "8050" in str(exc) or "지정단말기" in str(exc):
                raise KiwoomError(
                    f"{exc}\n"
                    "  호출한 컴퓨터의 공인 IP가 키움에 등록되어 있지 않습니다.\n"
                    "  https://openapi.kiwoom.com/ 에서 현재 공인 IP를 등록하세요.\n"
                    "  현재 IP는 `curl -s https://api.ipify.org` 로 확인합니다.",
                    code=exc.code,
                ) from exc
            raise

    def access_token(self, force: bool = False) -> str:
        with self._token_lock:
            if not force and self._token and self._expires_at - time.time() > 600:
                return self._token
            if not force and self._load_cached_token():
                return str(self._token)
            return self._issue_token()

    def _refresh_after_auth_failure(self, failed_token: str) -> None:
        """동시 인증 실패에서도 다른 스레드가 갱신한 토큰은 재발급하지 않는다."""
        with self._token_lock:
            if (
                self._token
                and self._token != failed_token
                and self._expires_at - time.time() > 600
            ):
                return
            self._token, self._expires_at = None, 0.0
            self._issue_token()

    def _post_chart_page(
        self,
        ticker: str,
        base_date: date,
        api_id: str,
        adjusted_flag: str,
        cont_yn: str = "",
        next_key: str = "",
    ) -> tuple[list[dict[str, Any]], dict[str, str]]:
        payload = {
            "stk_cd": ticker,
            "base_dt": base_date.strftime("%Y%m%d"),
            "upd_stkpc_tp": adjusted_flag,
        }
        token_retried = False
        for attempt in range(5):
            self.limiter.acquire()
            token = self.access_token()
            headers = {
                "content-type": "application/json;charset=UTF-8",
                "authorization": f"Bearer {token}",
                "api-id": api_id,
            }
            if cont_yn and next_key:
                headers.update({"cont-yn": cont_yn, "next-key": next_key})
            try:
                response = self.session.post(
                    self.config.base_url + CHART_PATH,
                    headers=headers,
                    json=payload,
                    timeout=self.timeout,
                )
            except requests.RequestException as exc:
                if attempt == 4:
                    raise KiwoomError(
                        f"{ticker} 네트워크 오류: {exc.__class__.__name__}"
                    ) from exc
                time.sleep(2**attempt)
                continue

            if response.status_code == 401 and not token_retried:
                self._refresh_after_auth_failure(token)
                token_retried = True
                continue
            if response.status_code == 429 or 500 <= response.status_code < 600:
                if attempt == 4:
                    raise KiwoomError(f"{ticker} 조회 실패: HTTP {response.status_code}")
                log(f"{ticker} HTTP {response.status_code} · {2**attempt}초 후 재시도")
                time.sleep(2**attempt)
                continue
            if response.status_code != 200:
                raise KiwoomError(f"{ticker} 조회 실패: HTTP {response.status_code}")

            try:
                body = response.json()
            except ValueError as exc:
                raise KiwoomError(f"{ticker} JSON 응답 해석 실패") from exc
            code = str(body.get("return_code", ""))
            message = str(body.get("return_msg", "알 수 없는 키움 오류"))
            if code == "0":
                return _chart_rows(body, api_id), {
                    "cont_yn": str(response.headers.get("cont-yn", "")),
                    "next_key": str(response.headers.get("next-key", "")),
                }
            if not token_retried and any(word in message for word in ("토큰", "인증")):
                self._refresh_after_auth_failure(token)
                token_retried = True
                continue
            retryable = any(
                word in message for word in ("요청 횟수", "호출 제한", "과도한", "잠시 후")
            )
            if retryable and attempt < 4:
                log(f"{ticker} 유량 제한 {code}: {message} · {2**attempt}초 후 재시도")
                time.sleep(2**attempt)
                continue
            raise KiwoomError(f"{ticker} {code}: {message}", code=code)
        raise KiwoomError(f"{ticker} 조회 재시도 한도 초과")

    def fetch_period(
        self,
        ticker: str,
        start: date,
        end: date,
        api_id: str = WEEKLY_API_ID,
        adjusted_flag: str = ADJUSTED_PRICE_FLAG,
    ) -> tuple[pd.DataFrame, list[dict[str, str]]]:
        """응답 헤더의 cont-yn/next-key로 연속조회하고 요청 구간만 반환."""
        pages: list[list[dict[str, Any]]] = []
        page_meta: list[dict[str, str]] = []
        cont_yn = next_key = ""
        seen_keys: set[str] = set()

        for _ in range(100):
            rows, meta = self._post_chart_page(
                ticker, end, api_id, adjusted_flag, cont_yn, next_key
            )
            page_meta.append(meta)
            if not rows:
                break
            pages.append(rows)
            dates: list[date] = []
            for row in rows:
                try:
                    dates.append(datetime.strptime(str(row["dt"]), "%Y%m%d").date())
                except (KeyError, TypeError, ValueError):
                    continue
            if dates and min(dates) <= start:
                break
            cont_yn, next_key = meta["cont_yn"], meta["next_key"]
            if cont_yn.upper() != "Y" or not next_key:
                break
            if next_key in seen_keys:
                raise KiwoomError(f"{ticker} 연속조회 키가 반복되었습니다.")
            seen_keys.add(next_key)
        else:
            raise KiwoomError(f"{ticker} 연속조회 100페이지 한도를 초과했습니다.")

        frame = merge_price_pages(pages)
        if frame.empty:
            return frame, page_meta
        mask = (frame.index.date >= start) & (frame.index.date <= end)
        return frame[mask], page_meta

    def weekly_close(
        self,
        ticker: str,
        start: date,
        end: date,
        refresh: bool = False,
    ) -> pd.Series:
        path = self.weekly_cache / f"{ticker}.csv"
        cached = pd.Series(dtype="float64", name="close")
        if path.exists() and not refresh:
            try:
                cached_frame = pd.read_csv(path, index_col=0, parse_dates=True)
                cached = normalize_weekly(cached_frame["close"].astype(float))
            except (OSError, KeyError, ValueError):
                cached = pd.Series(dtype="float64", name="close")

        if refresh or cached.empty:
            fetch_start = start
        elif cached.index.max().date() > end:
            return cached[(cached.index.date >= start) & (cached.index.date <= end)]
        else:
            # 현재 주의 부분 데이터와 소급 정정을 덮어쓸 수 있도록 한 주 겹쳐 받는다.
            fetch_start = max(start, cached.index.max().date() - timedelta(days=7))

        fresh_frame, _ = self.fetch_period(ticker, fetch_start, end)
        fresh = (
            normalize_weekly(fresh_frame["close"])
            if not fresh_frame.empty
            else cached.iloc[0:0]
        )
        retained = cached[~cached.index.isin(fresh.index)]
        if retained.empty:
            combined = fresh.copy()
        elif fresh.empty:
            combined = retained.copy()
        else:
            combined = pd.concat([retained, fresh]).sort_index()
        combined = combined[~combined.index.duplicated(keep="last")]
        if not combined.empty:
            combined.rename("close").to_frame().to_csv(path)
        return combined[(combined.index.date >= start) & (combined.index.date <= end)]
