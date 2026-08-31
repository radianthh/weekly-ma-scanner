#!/usr/bin/env bash
# 금요일 16:00 KST 실행. KRX/키움 데이터 미반영(exit 2)만 재시도한다.
set -u
cd "$(dirname "$0")"

if [ ! -x .venv/bin/python ]; then
  echo ".venv가 없습니다: python3.11 -m venv .venv && .venv/bin/pip install -r requirements.txt"
  exit 1
fi

# 리포트를 못 만든 날에도 메일이 오게 한다. 아무 메일도 오지 않으면
# 정상인지 고장인지 구분할 수 없다.
notify_failure() {
  .venv/bin/python mailer.py --failure "$1" --log run.log || \
    echo "실패 알림 메일도 보내지 못했습니다"
}

for attempt in 1 2 3 4; do
  .venv/bin/python jb_scan.py --strict-date "$@"
  code=$?
  if [ "$code" -eq 0 ]; then exit 0; fi
  if [ "$code" -ne 2 ]; then
    echo "실행 실패(코드 $code)"
    notify_failure "스캔이 오류로 중단됨 (종료코드 $code)"
    exit "$code"
  fi
  if [ "$attempt" -lt 4 ]; then
    echo "데이터 미반영 — 10분 후 재시도 ($attempt/3)"
    sleep 600
  fi
done

echo "최초 실행과 3회 재시도 모두 데이터 미반영"
notify_failure "기준일 데이터가 반영되지 않음 (최초 실행과 3회 재시도 모두 실패)"
exit 2
