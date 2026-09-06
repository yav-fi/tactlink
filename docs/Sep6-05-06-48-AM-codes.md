# Six-digit room codes

Completed: 2026-09-06T05:06:48.019879-04:00 (EDT), America/New_York

Prompt: Replace long room codes with six numbers.

New codes contain exactly six ASCII digits. Mode remains encoded in the first digit (2, 3, or 5); five random numeric digits follow, including zero and one. Display and copy use the same six digits. Join uses a number keypad and six-digit validation guidance. Existing 16-character codes remain accepted for compatibility; both participants must update before joining a new short-code room. Updated iOS setup instructions.

Verification: Added generation checks for all three modes, ASCII validation, zero digits, whitespace/hyphen normalization, malformed inputs, and legacy compatibility. Focused production Network.framework two-peer protocol test passed using a generated six-digit code, synthetic UWB, shared geometry, and mode transition. Native build passed after correcting a missing return found by the initial build. Short codes provide convenience for nearby demo sessions, not high-entropy access control.

Deployment: build 1788685568 installed and launched on Alvan’s phone. Rejoin the current room after launch. Thomas’s separately signed com.thomas.SignalMap build still needs an update using his signing account/Mac; no Thomas installation is claimed.

Preserved unrelated staged docs/Sep6-03-32-00-AM.md.
