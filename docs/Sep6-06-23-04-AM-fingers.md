# Ship fist, one-finger and two-finger controls

Completed: 2026-09-06 06:23:04 EDT (America/New_York).

Request: Use only closed fist to call/follow, one finger to climb, and two fingers to descend; retain visible tracked points and use MediaPipe plus finger rules if useful.

## Final behavior

- MediaPipe remains the pretrained hand tracker. The classifier consumes its 3D world landmarks (normalized 3D landmarks with aspect correction as fallback), using finger-bend cosine and fingertip reach relative to proximal segment length. Extended and curled states have separate thresholds with an uncertain gap.
- Four curled fingers means Closed_Fist even when the canned category is None. Index extended and other fingers curled means One_Finger_Up. Index+middle extended with ring+pinky curled means Two_Fingers_Down. Thumb and hand direction are ignored. Confident canned fist is a fallback only when no finger is clearly extended.
- Temporal voting remains; no hand, interruption, or inference error clears pending output. Invalid joint geometry is uncertain rather than an automatic fist. Other finger combinations have no command.
- Browser accepts only the three final labels. Fist transfers ownership/follow; one/two fingers adjust altitude, and release holds the resulting height. Stop drone cancels follow. Old directional labels no longer command altitude, so both participants need the updated build for one/two-finger control.
- The preview keeps its 21-point skeleton and adds raw model label/score, frame dimensions/rotation, and index/middle/ring/pinky states. Rule acceptance flags remain in the existing transport confidence field, but are no longer displayed as percentage confidence. Actual Google scores remain on the raw line.
- Apple's RotationCoordinator now selects camera-specific capture and preview angles instead of hardcoded 90 degrees. Physical diagnostics on this iPhone report rotation 0 degrees and portrait 480x640 frames; the prior forced angle produced 640x480 frames. This is a concrete input-orientation correction, not evidence of measured end-to-end recognition accuracy.

## Verification and delivery

Swift tests passed for fist with canned None, one/two fingers at four rotations, ignored thumb orientation, rejected open/three-finger/middle-only poses, a depth-rotated index finger, degenerate geometry, and temporal filtering. All 21 focused browser tests passed, including actual simulated drone movement, handoff, ownership retention, release, and rejection of legacy gestures. The final TypeScript/Vite build passed and local port 5173 serves index-Bo5XHW1a.js. git diff --check passed.

Final signed native build 1788689988 compiled successfully through the installed Xcode fallback, passed signature verification, and installed/launched on Alvan's iPhone in existing room 290315. Live diagnostics verified inference at 480x640/rotation 0. The latest sample had no hand and only one room member; no user-confirmed fist success or physical two-phone drone run is claimed.

Thomas still needs the same build signed with his existing account; the available profile on this Mac excludes his device. No change to his app was attempted. Real-hand reliability remains to be checked in the visible preview, especially occlusion and finger ambiguity. No custom weights were trained and camera frames stay on-device.

Preserved unrelated staged docs/Sep6-03-32-00-AM.md. Updated ios/README.md and browser/iPhone instructions for the final mapping. Earlier steering in this active turn is documented separately in the fist and only-three notes.
