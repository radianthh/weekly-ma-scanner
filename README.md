# 주봉 정배열 스캐너 — 키움증권 REST API

매주 금요일 마감 기준으로 KOSPI·KOSDAQ 시가총액 3,000억원 이상 종목의
`주봉 MA4 > MA13 > MA26 > MA52`를 판정해 단일 HTML, CSV, TXT와 주간 JSON 스냅샷을 만듭니다.

- 종목 목록·종목명·시장·시가총액: KRX 정보데이터시스템(`pykrx`)
- 수정주가 주봉: 키움증권 REST API
- 결과: `reports/{YYYYMMDD}_주봉_정배열.*`

데이터 출처는 이 둘뿐입니다. 종목명도 `pykrx`로 개별 조회하며, 4스레드로 665종목에
약 6초가 걸립니다.

Windows 전용 OpenAPI+가 아니라 HTTP 기반 **키움 REST API**를 사용하므로 macOS와 Linux에서도
실행할 수 있습니다. 이 프로그램은 기계적 스크리너이며 투자 권유가 아닙니다.

## 1. 설치와 키 설정

Python 3.11을 권장합니다.

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

[키움 REST API 포털](https://openapi.kiwoom.com/)에서 서비스 신청 후 운영 App Key와 Secret Key를
발급하고, 호출할 컴퓨터의 **공인 IP를 등록**합니다. `.env`를 열어 아래 값을 입력하세요.

```dotenv
KIWOOM_APP_KEY=발급받은_App_Key
KIWOOM_APP_SECRET=발급받은_Secret_Key
KRX_ID=KRX_정보데이터시스템_아이디
KRX_PW=KRX_정보데이터시스템_비밀번호
```

`pykrx>=1.2.8`의 현재 KRX 로그인 세션 때문에 KRX 정보데이터시스템 계정도 필요합니다.
`.env`와 `.kiwoom_token_cache.json`은 Git에서 제외됩니다. 키·Secret·토큰을 커밋하거나 로그에
출력하지 마세요. 토큰은 만료 10분 전까지 파일에서 재사용합니다.

모의투자 키로 시험할 때만 다음 값을 추가합니다. 운영 키와 모의 키는 서로 호환되지 않습니다.

```dotenv
KIWOOM_BASE_URL=https://mockapi.kiwoom.com
```

## 2. 실행

```bash
source .venv/bin/activate
python jb_scan.py
python jb_scan.py --date 2026-08-14 --refresh
```

유니버스만 확인하려면 키움 호출 없이 실행할 수 있습니다.

```bash
python jb_scan.py --dry-run
```

주요 옵션:

| 옵션 | 기본값 | 설명 |
|---|---:|---|
| `--min-cap` | 3000 | 시가총액 하한(억원) |
| `--years` | 4 | 주봉 조회 기간 |
| `--rps` | 8 | 키움 REST 초당 요청 수(보수적 클라이언트 제한) |
| `--workers` | 4 | 동시 작업 수(최대 4) |
| `--out-dir` | reports | 출력 폴더 |
| `--cache-dir` | .cache | 캐시 상위 폴더 |
| `--refresh` | off | 캐시를 무시하고 전체 재조회 |
| `--exclude-file` | exclude.txt | 추가 제외 코드 목록 |
| `--strict-date` | off | 기준일 데이터 미반영 시 종료코드 2 |
| `--dry-run` | off | KRX 유니버스만 만들고 키움 호출 없이 종료 |
| `--no-mail` | off | 리포트만 만들고 메일은 보내지 않음 |

키움 `ka10082` 응답 헤더의 `cont-yn: Y`와 `next-key`를 다음 요청에 전달해 4년 주봉을 끝까지
조회합니다. `.cache/kiwoom_weekly/{코드}.csv`에는 금요일 라벨 주봉을 누적하고, 새 응답과 기존
값이 충돌하면 새 수정주가를 우선합니다.

## 3. 판정·제외 규칙

- 주봉 종가는 월~금 중 마지막 거래일 종가이며 내부 라벨은 금요일, 시작 주 표시는 월요일입니다.
- MA52가 계산되는 주봉 53개 이상을 상장 1년 이상으로 간주합니다.
- 유지 기간은 기준 주에서 과거로 연속된 정배열 주 수입니다. 보유 데이터 전체가 정배열이면 `+`를 붙입니다.
- 우선주(코드 끝자리 0 아님), 스팩, 리츠, ETF, ETN, 기준일 거래량 0을 제외합니다.
- pykrx에는 관리종목 지정 여부 API가 없어 `exclude.txt`로 추가 제외합니다. 이 목록을 정기적으로
  갱신해야 하며, 자동 관리종목 수집 실패가 전체 실행을 막지 않게 합니다.
- 스팩·리츠·ETN 제외는 **종목명**으로 판정합니다. 종목명 조회에 실패하면 코드가 그대로 남아
  이 필터를 빠져나가므로, 실패한 종목은 한 번 재시도하고 그래도 안 되면 경고로 남깁니다.
  `run.log`에 `경고: 종목명 조회 실패`가 보이면 그 종목들은 필터가 적용되지 않은 것입니다.
- 정렬은 유지 기간 내림차순, 동률이면 시가총액 내림차순입니다.

직전 주 `reports/history/{날짜}.json`이 있으면 그 목록과 비교합니다. 없으면 현재 시계열의 직전 주
판정을 사용합니다. 직전 종목이 이번 유니버스에서 사라져도 이전 스냅샷 상세로 이탈 표에 남습니다.

## 4. 메일 발송

스캔이 끝나면 리포트를 메일로 보냅니다. 본문에는 요약 지표와 신규·이탈·전체 목록이
인라인 스타일 HTML로 들어가고, 전체 리포트 HTML과 CSV가 첨부됩니다. 메일 클라이언트는
`<style>` 블록과 CSS 변수를 지원하지 않으므로 본문은 `report.py`의 HTML과 별도로 만듭니다.

`.env`에 아래 값을 넣습니다. `MAIL_TO`가 비어 있으면 발송을 건너뜁니다.

```dotenv
MAIL_TO=받는주소@naver.com
SMTP_HOST=smtp.naver.com
SMTP_PORT=465
SMTP_USER=네이버아이디
SMTP_PASSWORD=계정_또는_앱_비밀번호
MAIL_FROM=네이버아이디@naver.com
```

네이버 메일 > 환경설정 > **POP3/IMAP 설정**에서 "IMAP/SMTP 사용"을 먼저 켜야 합니다.
꺼져 있으면 비밀번호가 맞아도 인증에서 막힙니다. 2단계 인증을 쓰지 않는다면 계정
비밀번호를 그대로 쓸 수 있고, 켜뒀다면 애플리케이션 비밀번호가 필요합니다.

`SMTP_USER`는 `@naver.com`을 뺀 아이디입니다. 이때 `MAIL_FROM`을 반드시 적으세요.
비워 두면 `SMTP_USER`가 발신 주소로 쓰이는데 아이디만으로는 메일 주소가 되지 않습니다.

### 그 밖의 설정

`SMTP_HOST`는 기본 `smtp.naver.com`, `SMTP_PORT`는 기본 `465`(접속 즉시 SSL)이고,
`587`을 주면 STARTTLS로 붙습니다. 다른 메일 서비스도 이 두 값만 바꾸면 그대로 씁니다.
`MAIL_FROM_NAME`은 발신자 표시 이름, `MAIL_ATTACH=0`이면 첨부 없이 본문만 보냅니다.
`MAIL_TO`에는 쉼표로 여러 주소를 넣을 수 있습니다.

첨부는 `charset=utf-8`을 명시하고 base64로 싣습니다. bytes로 붙이면 charset이 선언되지
않아 클라이언트가 한글 파일명·내용을 깨뜨리고, 기본 `8bit` 전송은 중간 서버가 모두
8BITMIME을 지원해야 원본이 보존됩니다.

스캔 없이 설정만 확인합니다.

```bash
.venv/bin/python mailer.py
```

발송 실패는 경고로만 남기고 종료코드 0을 유지합니다. 리포트는 이미 `reports/`에
저장된 뒤이므로 발송 문제로 스캔 전체를 다시 돌리지 않습니다. 실패 원인은 `run.log`에서 봅니다.

### 실행 실패 알림

스캔이 리포트를 만들지 못하면 `run_weekly.sh`가 실패 알림 메일을 보냅니다. 아무 메일도
오지 않으면 그날이 정상인지 고장인지 구분할 수 없기 때문입니다. 기준일 데이터 미반영으로
재시도까지 모두 실패했을 때와, 그 밖의 오류로 중단됐을 때 각각 한 번씩 보내며 `run.log`의
마지막 25줄을 함께 싣습니다.

```bash
.venv/bin/python mailer.py --failure "사유" --log run.log
```

## 5. 매주 금요일 16:00 KST

### cron

```bash
crontab -e
0 16 * * 5 /절대경로/jbscan/run_weekly.sh >> /절대경로/jbscan/run.log 2>&1
```

`run_weekly.sh`는 `--strict-date` 종료코드 2일 때만 10분 간격으로 3회 재시도합니다(총 4회).

### launchd

`com.jbscan.weekly.plist.example`의 `/ABSOLUTE/PATH/jbscan`을 실제 경로로 바꾼 뒤:

```bash
cp com.jbscan.weekly.plist.example ~/Library/LaunchAgents/com.jbscan.weekly.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.jbscan.weekly.plist
launchctl print gui/$(id -u)/com.jbscan.weekly   # 등록 확인
launchctl bootout gui/$(id -u)/com.jbscan.weekly # 해제
```

`Weekday`는 0과 7이 일요일이므로 금요일은 `5`입니다. 맥이 꺼져 있으면 실행되지 않고,
잠자기 상태였다면 깨어난 직후에 한 번 실행됩니다.

## 파일 구조

```text
jb_scan.py           CLI 진입점
kiwoom_client.py     키움 인증·토큰·유량제한·차트·캐시
universe.py          pykrx 유니버스
screen.py            주봉 정규화·MA·streak
report.py            HTML/CSV/TXT/JSON
mailer.py            리포트 메일 발송(SMTP)
run_weekly.sh        금요일 16:00 실행 + 재시도 + 실패 알림
```
