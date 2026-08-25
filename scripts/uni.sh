#!/bin/bash
# helper: login once, expose api() for reuse
U=$(cut -d: -f1 /etc/unifi-automation.auth); P=$(cut -d: -f2- /etc/unifi-automation.auth)
CJ=/tmp/.unicj; HD=/tmp/.unihd
curl -sk -c "$CJ" -D "$HD" -o /dev/null -X POST "https://192.168.1.1/api/auth/login" \
  -H "Content-Type: application/json" -d "{\"username\":\"$U\",\"password\":\"$P\"}" --max-time 25
export TOK=$(grep -i "^x-csrf-token:" "$HD" | tr -d "\r" | awk "{print $2}")
api() { # api METHOD PATH [BODY]
  local m="$1" p="$2" b="$3"
  if [ -n "$b" ]; then
    curl -sk -b "$CJ" -X "$m" -H "Content-Type: application/json" -H "X-CSRF-Token: $TOK" \
      -d "$b" "https://192.168.1.1/proxy/network/api/s/default$p" --max-time 40
  else
    curl -sk -b "$CJ" -X "$m" -H "X-CSRF-Token: $TOK" \
      "https://192.168.1.1/proxy/network/api/s/default$p" --max-time 40
  fi
}
