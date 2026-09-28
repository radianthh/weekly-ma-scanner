#!/usr/bin/env bash
# launchd가 30분마다 부른다. 매번 "이번 주 리포트가 아직 없는가"만 확인하고,
# 없을 때만 스캔한다. 있으면 아무 것도 하지 않고 즉시 끝난다.
#
# 키움은 등록된 공인 IP에서만 호출된다. 금요일 저녁에 그 IP에 앉아 있으리란
# 보장이 없으므로, 인증이 막혀 있으면 메일을 보내지 않고 조용히 다음 호출을
# 기다린다. 재시도 창(기본 165시간, 다음 금요일 15시)을 다 쓰고도
# 리포트가 없을 때만 한 번 알린다.
set -u
cd "$(dirname "$0")"

START_HOUR="${JBSCAN_START_HOUR:-18}"    # 금요일 이 시각부터 시도한다.
                                         # 15:30 마감 뒤 KRX 일별 데이터가 반영될 시간을 준다.
RETRY_HOURS="${JBSCAN_RETRY_HOURS:-165}" # 그 시각부터 이만큼 재시도한다(기본: 다음 금요일 15시까지).
                                         # 다음 주 창이 열리는 금요일 18시보다 먼저 끝나야
                                         # 기준일이 넘어가기 전에 실패 메일이 나간다.
NET_WAIT="${JBSCAN_NET_WAIT:-60}"        # 네트워크 오류일 때 한 번 더 보기까지 기다리는 초.
STATE=".state"

say() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

if [ ! -x .venv/bin/python ]; then
  say ".venv가 없습니다: python3.11 -m venv .venv && .venv/bin/pip install -r requirements.txt"
  exit 1
fi

# 기준 금요일. 금요일 START_HOUR가 지났으면 이번 주 금요일, 아니면 지난주 금요일이다.
back=$(( ($(date +%u) - 5 + 7) % 7 ))
if [ "$(date +%u)" -eq 5 ] && [ "$(date +%H)" -lt "$START_HOUR" ]; then back=7; fi
target=$(date -v-"${back}"d +%Y-%m-%d)
opens=$(date -j -f '%Y-%m-%d %H:%M:%S' "$target $(printf '%02d' "$START_HOUR"):00:00" +%s)
deadline=$(( opens + RETRY_HOURS * 3600 ))
until_text=$(date -r "$deadline" '+%m-%d %H:%M')
now=$(date +%s)

mkdir -p "$STATE"
find "$STATE" \( -name 'gaveup-*' -o -name 'tried-*' \) -mtime +30 -delete 2>/dev/null

# 이미 만들었으면 할 일이 없다. 조용히 끝내야 로그가 30분마다 불어나지 않는다.
[ -f "reports/history/$target.json" ] && exit 0

# 재시도 창을 다 썼다면 그때 한 번만 알린다. 창 안에서 한 번도 시도한 적이
# 없다면(설치 직후 등) 애초에 맡은 주가 아니므로 조용히 넘긴다.
if [ "$now" -ge "$deadline" ]; then
  marker="$STATE/gaveup-$target"
  [ -f "$marker" ] && exit 0
  : > "$marker"
  [ -f "$STATE/tried-$target" ] || exit 0
  say "기준일 $target: ${RETRY_HOURS}시간 재시도 창을 모두 쓰고도 리포트를 만들지 못했습니다"
  .venv/bin/python -m jbscan.mailer \
    --failure "기준일 $target 리포트 실패 — ${RETRY_HOURS}시간 동안 재시도했지만 성공하지 못했습니다" \
    --log run.log || say "실패 알림 메일도 보내지 못했습니다"
  exit 2
fi

# 스캔은 몇 분씩 걸린다. 앞선 실행이 아직 돌고 있으면 비켜선다.
lock="$STATE/lock"
if [ -d "$lock" ] && [ -n "$(find "$lock" -maxdepth 0 -mmin +120 2>/dev/null)" ]; then
  say "2시간 넘게 남아 있는 잠금을 지웁니다"
  rmdir "$lock" 2>/dev/null
fi
mkdir "$lock" 2>/dev/null || exit 0
trap 'rmdir "$lock" 2>/dev/null' EXIT

# 이 기준일을 맡았다는 표시. 이게 없으면 마감이 지나도 실패 메일을 보내지 않는다.
: > "$STATE/tried-$target"

# 인증부터 확인한다. 등록되지 않은 IP면 몇 분짜리 KRX 유니버스 구성을 시작조차 하지 않는다.
auth_error=$(.venv/bin/python -m jbscan --check-auth 2>&1); auth_code=$?
if [ "$auth_code" -ne 0 ] && [ "$auth_code" -ne 3 ]; then
  # 코드 3(미등록 IP)이 아닌 실패는 대개 네트워크다. 뚜껑을 연 직후라면 Wi-Fi가
  # 아직 안 붙었을 수 있으므로, 다음 호출까지 30분을 버리지 말고 한 번 더 본다.
  sleep "$NET_WAIT"
  auth_error=$(.venv/bin/python -m jbscan --check-auth 2>&1); auth_code=$?
fi
if [ "$auth_code" -ne 0 ]; then
  reason=$(printf '%s\n' "$auth_error" | grep -m1 '오류:' | cut -c1-140)
  say "기준일 $target: 키움 인증 대기 — ${reason:-원인 불명} · 다음 호출에서 재시도 (마감 $until_text)"
  exit 0
fi

say "기준일 $target 스캔 시작 (마감 $until_text)"
.venv/bin/python -m jbscan --strict-date "$@"
code=$?
case "$code" in
  0) exit 0 ;;
  2) say "기준일 $target: 데이터 미반영 — 다음 호출에서 재시도 (마감 $until_text)" ;;
  3) say "기준일 $target: 키움 인증 실패 — 다음 호출에서 재시도 (마감 $until_text)" ;;
  *) say "기준일 $target: 스캔 실패(코드 $code) — 다음 호출에서 재시도 (마감 $until_text)" ;;
esac
exit "$code"
