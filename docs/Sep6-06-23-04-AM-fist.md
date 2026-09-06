# Diagnose missed closed fists

Completed: 2026-09-06 06:23:04 EDT (America/New_York).

Request: The user clarified that even closed fist was reported as None.

Added raw MediaPipe hand count, top category/score, frame size and rotation, a preview skeleton, and a diagnostic-log readout. Requested explicit VGA capture and initially protected learned fists from ancillary gesture rules. The first diagnostic build 1788689611 compiled and was installed/launched. Later user steering narrowed the active gestures and then replaced directional pointing with finger counts; the final implementation is documented in the matching fingers note.

Before these changes, phone diagnostics reported no detected hand. A later intermediate build showed one hand with raw None at 53% and landscape 640x480 buffers. These snapshots were not synchronized to a user-confirmed fist pose and do not establish classifier accuracy. No camera images were recorded or transferred. Tests of learned-fist precedence passed before the final count-based rules superseded that approach.
