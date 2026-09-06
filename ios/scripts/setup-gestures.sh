#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
model="SignalMap/gesture_recognizer.task"
expected="97952348cf6a6a4915c2ea1496b4b37ebabc50cbbf80571435643c455f2b0482"
if [ ! -f "$model" ] || [ "$(shasum -a 256 "$model" | cut -d ' ' -f 1)" != "$expected" ]; then
  temporary=$(mktemp "${model}.XXXXXX")
  trap 'rm -f "$temporary"' EXIT INT TERM
  curl -fL --retry 2 'https://storage.googleapis.com/mediapipe-models/gesture_recognizer/gesture_recognizer/float16/1/gesture_recognizer.task' -o "$temporary"
  [ "$(shasum -a 256 "$temporary" | cut -d ' ' -f 1)" = "$expected" ] || { echo 'MediaPipe model checksum mismatch' >&2; exit 1; }
  mv "$temporary" "$model"
fi
pod install
