# TactLink pitch

Technical pitch site for the existing TactLink prototype. Source is isolated in `pitch/`; no flight, iOS, or simulator behavior changes.

## Run

Requires Node.js 22.13 or newer.

```sh
cd pitch
npm ci
npm run dev -- --port 3100
npm run build
```

The build statically exports to `dist/client`. Serve that directory with any static host. The page does not request camera access, connect to devices, or display live telemetry. No external fonts or raster assets are needed.

## Diagram behavior

- Five-shell geometry: numbered buttons reveal successive range constraints. Each schematic sphere silhouette meets the indicated point. This explains multilateration; the actual five-phone implementation jointly embeds ten mutual distances with classical multidimensional scaling.
- Round-robin schedule: select a round or play the sequence. Pairings reproduce the circle-method schedule in `ios/SignalMap/RoomTypes.swift`; no phone is assigned two simultaneous partners. Runtime dispatch can overlap nominal round boundaries when peers become free.
- Gesture architecture: accessible tabs switch between runtime inference and optional desktop custom training. Positioning remains independent of this camera-based input path.

## Claim boundaries

- No GPS, camera, fixed anchors, or cloud service are needed for relative UWB positioning. Low-visibility operation is a design use case, not a measured field-performance claim.
- The implementation elects a peer coordinator for scheduling and geometry publication. It is infrastructure-free and peer-coordinated, not strictly leaderless or a fully distributed geometry solver.
- `< 5 s` is a five-phone complete-cycle design target, not a verified benchmark. No physical-device cycle logs were present to support a measured p50/p95 claim.
- A 100 ms scheduler interval and roughly 10 Hz bridge transmission do not imply new measured geometry on each update.
- MediaPipe supplies pretrained hand landmarks and canned classification. Optional desktop custom training fits k-NN over 63 normalized landmark features. iOS uses MediaPipe plus rules; no optional custom artifact is bundled.
- Four readings is the attempt completion threshold. Fit residual thresholds are not ground-truth position accuracy.

## Verification

Run `npx tsc --noEmit` and `npx oxlint app lib vite.config.ts next.config.ts` for authored code. The starter-wide `npm run lint` also checks its untouched generated component catalog, which currently contains upstream lint violations. No browser interaction or visual QA was performed; validation covers compilation, static output, local HTTP response, and diagram invariants.

Primary implementation references and UWB / MediaPipe references are linked on the page. Sites registration is in `.openai/hosting.json`; generated dependencies and build output stay ignored.
