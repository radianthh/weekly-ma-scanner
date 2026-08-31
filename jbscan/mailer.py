#!/usr/bin/env python3
"""리포트 메일 발송. 본문은 메일 클라이언트에서 그대로 읽히는 인라인 스타일 HTML."""

from __future__ import annotations

import argparse
import html
import mimetypes
import os
import smtplib
import ssl
import sys
from datetime import date, datetime
from email.message import EmailMessage
from email.utils import formataddr, formatdate
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

from .report import Changes, fmt_cap, fmt_price, render_text

INK = "#1a1a1a"
SUB = "#8a8a8a"
LINE = "#e8e8e8"
ACCENT = "#3b6cf6"

TD = f"padding:9px 10px;border:1px solid {LINE};text-align:right;font-size:14px"
TD_L = TD.replace("text-align:right", "text-align:left")
TD_C = TD.replace("text-align:right", "text-align:center")
TH = f"padding:9px 10px;border:1px solid {LINE};color:{SUB};font-weight:400;font-size:12.5px;text-align:right"
TH_L = TH.replace("text-align:right", "text-align:left")
TH_C = TH.replace("text-align:right", "text-align:center")
CODE = f"{TD};font-family:ui-monospace,Menlo,monospace;font-size:13px;color:#b8b8b8"
CARD = (
    f"background:#ffffff;border:1px solid {LINE};border-radius:14px;"
    "padding:20px 22px;margin-bottom:16px"
)


class MailError(RuntimeError):
    """메일 설정이 없거나 발송에 실패했을 때."""


class MailConfig:
    """.env에서 읽는 SMTP 설정. MAIL_TO가 없으면 발송을 건너뛴다."""

    def __init__(
        self,
        host: str,
        port: int,
        user: str,
        password: str,
        sender: str,
        sender_name: str,
        recipients: list[str],
        attach: bool,
    ) -> None:
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.sender = sender
        self.sender_name = sender_name
        self.recipients = recipients
        self.attach = attach

    @classmethod
    def from_env(cls, env_file: str = ".env") -> "MailConfig":
        load_dotenv(env_file)
        recipients = [
            address.strip()
            for address in os.getenv("MAIL_TO", "").replace(";", ",").split(",")
            if address.strip()
        ]
        if not recipients:
            raise MailError("MAIL_TO가 비어 있습니다. .env에 수신 주소를 설정하세요.")
        user = os.getenv("SMTP_USER", "").strip()
        password = os.getenv("SMTP_PASSWORD", "").strip()
        if not user or not password:
            raise MailError("SMTP_USER 또는 SMTP_PASSWORD가 비어 있습니다.")
        return cls(
            host=os.getenv("SMTP_HOST", "smtp.naver.com").strip(),
            port=int(os.getenv("SMTP_PORT", "465")),
            user=user,
            password=password,
            sender=os.getenv("MAIL_FROM", user).strip() or user,
            sender_name=os.getenv("MAIL_FROM_NAME", "주봉 정배열 스캐너").strip(),
            recipients=recipients,
            attach=os.getenv("MAIL_ATTACH", "1").strip().lower() not in {"0", "false", "no"},
        )


def _rows(frame: pd.DataFrame, columns: list[str]) -> str:
    """신규·이탈 표의 행. columns는 항상 종목명/시장/시가총액_억."""
    if frame.empty:
        return f'<tr><td colspan="4" style="{TD_C};color:{SUB}">해당 종목 없음</td></tr>'
    cells = []
    for ticker, row in frame.iterrows():
        cells.append(
            f'<tr><td style="{TD_L}">{html.escape(str(row[columns[0]]))}</td>'
            f'<td style="{CODE}">{ticker}</td>'
            f'<td style="{TD_C}">{row[columns[1]]}</td>'
            f'<td style="{TD}">{fmt_cap(float(row[columns[2]]))}</td></tr>'
        )
    return "".join(cells)


def _stat(label: str, value: str, unit: str) -> str:
    return (
        f'<td style="padding:4px 10px 4px 0;vertical-align:top">'
        f'<div style="color:{SUB};font-size:13px">{label}</div>'
        f'<div style="font-size:26px;font-weight:700;color:{INK}">{value}'
        f'<span style="font-size:13px;font-weight:400;color:{SUB};margin-left:3px">{unit}</span></div></td>'
    )


def build_subject(context: dict[str, object]) -> str:
    passed: pd.DataFrame = context["passed"]  # type: ignore[assignment]
    changes: Changes = context["changes"]  # type: ignore[assignment]
    asof: date = context["asof"]  # type: ignore[assignment]
    return (
        f"[주봉 정배열] {asof:%Y-%m-%d} · {len(passed)}종목 "
        f"(신규 {len(changes.new_df)} / 이탈 {len(changes.out_df)})"
    )


def build_html(context: dict[str, object]) -> str:
    """메일 클라이언트는 <style> 블록과 CSS 변수를 지원하지 않으므로 전부 인라인으로 둔다."""
    passed: pd.DataFrame = context["passed"]  # type: ignore[assignment]
    scanned: pd.DataFrame = context["scanned"]  # type: ignore[assignment]
    changes: Changes = context["changes"]  # type: ignore[assignment]
    asof: date = context["asof"]  # type: ignore[assignment]
    prev_friday: date = context["prev_friday"]  # type: ignore[assignment]
    generated: datetime = context["generated"]  # type: ignore[assignment]
    min_cap = int(context["min_cap"])

    count = len(passed)
    ratio = count / len(scanned) * 100 if len(scanned) else 0
    longest = int(passed["streak"].max()) if count else 0
    kospi = int((passed["시장"] == "KOSPI").sum())
    kosdaq = int((passed["시장"] == "KOSDAQ").sum())

    main_rows = []
    for ticker, row in passed.iterrows():
        plus = "+" if bool(row["streak_capped"]) else ""
        mark = (
            f'<span style="color:{ACCENT};font-weight:700">신규 </span>'
            if ticker in changes.new_tickers
            else ""
        )
        main_rows.append(
            f'<tr><td style="{TD_L}">{mark}{html.escape(str(row["종목명"]))}</td>'
            f'<td style="{CODE}">{ticker}</td>'
            f'<td style="{TD_C}">{row["시장"]}</td>'
            f'<td style="{TD_C};font-weight:700">{int(row["streak"])}주{plus}</td>'
            f'<td style="{TD}">{fmt_cap(float(row["시가총액_억"]))}</td>'
            f'<td style="{TD}">{fmt_price(float(row["close"]))}</td></tr>'
        )
    if not main_rows:
        main_rows.append(f'<tr><td colspan="6" style="{TD_C};color:{SUB}">해당 종목 없음</td></tr>')

    change_header = (
        f'<tr><th style="{TH_L}">종목</th><th style="{TH_L}">코드</th>'
        f'<th style="{TH_C}">시장</th><th style="{TH}">시가총액</th></tr>'
    )
    columns = ["종목명", "시장", "시가총액_억"]
    attach_note = (
        f'<p style="color:{SUB};font-size:13px;margin:0">'
        "전체 리포트 HTML과 CSV를 첨부했습니다. 분포 그래프와 산출 기준은 첨부 HTML에서 확인하세요.</p>"
        if context.get("attached")
        else ""
    )

    return f"""<div style="margin:0;padding:24px 12px;background:#fafafa;
font-family:-apple-system,BlinkMacSystemFont,'Apple SD Gothic Neo','Malgun Gothic',sans-serif;
color:{INK};font-size:15px;line-height:1.6">
<div style="max-width:900px;margin:0 auto">
<h1 style="font-size:26px;margin:0 0 8px">주봉 정배열 종목 {asof:%Y-%m-%d} 기준</h1>
<p style="color:{SUB};font-size:13px;margin:0 0 24px">{asof:%Y-%m-%d} 마감 기준 · 주봉 MA4 &gt; MA13 &gt; MA26 &gt; MA52 ·
KOSPI·KOSDAQ 시가총액 {min_cap:,}억원 이상 · {generated:%Y-%m-%d %H:%M} 작성</p>

<div style="{CARD}"><table role="presentation" width="100%" style="border-collapse:collapse"><tr>
{_stat("정배열 종목", str(count), "종목")}
{_stat("검토 대상", f"{len(scanned):,}", "종목")}
{_stat("비율", f"{ratio:.1f}", "%")}
{_stat("최장 유지", str(longest), "주")}
</tr></table></div>

<div style="{CARD}">
<h2 style="font-size:17px;margin:0 0 4px">지난주 대비 변화</h2>
<p style="color:{SUB};font-size:13px;margin:0 0 14px">직전 주({prev_friday:%Y-%m-%d}) 목록과 비교 · {changes.source}</p>
<p style="margin:0 0 8px"><b>▶ 신규 진입 {len(changes.new_df)}</b></p>
<table width="100%" style="border-collapse:collapse">{change_header}{_rows(changes.new_df, columns)}</table>
<p style="margin:22px 0 8px"><b>▼ 이탈 {len(changes.out_df)}</b></p>
<table width="100%" style="border-collapse:collapse">{change_header}{_rows(changes.out_df, columns)}</table>
</div>

<div style="{CARD}">
<h2 style="font-size:17px;margin:0 0 4px">주봉 정배열 종목 {count}개</h2>
<p style="color:{SUB};font-size:13px;margin:0 0 14px">유지 기간이 긴 순 · KOSPI {kospi} / KOSDAQ {kosdaq} ·
+ 는 보유 데이터 전 구간이 정배열이라 시작 시점이 더 이전임을 뜻합니다</p>
<table width="100%" style="border-collapse:collapse">
<tr><th style="{TH_L}">종목</th><th style="{TH_L}">코드</th><th style="{TH_C}">시장</th>
<th style="{TH_C}">유지</th><th style="{TH}">시가총액</th><th style="{TH}">종가</th></tr>
{''.join(main_rows)}</table>
</div>

<div style="{CARD}">
{attach_note}
<div style="border-left:3px solid {ACCENT};padding:2px 0 2px 14px;margin:14px 0 0;font-size:13.5px">
<b>이 보고서는 공개 시세를 기계적으로 집계한 결과이며 특정 종목의 매수·매도를 권유하지 않습니다.
이동평균 정배열은 과거 가격에서 계산되는 후행 지표이고, 투자 판단과 결과의 책임은 투자자 본인에게 있습니다.</b>
</div></div>

<p style="text-align:center;color:#bbb;font-size:12.5px;margin-top:28px">
기계적 스크리닝 결과이며 투자 권유가 아닙니다.</p>
</div></div>"""


def _attach(message: EmailMessage, path: Path) -> None:
    guessed, _ = mimetypes.guess_type(path.name)
    maintype, _, subtype = (guessed or "application/octet-stream").partition("/")
    if maintype == "text":
        # bytes로 넣으면 charset이 선언되지 않아 클라이언트가 us-ascii로 추측하고
        # 한글이 깨진다. str로 넣어 utf-8을 명시하고, 전송은 base64로 고정한다
        # (기본 8bit는 중간 서버가 모두 8BITMIME을 지원해야 원본이 보존된다).
        message.add_attachment(
            path.read_text(encoding="utf-8"),
            subtype=subtype,
            filename=path.name,
            cte="base64",
        )
    else:
        message.add_attachment(
            path.read_bytes(), maintype=maintype, subtype=subtype, filename=path.name
        )


def _auth_hint(host: str) -> str:
    """서버별로 인증이 막히는 흔한 원인을 알려준다."""
    if "naver" in host:
        return (
            "확인할 것: (1) 네이버 메일 > 환경설정 > POP3/IMAP 설정에서 "
            "'IMAP/SMTP 사용'이 켜져 있는지 (2) SMTP_USER가 @naver.com을 뺀 아이디인지 "
            "(3) 2단계 인증을 켰다면 계정 비밀번호가 아니라 애플리케이션 비밀번호인지."
        )
    return "SMTP_USER와 SMTP_PASSWORD, 그리고 해당 서비스의 외부 메일 프로그램 사용 설정을 확인하세요."


def deliver(message: EmailMessage, config: MailConfig) -> None:
    context = ssl.create_default_context()
    try:
        if config.port == 465:
            with smtplib.SMTP_SSL(config.host, config.port, context=context, timeout=60) as server:
                server.login(config.user, config.password)
                server.send_message(message)
        else:
            with smtplib.SMTP(config.host, config.port, timeout=60) as server:
                server.starttls(context=context)
                server.login(config.user, config.password)
                server.send_message(message)
    except smtplib.SMTPAuthenticationError as exc:
        detail = exc.smtp_error.decode("utf-8", "replace") if exc.smtp_error else ""
        raise MailError(
            f"SMTP 인증 실패 ({config.user}@{config.host}). "
            f"서버 응답: {exc.smtp_code} {detail}\n{_auth_hint(config.host)}"
        ) from exc
    except (smtplib.SMTPException, OSError) as exc:
        raise MailError(f"{exc.__class__.__name__}: {exc}") from exc


def send_report(
    context: dict[str, object],
    paths: dict[str, Path],
    config: MailConfig | None = None,
) -> list[str]:
    """리포트를 메일로 보내고 수신 주소를 돌려준다. 실패하면 MailError."""
    config = config or MailConfig.from_env()
    context = {**context, "attached": config.attach}

    message = EmailMessage()
    message["Subject"] = build_subject(context)
    message["From"] = formataddr((config.sender_name, config.sender))
    message["To"] = ", ".join(config.recipients)
    message["Date"] = formatdate(localtime=True)
    message.set_content(render_text(context))
    message.add_alternative(build_html(context), subtype="html")

    if config.attach:
        for key in ("html", "csv"):
            path = paths.get(key)
            if path and Path(path).exists():
                _attach(message, Path(path))

    deliver(message, config)
    return config.recipients


def send_check() -> list[str]:
    """SMTP 설정만 검증하는 짧은 메일. 스캔 없이 앱 비밀번호를 확인할 때 쓴다."""
    config = MailConfig.from_env()
    message = EmailMessage()
    message["Subject"] = "[주봉 정배열] 메일 설정 확인"
    message["From"] = formataddr((config.sender_name, config.sender))
    message["To"] = ", ".join(config.recipients)
    message["Date"] = formatdate(localtime=True)
    message.set_content(
        "주봉 정배열 스캐너의 SMTP 설정이 정상입니다.\n"
        f"발송 시각: {datetime.now():%Y-%m-%d %H:%M:%S}\n"
        "매주 금요일 18:00부터 스캔이 성공할 때까지 시도하고, 그 결과가 이 주소로 옵니다."
    )
    deliver(message, config)
    return config.recipients


def send_failure(reason: str, log_path: str | Path | None = None, tail: int = 25) -> list[str]:
    """스캔이 리포트를 만들지 못했을 때 알리는 짧은 메일.

    리포트 메일이 안 오는 날 그것이 정상인지 고장인지 구분되지 않는 문제를 막는다.
    """
    config = MailConfig.from_env()
    lines = [
        f"주봉 정배열 스캔이 리포트를 만들지 못했습니다.",
        "",
        f"사유     : {reason}",
        f"발생 시각: {datetime.now():%Y-%m-%d %H:%M:%S}",
        "",
        "리포트 메일은 오지 않습니다. 아래 로그를 확인하세요.",
    ]
    if log_path and Path(log_path).exists():
        recent = Path(log_path).read_text(encoding="utf-8", errors="replace").splitlines()
        lines += ["", f"--- {Path(log_path).name} 마지막 {tail}줄 ---", *recent[-tail:]]

    message = EmailMessage()
    message["Subject"] = f"[주봉 정배열] 실행 실패 — {datetime.now():%Y-%m-%d}"
    message["From"] = formataddr((config.sender_name, config.sender))
    message["To"] = ", ".join(config.recipients)
    message["Date"] = formatdate(localtime=True)
    message.set_content("\n".join(lines))
    deliver(message, config)
    return config.recipients


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="python -m jbscan.mailer", description="리포트 메일 발송 도우미"
    )
    parser.add_argument("--failure", metavar="사유", help="실패 알림 메일을 보낸다")
    parser.add_argument("--log", help="실패 알림에 덧붙일 로그 파일")
    args = parser.parse_args()
    try:
        if args.failure:
            sent = send_failure(args.failure, args.log)
            print(f"실패 알림 발송: {', '.join(sent)}")
        else:
            sent = send_check()
            print(f"확인 메일 발송: {', '.join(sent)}")
    except MailError as error:
        print(f"메일 발송 실패: {error}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
