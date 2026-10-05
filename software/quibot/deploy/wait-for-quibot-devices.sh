#!/usr/bin/env bash
set -u

for _ in $(seq 1 60); do
    if [[ -c /dev/ttyACM0 ]] \
        && [[ -c /dev/ttyS0 ]] \
        && arecord -l 2>/dev/null | grep -q "ReSpeaker Lite" \
        && aplay -l 2>/dev/null | grep -q "ReSpeaker Lite"; then
        exit 0
    fi
    sleep 1
done

echo "Quibot devices were not ready after 60 seconds." >&2
exit 1
