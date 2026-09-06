# Close-up human and drone scene

Completed: 2026-09-06T05:12:25.368511-04:00 (EDT), America/New_York

Prompt: Show human models at phone positions, fly the drone above them, zoom for 1–10 m spacing, and explain exact gestures.

Added procedural three-dimensional human figures to the existing operator layer in phone mode. Preserved old runtime billboard behavior. Added a close camera that fits human feet/heads and the drone, with frame-group and zoom controls, and manual drag/wheel override. A fixed one-metre ENU grid makes separation visible. The drone starts at 3.5 m; a per-frame height constraint keeps it between 2.5 m and 8 m above the highest positioned person base. Thumbs-down now stops at this hover floor and orbit targets a two-metre radius. Added an always-visible gesture guide with hand shapes, directions, speeds, hold/release behavior, one-shot turns, home travel, and finger swings; current gesture highlights in gold. Updated README to distinguish this demo from the legacy runtime.

Verification: TypeScript/Vite production build passed; 11 focused phone-control checks passed, including actual rendered entity height, feet ground level, measured three-metre phone separation, flight floor/ceiling through the production controller, close camera range, and physical drone scaling with no pixel enlargement. git diff --check passed. The exact local preview URL returned HTTP 200 and the UDP receiver was listening; its last snapshot had no live phone samples. No browser visual QA or physical multi-phone gesture flight is claimed. The existing local simulator was rebuilt.

Preserved unrelated staged docs/Sep6-03-32-00-AM.md.
