# 주봉 정배열 스캐너

매주 금요일 마감 기준으로 KOSPI·KOSDAQ 시가총액 3,000억원 이상 종목 중
`주봉 MA4 > MA13 > MA26 > MA52`인 종목을 찾아 HTML 리포트를 만들고 메일로 보냅니다.

- **종목·시가총액**: KRX 정보데이터시스템 (`pykrx`)
- **수정주가 주봉**: 키움증권 REST API
- **출력**: `reports/{YYYYMMDD}_주봉_정배열.{html,csv,txt}` + `reports/history/{날짜}.json`

데이터 출처는 이 둘뿐입니다. HTTP 기반 키움 REST API를 쓰므로 Windows 전용 OpenAPI+와 달리
macOS·Linux에서 실행됩니다.

> 기계적 스크리너이며 투자 권유가 아닙니다. 이동평균 정배열은 후행 지표이고,
> 투자 판단과 결과의 책임은 투자자 본인에게 있습니다.

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
- 선택: `SMTP_HOST`(기본 `smtp.naver.com`), `SMTP_PORT`(기본 `465`, `587`이면 STARTTLS),
  `MAIL_FROM_NAME`, `MAIL_ATTACH=0`(첨부 없이 본문만).

설정만 확인하려면 (스캔 없이 메일 한 통):

```bash
.venv/bin/python mailer.py
```

`.env`와 `.kiwoom_token_cache.json`은 Git에서 제외됩니다. 키를 커밋하거나 로그에 남기지 마세요.

## 실행

```bash
python jb_scan.py                          # 최근 금요일 기준
python jb_scan.py --date 2026-08-14 --refresh
python jb_scan.py --dry-run                # 유니버스만, 키움 호출 없음
```

| 옵션 | 기본값 | 설명 |
|---|---:|---|
| `--min-cap` | 3000 | 시가총액 하한(억원) |
| `--years` | 4 | 주봉 조회 기간 |
| `--rps` | 8 | 키움 초당 요청 수 |
| `--workers` | 4 | 동시 작업 수(최대 4) |
| `--refresh` | off | 캐시 무시하고 재조회 |
| `--strict-date` | off | 기준일 데이터 미반영 시 종료코드 2 |
| `--dry-run` | off | 유니버스만 만들고 종료 |
| `--no-mail` | off | 리포트만 만들고 메일 생략 |

## 자동 실행 (매주 금요일 16:00 KST)

`com.jbscan.weekly.plist.example`의 `/ABSOLUTE/PATH/jbscan`을 실제 경로로 바꾼 뒤:

```bash
cp com.jbscan.weekly.plist.example ~/Library/LaunchAgents/com.jbscan.weekly.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.jbscan.weekly.plist
launchctl print  gui/$(id -u)/com.jbscan.weekly   # 확인
launchctl bootout gui/$(id -u)/com.jbscan.weekly  # 해제
```

`Weekday`는 0과 7이 일요일이라 **금요일은 5**입니다. 맥이 꺼져 있으면 실행되지 않고,
잠자기였다면 깨어난 직후 한 번 실행됩니다.

`run_weekly.sh`는 데이터 미반영(종료코드 2)일 때만 10분 간격으로 3회 재시도합니다(총 4회).
**리포트를 만들지 못하면 실패 알림 메일을 보냅니다** — 아무 메일도 오지 않으면 정상인지
고장인지 구분할 수 없기 때문입니다. 본문에 `run.log` 마지막 25줄이 실립니다.

## 판정·제외 규칙

- 주봉 종가는 월~금 중 마지막 거래일 종가. 내부 라벨은 금요일, 시작 주 표시는 월요일.
- MA52가 계산되는 **주봉 53개 이상**을 상장 1년 이상으로 간주합니다.
- 유지 기간은 기준 주에서 과거로 연속된 정배열 주 수입니다. 보유 데이터 전체가 정배열이면 `+`.
- 우선주·스팩·리츠·ETF·ETN·기준일 거래량 0을 제외합니다.
- 관리종목은 pykrx에 API가 없어 `exclude.txt`로 직접 관리합니다. **주기적으로 갱신하세요.**
- 정렬은 유지 기간 내림차순, 동률이면 시가총액 내림차순.
- 직전 주 `reports/history/{날짜}.json`과 비교해 신규·이탈을 만듭니다.

> **스팩·리츠·ETN 제외는 종목명으로 판정합니다.** 종목명 조회에 실패하면 코드가 남아 필터를
> 빠져나가므로, 실패 시 한 번 재시도하고 그래도 안 되면 경고를 남깁니다. `run.log`에
> `경고: 종목명 조회 실패`가 보이면 그 종목들은 필터가 적용되지 않은 것입니다.

## 구조

```text
jb_scan.py         진입점 (스캔 → 리포트 → 메일)
kiwoom_client.py   키움 인증·유량제한·주봉·캐시
universe.py        pykrx 유니버스와 종목명
screen.py          주봉 정규화·MA·유지 기간
report.py          HTML/CSV/TXT/JSON 생성
mailer.py          메일 발송과 실패 알림
run_weekly.sh      금요일 실행·재시도·실패 알림
```
