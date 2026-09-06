#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
gesture_test_dir="${TMPDIR:-/tmp}/signalmap-gesture-tests"
mkdir -p "$gesture_test_dir"
xcrun swiftc -D ROOM_PROTOCOL_TEST -module-cache-path "$gesture_test_dir/module-cache" SignalMap/GestureCamera.swift Tests/GestureTests/main.swift -o "$gesture_test_dir/gesture-tests"
"$gesture_test_dir/gesture-tests"
