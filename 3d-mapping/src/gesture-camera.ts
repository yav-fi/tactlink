import { GestureRecognizer, type GestureRecognizerResult } from "@mediapipe/tasks-vision";
import wasmLoaderPath from "@mediapipe/tasks-vision/vision_wasm_internal.js?url";
import wasmBinaryPath from "@mediapipe/tasks-vision/vision_wasm_internal.wasm?url";
import { GestureHoldInterpreter } from "./gesture-hold";
import { deriveFingerMotionInput, FingerMotionInterpreter } from "./finger-motion";
import { GestureCommandRepeater } from "./gesture-repeat";
import type { WorkerReply, WorkerRequest } from "./gesture-worker";

const MODEL_PATH = "/models/gesture_recognizer.task";

let activeDelegate = "—";
const MIN_SCORE = 0.5;
const FRAME_INTERVAL_MS = 60;

export type BrowserGestureState = {
  status: "loading" | "active" | "error";
  present: boolean;
  gesture: string;
  score: number;
  holdProgress: number;
  message?: string;
};

function topGesture(result: GestureRecognizerResult): { gesture: string; score: number } {
  const category = result.gestures[0]?.[0];
  if (!category || category.categoryName === "None" || category.score < MIN_SCORE) {
    return { gesture: "None", score: 0 };
  }
  return { gesture: category.categoryName, score: category.score };
}

/**
 * Open a private, hidden webcam stream and run gesture recognition in WASM.
 * No frame is rendered, uploaded, recorded, or sent to the Python runtime.
 *
 * Recognition happens in a worker so its cost does not land on a render frame;
 * `?gesture=inline` keeps it in the page, which is also the automatic fallback
 * when a worker cannot be created. Interpretation stays here either way.
 */
export async function startGestureCamera(
  onState: (state: BrowserGestureState) => void,
  onAction: (action: string) => void,
): Promise<() => void> {
  if (!navigator.mediaDevices?.getUserMedia) throw new Error("This browser does not provide camera access.");
  onState({ status: "loading", present: false, gesture: "None", score: 0, holdProgress: 0 });

  const stream = await navigator.mediaDevices.getUserMedia({
    audio: false,
    video: { facingMode: "user", width: { ideal: 640 }, height: { ideal: 480 }, frameRate: { ideal: 20, max: 30 } },
  });
  const video = document.createElement("video");
  video.muted = true;
  video.playsInline = true;
  video.srcObject = stream;
  await video.play();

  const parameters = new URLSearchParams(window.location.search);
  const delegate = parameters.get("delegate") ?? undefined;
  const interpreter = new GestureHoldInterpreter();
  const fingerMotion = new FingerMotionInterpreter();
  const repeater = new GestureCommandRepeater();
  let stopped = false;

  /** Everything downstream of the model, shared by both recognition paths. */
  const interpret = (result: GestureRecognizerResult, now: number): void => {
    const classified = topGesture(result);
    const held = interpreter.update(classified.gesture, now);
    const motion = fingerMotion.update(deriveFingerMotionInput(result.landmarks[0]), now);
    const source = motion.label ?? (classified.gesture === "None" ? undefined : classified.gesture);
    onState({
      status: "active",
      present: result.landmarks.length > 0,
      gesture: motion.label ?? classified.gesture,
      score: classified.score,
      holdProgress: Math.max(held.progress, motion.progress),
      message: motion.hint || undefined,
    });
    if (held.action) {
      onAction(held.action);
      repeater.start(held.action, classified.gesture, now);
    }
    for (const action of motion.actions) {
      onAction(action);
      repeater.start(action, motion.label ?? classified.gesture, now);
    }
    for (const action of repeater.update(source, now, result.landmarks.length > 0)) {
      // A pose that is still being held must be able to command again without
      // the operator lowering their hand first.
      if (action === "gesture_stop") interpreter.rearm();
      onAction(action);
    }
  };

  const fail = (message: string): void => {
    stopped = true;
    onState({ status: "error", present: false, gesture: "None", score: 0, holdProgress: 0, message });
  };

  const worker = parameters.get("gesture") === "inline" ? undefined : await startWorker(delegate);
  if (worker) return runWithWorker(worker, video, stream, interpret, fail, () => stopped, () => { stopped = true; });
  return runInline(video, stream, delegate, interpret, fail, () => stopped, () => { stopped = true; });
}

/** Resolve to a ready worker, or to undefined so the caller stays in the page. */
async function startWorker(delegate?: string): Promise<Worker | undefined> {
  if (typeof Worker === "undefined" || typeof createImageBitmap !== "function") return undefined;
  let worker: Worker;
  try {
    worker = new Worker(new URL("./gesture-worker.ts", import.meta.url), { type: "module" });
  } catch {
    return undefined;
  }
  return new Promise<Worker | undefined>(resolve => {
    const settle = (value: Worker | undefined) => {
      clearTimeout(timer);
      worker.removeEventListener("message", onMessage);
      if (!value) worker.terminate();
      resolve(value);
    };
    const onMessage = (event: MessageEvent<WorkerReply>) => {
      if (event.data.type === "ready") { activeDelegate = `${event.data.delegate} · worker`; settle(worker); }
      else if (event.data.type === "failed") settle(undefined);
    };
    // Loading an 8 MB model can be slow on a cold cache, but it must not hang
    // the demo: give up and recognize in the page instead.
    const timer = setTimeout(() => settle(undefined), 15_000);
    worker.addEventListener("message", onMessage);
    worker.addEventListener("error", () => settle(undefined), { once: true });
    worker.postMessage({ type: "start", modelPath: MODEL_PATH, minScore: MIN_SCORE, delegate } satisfies WorkerRequest);
  });
}

function runWithWorker(
  worker: Worker,
  video: HTMLVideoElement,
  stream: MediaStream,
  interpret: (result: GestureRecognizerResult, now: number) => void,
  fail: (message: string) => void,
  isStopped: () => boolean,
  markStopped: () => void,
): () => void {
  let animationFrame = 0;
  let lastRun = -Infinity;
  let lastVideoTime = -1;
  let busy = false;

  worker.addEventListener("message", (event: MessageEvent<WorkerReply>) => {
    if (event.data.type === "failed") { fail(`Recognition stopped · ${event.data.message} · add &gesture=inline to the URL`); return; }
    if (event.data.type !== "result") return;
    busy = false;
    if (!isStopped()) interpret(event.data.result, event.data.timestamp);
  });

  const update = (now: number) => {
    if (isStopped()) return;
    animationFrame = requestAnimationFrame(update);
    if (busy || video.readyState < HTMLMediaElement.HAVE_CURRENT_DATA
      || now - lastRun < FRAME_INTERVAL_MS || video.currentTime === lastVideoTime) return;
    lastRun = now;
    lastVideoTime = video.currentTime;
    busy = true;
    // Grabbing the frame is the only recognition work left on this thread.
    createImageBitmap(video).then(bitmap => {
      if (isStopped()) { bitmap.close(); busy = false; return; }
      worker.postMessage({ type: "frame", bitmap, timestamp: now } satisfies WorkerRequest, [bitmap]);
    }).catch(() => { busy = false; });
  };
  animationFrame = requestAnimationFrame(update);

  return () => {
    markStopped();
    cancelAnimationFrame(animationFrame);
    worker.terminate();
    for (const track of stream.getTracks()) track.stop();
    video.srcObject = null;
  };
}

async function runInline(
  video: HTMLVideoElement,
  stream: MediaStream,
  delegate: string | undefined,
  interpret: (result: GestureRecognizerResult, now: number) => void,
  fail: (message: string) => void,
  isStopped: () => boolean,
  markStopped: () => void,
): Promise<() => void> {
  const order: ("GPU" | "CPU")[] = delegate?.toUpperCase() === "CPU" ? ["CPU"] : ["GPU", "CPU"];
  let recognizer: GestureRecognizer | undefined;
  let failure: unknown;
  for (const option of order) {
    try {
      recognizer = await GestureRecognizer.createFromOptions(
        { wasmLoaderPath, wasmBinaryPath },
        {
          baseOptions: { modelAssetPath: MODEL_PATH, delegate: option },
          runningMode: "VIDEO",
          numHands: 1,
          minHandDetectionConfidence: 0.5,
          minHandPresenceConfidence: 0.5,
          minTrackingConfidence: 0.5,
          cannedGesturesClassifierOptions: { scoreThreshold: MIN_SCORE },
        },
      );
      activeDelegate = `${option} · inline`;
      break;
    } catch (error) {
      failure = error;
    }
  }
  if (!recognizer) {
    for (const track of stream.getTracks()) track.stop();
    throw failure instanceof Error ? failure : new Error("Gesture recognizer could not start.");
  }

  let animationFrame = 0;
  let lastRun = -Infinity;
  let lastVideoTime = -1;
  const update = (now: number) => {
    if (isStopped()) return;
    animationFrame = requestAnimationFrame(update);
    if (video.readyState < HTMLMediaElement.HAVE_CURRENT_DATA || now - lastRun < FRAME_INTERVAL_MS || video.currentTime === lastVideoTime) return;
    lastRun = now;
    lastVideoTime = video.currentTime;
    let result: GestureRecognizerResult;
    try {
      result = recognizer.recognizeForVideo(video, now);
    } catch (error) {
      // An unhandled throw here would silently kill the loop and the gestures
      // with it, so surface it and stop instead of failing invisibly.
      markStopped();
      cancelAnimationFrame(animationFrame);
      fail(`Recognition stopped on ${activeDelegate} · ${error instanceof Error ? error.message : "reload to retry"}`
        + (activeDelegate.startsWith("GPU") ? " · add &delegate=cpu to the URL" : ""));
      return;
    }
    interpret(result, now);
  };
  animationFrame = requestAnimationFrame(update);

  return () => {
    markStopped();
    cancelAnimationFrame(animationFrame);
    recognizer?.close();
    for (const track of stream.getTracks()) track.stop();
    video.srcObject = null;
  };
}
