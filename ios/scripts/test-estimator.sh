#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
build_dir="${TMPDIR:-/tmp}/signalmap-estimator-tests"
mkdir -p "$build_dir"
xcrun swiftc -module-cache-path "$build_dir/module-cache" docs/legacy/Code/PositionEstimator.swift docs/legacy/Code/PeerMeasurement.swift Tests/main.swift -o "$build_dir/estimator-tests"
"$build_dir/estimator-tests"
