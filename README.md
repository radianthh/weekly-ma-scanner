# 주봉 정배열 스캐너

매주 금요일 마감 기준으로 KOSPI·KOSDAQ 시가총액 3,000억원 이상 종목 중
`주봉 MA4 > MA13 > MA26 > MA52`인 종목을 찾아 HTML 리포트를 만들고 메일로 보냅니다.

- **종목·시가총액**: KRX 정보데이터시스템 (`pykrx`)
- **수정주가 주봉**: 키움증권 REST API
- **출력**: `reports/{YYYYMMDD}_주봉_정배열.{html,csv,txt}` + `reports/history/{날짜}.json`

## 설치

Python 3.11 권장.

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

## 설정 (`.env`)

### 키움 · KRX

[키움 REST API 포털](https://openapi.kiwoom.com/)에서 운영 App Key를 발급하고
**호출할 컴퓨터의 공인 IP를 등록**해야 합니다. `pykrx`의 로그인 세션 때문에 KRX 계정도 필요합니다.

> **IP는 실행 시점마다 검사됩니다.** 등록되지 않은 IP에서 부르면 토큰 발급이
> `8050:지정단말기 인증에 실패`로 거절됩니다. 가정용 회선은 유동 IP라 공유기 재부팅이나
> ISP 임대 갱신으로 바뀔 수 있고, 노트북을 다른 망(카페·회사·테더링)으로 옮겨도 실패합니다.
> 현재 IP는 `curl -s https://api.ipify.org`로 확인합니다.
>
> 스캔 전에 토큰을 한 번 받아 보고 실패하면 **즉시 중단**합니다(종료코드 3). 이 검사가 없으면
> 종목 수만큼 발급을 재시도해 유량 제한(HTTP 429)까지 맞습니다.

```dotenv
KIWOOM_APP_KEY=발급받은_App_Key
KIWOOM_APP_SECRET=발급받은_Secret_Key
KRX_ID=KRX_아이디
KRX_PW=KRX_비밀번호
```

### 메일 (네이버 기준)

```dotenv
MAIL_TO=받는주소@naver.com
SMTP_USER=네이버아이디
SMTP_PASSWORD=계정_또는_앱_비밀번호
MAIL_FROM=네이버아이디@naver.com
```

- 네이버 메일 → 환경설정 → **POP3/IMAP 설정**에서 "IMAP/SMTP 사용"을 먼저 켜야 합니다.
  꺼져 있으면 비밀번호가 맞아도 `535`로 막힙니다.
- 2단계 인증을 켰다면 계정 비밀번호가 아니라 **애플리케이션 비밀번호**가 필요합니다.
- `SMTP_USER`는 `@naver.com`을 뺀 아이디이므로 `MAIL_FROM`을 반드시 적으세요.
- `MAIL_TO`가 비어 있으면 발송을 건너뜁니다. 쉼표로 여러 주소를 넣을 수 있습니다.

설정만 확인하려면 (스캔 없이 메일 한 통):

```bash
.venv/bin/python -m jbscan.mailer
```

`.env`와 `.kiwoom_token_cache.json`은 Git에서 제외됩니다. 키를 커밋하거나 로그에 남기지 마세요.

## 실행

```bash
python -m jbscan                           # 최근 금요일 기준
python -m jbscan --date 2026-08-14 --refresh
python -m jbscan --dry-run                 # 유니버스만, 키움 호출 없음
```

## 자동 실행 (매주 금요일 16:00 KST)

`deploy/com.jbscan.weekly.plist.example`의 `/ABSOLUTE/PATH/jbscan`을 실제 경로로 바꾼 뒤:

```bash
cp deploy/com.jbscan.weekly.plist.example ~/Library/LaunchAgents/com.jbscan.weekly.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.jbscan.weekly.plist
launchctl print  gui/$(id -u)/com.jbscan.weekly   # 확인
launchctl bootout gui/$(id -u)/com.jbscan.weekly  # 해제
```

`Weekday`는 0과 7이 일요일이라 **금요일은 5**입니다. 맥이 꺼져 있으면 실행되지 않고,
잠자기였다면 깨어난 직후 한 번 실행됩니다.

`run_weekly.sh`는 데이터 미반영(종료코드 2)일 때만 10분 간격으로 3회 재시도합니다(총 4회).
**리포트를 만들지 못하면 실패 알림 메일을 보냅니다** — 아무 메일도 오지 않으면 정상인지
고장인지 구분할 수 없기 때문입니다. 본문에 `run.log` 마지막 25줄이 실립니다.

## 구조

```text
run_weekly.sh              금요일 실행·재시도·실패 알림 (launchd 진입점)
jbscan/
  __main__.py              CLI (python -m jbscan)
  kiwoom_client.py         키움 인증·유량제한·주봉·캐시
  universe.py              pykrx 유니버스와 종목명
  screen.py                주봉 정규화·MA·유지 기간
  report.py                HTML/CSV/TXT/JSON 생성
  mailer.py                메일 발송과 실패 알림
deploy/                    launchd plist 템플릿
reports/                   출력 (Git 추적 제외)
```

`run_weekly.sh`는 launchd plist가 절대경로로 가리키므로 루트에 있어야 합니다.
