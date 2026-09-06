# Consistent physical model scale

Completed: 2026-09-06T05:12:25.368511-04:00 (EDT), America/New_York

Prompt: Ensure human and drone sizes make sense in metres.

Human figure geometry spans exactly 1.75 m from feet to head, positioned through the same ENU metre frame as the UWB offsets. Drone model scale is computed from its authored 1.48 m front/rear motor-center span and rotor blade corner radius hypot(0.52,0.035), producing a 0.8 m spinning-rotor envelope. Disabled minimum-pixel-size enlargement and capped scale to the physical value in phone mode. A one-metre ground grid and explicit dimensions are displayed. Chosen standard demo dimensions are not inferred measurements of the actual users or an unspecified physical drone; UWB relative positions are preserved.

Verification: TypeScript/Vite production build passed; 11 focused phone-control checks passed, including actual rendered entity height, feet ground level, measured three-metre phone separation, flight floor/ceiling through the production controller, close camera range, and physical drone scaling with no pixel enlargement. git diff --check passed. The exact local preview URL returned HTTP 200 and the UDP receiver was listening; its last snapshot had no live phone samples. No browser visual QA or physical multi-phone gesture flight is claimed. The existing local simulator was rebuilt.

Preserved unrelated staged docs/Sep6-03-32-00-AM.md.
