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
> KRX 유니버스를 구성하기 전에 토큰부터 받아 봅니다. 실패하면 **즉시 중단**합니다(종료코드 3).
> 이 검사가 없으면 수 분짜리 KRX 조회를 버리고, 종목 수만큼 발급을 재시도해
> 유량 제한(HTTP 429)까지 맞습니다.

지금 이 컴퓨터가 등록된 IP인지만 묻고 싶다면:

```bash
.venv/bin/python -m jbscan --check-auth   # 등록 IP면 0, 아니면 3
```

캐시된 토큰은 **발급 시점의** IP로 받은 것이라 지금 막혀 있어도 통과합니다.
`--check-auth`는 그래서 캐시를 무시하고 새로 발급받습니다.

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

## 자동 실행 (금요일 18:00부터, 성공할 때까지)

`deploy/com.jbscan.weekly.plist.example`의 `/ABSOLUTE/PATH/jbscan`을 실제 경로로 바꾼 뒤:

```bash
cp deploy/com.jbscan.weekly.plist.example ~/Library/LaunchAgents/com.jbscan.weekly.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.jbscan.weekly.plist
launchctl print  gui/$(id -u)/com.jbscan.weekly   # 확인
launchctl bootout gui/$(id -u)/com.jbscan.weekly  # 해제
```

`Weekday`는 0과 7이 일요일이라 **금요일은 5**입니다.

> **프로젝트를 `~/Documents`·`~/Desktop`·`~/Downloads` 안에 두지 마세요.** macOS 개인정보
> 보호(TCC)가 launchd에서 실행된 프로세스의 이 폴더 접근을 막습니다. 터미널에서는 잘 돌아가는데
> 예약 실행만 `Operation not permitted`(종료코드 126)로 죽고, 화면에는 아무 것도 뜨지 않습니다.
> 홈 디렉터리 바로 아래(`~/jbscan` 등)에는 제한이 없습니다. `~/Documents`는 iCloud 동기화
> 대상이기도 해서, 파일이 클라우드로 비워지면 예약 실행이 또 다른 이유로 실패합니다.

`launchctl kickstart gui/$(id -u)/com.jbscan.weekly`로 즉시 한 번 돌려 볼 수 있습니다.
`launchctl print gui/$(id -u)/com.jbscan.weekly | grep 'last exit code'`가 0이 아니면
`run.log`를 보세요.

### 왜 한 번만 시도하지 않는가

키움은 등록된 공인 IP에서만 호출됩니다. 금요일 저녁에 그 IP에 앉아 있으리란 보장이 없고,
맥이 꺼져 있거나 잠자기일 수도 있습니다. 그래서 launchd는 금요일 18:00에 한 번,
그리고 **30분마다** `run_weekly.sh`를 부릅니다. 스크립트는 매번 이것만 확인합니다:

1. `reports/history/{기준 금요일}.json`이 이미 있으면 → 아무 것도 하지 않고 끝냅니다.
2. 재시도 창(금요일 18:00 + 72시간, 즉 월요일 18:00)을 넘겼으면 → 아래 실패 알림.
3. 아니면 `--check-auth`로 인증만 확인하고, 통과할 때만 스캔합니다.

인증이 막혀 있으면 **메일을 보내지 않고** 로그 한 줄만 남긴 뒤 다음 호출을 기다립니다.
금요일 18시에 다른 망에 있었고 일요일 낮에 집에 돌아왔다면, 그 시점의 호출에서 스캔이
돌고 리포트 메일이 옵니다. 기준일은 그대로 그 주 금요일입니다.

첫 시도가 16:00이 아니라 18:00인 이유는 15:30 마감 뒤 KRX 일별 시세·시가총액이 반영될
시간이 필요해서입니다. 그래도 미반영이면(종료코드 2) 마찬가지로 조용히 다음 호출에서
다시 시도합니다.

시각은 환경변수로 조절합니다 — `JBSCAN_START_HOUR`(기본 18), `JBSCAN_RETRY_HOURS`(기본 72).

### 실패 알림

**재시도 창을 다 쓰고도 리포트를 만들지 못했을 때 한 번만** 실패 메일을 보냅니다 —
아무 메일도 오지 않으면 정상인지 고장인지 구분할 수 없기 때문입니다. 본문에 `run.log`
마지막 25줄이 실립니다. 창 안에서 한 번도 시도하지 못한 주(설치 직후 등)는 건너뜁니다.

상태 파일은 `.state/`에 남습니다(`tried-*`, `gaveup-*`, 실행 중 잠금). Git에서 제외됩니다.

## 구조

```text
run_weekly.sh              30분마다 호출되는 게이트: 재시도·실패 알림 (launchd 진입점)
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
