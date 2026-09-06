#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
room_test_dir="${TMPDIR:-/tmp}/signalmap-room-tests"
mkdir -p "$room_test_dir"
xcrun swiftc -module-cache-path "$room_test_dir/module-cache" SignalMap/RoomTypes.swift SignalMap/RangeGeometry.swift Tests/RoomTests/main.swift -o "$room_test_dir/room-tests"
"$room_test_dir/room-tests"
