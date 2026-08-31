# 주봉 정배열 스캐너

매주 금요일 마감 기준으로 KOSPI·KOSDAQ 시가총액 3,000억원 이상 종목 중
`주봉 MA4 > MA13 > MA26 > MA52`인 종목을 찾아 HTML 리포트를 만들고 메일로 보냅니다.

- 종목·시가총액: KRX 정보데이터시스템 (`pykrx`)
- 수정주가 주봉: 키움증권 REST API
- 출력: `reports/{YYYYMMDD}_주봉_정배열.{html,csv,txt}` + `reports/history/{날짜}.json`

## 설치

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env    # 키움 키, KRX 계정, 메일 설정
```

키움은 [포털](https://openapi.kiwoom.com/)에서 App Key를 발급하고 **실행할 컴퓨터의 공인 IP를
등록**해야 합니다. 미등록 IP면 토큰 발급이 거절됩니다(`8050:지정단말기 인증에 실패`).

## 실행

```bash
python -m jbscan                            # 최근 금요일 기준
python -m jbscan --date 2026-08-14 --refresh
python -m jbscan --dry-run                  # 유니버스만, 키움 호출 없음
python -m jbscan --no-mail
python -m jbscan --check-auth               # 등록 IP인지만 확인 (0 / 3)
python -m jbscan --help                     # --min-cap, --years, --workers 등
```

종료코드: 0 성공 · 2 기준일 데이터 미반영(`--strict-date`) · 3 키움 인증 실패 · 1 그 외.

## 자동 실행

금요일 18:00부터 리포트가 나올 때까지 30분마다 재시도합니다(72시간). 등록 IP가 아닌 동안은
조용히 넘어가고, 창을 다 쓰면 실패 메일을 한 번 보냅니다.

```bash
# deploy/com.jbscan.weekly.plist.example 의 /ABSOLUTE/PATH/jbscan 을 실제 경로로 바꾼 뒤
cp deploy/com.jbscan.weekly.plist.example ~/Library/LaunchAgents/com.jbscan.weekly.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.jbscan.weekly.plist
launchctl kickstart gui/$(id -u)/com.jbscan.weekly   # 즉시 실행
launchctl bootout   gui/$(id -u)/com.jbscan.weekly   # 해제
```

macOS TCC 때문에 프로젝트를 `~/Documents`·`~/Desktop`·`~/Downloads` 안에 두면 예약 실행만
조용히 죽습니다. `~/jbscan`처럼 홈 바로 아래에 두세요.

## 구조

```text
run_weekly.sh              launchd 진입점: 재시도 게이트와 실패 알림
jbscan/
  __main__.py              CLI (python -m jbscan)
  kiwoom_client.py         키움 인증·유량제한·주봉·캐시
  universe.py              pykrx 유니버스와 종목명
  screen.py                주봉 정규화·MA·유지 기간
  report.py                HTML/CSV/TXT/JSON 생성
  mailer.py                메일 발송과 실패 알림
deploy/                    launchd plist 템플릿
reports/                   출력 (Git 제외)
.state/                    재시도 상태 (Git 제외)
```
