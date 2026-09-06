# Narrow the gesture controls

Completed: 2026-09-06 06:23:04 EDT (America/New_York).

Request: The user wanted only fist, point up, and point down, with reliable recognition rather than additional gestures.

Removed the active swing and three-finger paths, removed legacy gesture commands from the browser, and retained fist handoff, owner locking and vertical movement. The user then changed the desired altitude signs to one finger and two fingers; the final code implements that later mapping, not pointing direction. Stop drone remains an explicit browser button.

The intermediate three-gesture build 1788689750 compiled and was installed/launched. All 21 focused browser phone-control tests passed and the frontend production build passed. Native raw diagnostics exposed a camera mounting mismatch: the hardcoded 90-degree rotation produced landscape-shaped input on this phone. The final build uses Apple's device-specific rotation coordinator for preview and capture.
