#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
room_test_dir="${TMPDIR:-/tmp}/signalmap-room-tests"
mkdir -p "$room_test_dir"
xcrun swiftc -D ROOM_PROTOCOL_TEST -module-cache-path "$room_test_dir/module-cache" SignalMap/RoomTypes.swift SignalMap/RangeGeometry.swift SignalMap/RoomTransport.swift SignalMap/RoomBridge.swift SignalMap/HeadingSource.swift SignalMap/BenchLog.swift SignalMap/RoomSession.swift Tests/ProtocolTests/Platform.swift Tests/ProtocolTests/main.swift -o "$room_test_dir/protocol-tests"
"$room_test_dir/protocol-tests"
