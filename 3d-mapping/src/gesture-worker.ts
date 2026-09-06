/// <reference lib="webworker" />
import { GestureRecognizer, type GestureRecognizerResult } from "@mediapipe/tasks-vision";
import wasmLoaderPath from "@mediapipe/tasks-vision/vision_wasm_internal.js?url";
import wasmBinaryPath from "@mediapipe/tasks-vision/vision_wasm_internal.wasm?url";

/**
 * Runs hand recognition off the render thread. Landmarking costs tens of
 * milliseconds even on the GPU delegate, which is several whole frames at
 * 120 Hz, so doing it in the page stutters the flight. Only the recognition
 * itself moves: holds, finger motion and command repeats stay on the main
 * thread, where their logic is already covered by tests.
 *
 * Frames arrive as transferred ImageBitmaps and are closed after each run.
 * Nothing is stored, rendered, or sent anywhere.
 */

export type WorkerRequest =
  | { type: "start"; modelPath: string; minScore: number; delegate?: string }
  | { type: "frame"; bitmap: ImageBitmap; timestamp: number };

export type WorkerReply =
  | { type: "ready"; delegate: string }
  | { type: "failed"; message: string }
  | { type: "result"; result: GestureRecognizerResult; inferenceMs: number; timestamp: number };

let recognizer: GestureRecognizer | undefined;
const scope = self as unknown as DedicatedWorkerGlobalScope;

async function start(request: Extract<WorkerRequest, { type: "start" }>): Promise<void> {
  const order: ("GPU" | "CPU")[] = request.delegate?.toUpperCase() === "CPU" ? ["CPU"] : ["GPU", "CPU"];
  let failure: unknown;
  for (const delegate of order) {
    try {
      recognizer = await GestureRecognizer.createFromOptions(
        { wasmLoaderPath, wasmBinaryPath },
        {
          baseOptions: { modelAssetPath: request.modelPath, delegate },
          runningMode: "VIDEO",
          numHands: 1,
          minHandDetectionConfidence: 0.5,
          minHandPresenceConfidence: 0.5,
          minTrackingConfidence: 0.5,
          cannedGesturesClassifierOptions: { scoreThreshold: request.minScore },
        },
      );
      scope.postMessage({ type: "ready", delegate } satisfies WorkerReply);
      return;
    } catch (error) {
      failure = error;
    }
  }
  scope.postMessage({
    type: "failed",
    message: failure instanceof Error ? failure.message : "Gesture recognizer could not start in a worker.",
  } satisfies WorkerReply);
}

scope.onmessage = (event: MessageEvent<WorkerRequest>) => {
  const request = event.data;
  if (request.type === "start") { void start(request); return; }
  if (!recognizer) { request.bitmap.close(); return; }
  const startedAt = performance.now();
  try {
    const result = recognizer.recognizeForVideo(request.bitmap, request.timestamp);
    scope.postMessage({
      type: "result",
      // The result holds plain landmark objects, which structured-clone fine.
      result: { gestures: result.gestures, landmarks: result.landmarks, worldLandmarks: [], handedness: [], handednesses: [] } as unknown as GestureRecognizerResult,
      inferenceMs: performance.now() - startedAt,
      timestamp: request.timestamp,
    } satisfies WorkerReply);
  } catch (error) {
    scope.postMessage({
      type: "failed",
      message: error instanceof Error ? error.message : "Recognition failed in the worker.",
    } satisfies WorkerReply);
  } finally {
    request.bitmap.close();
  }
};
