import { GestureRecognizer, type GestureRecognizerResult } from "@mediapipe/tasks-vision";
import wasmLoaderPath from "@mediapipe/tasks-vision/vision_wasm_internal.js?url";
import wasmBinaryPath from "@mediapipe/tasks-vision/vision_wasm_internal.wasm?url";
import { GestureHoldInterpreter } from "./gesture-hold";
import { deriveFingerMotionInput, FingerMotionInterpreter } from "./finger-motion";

const MODEL_PATH = "/models/gesture_recognizer.task";
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

  let recognizer: GestureRecognizer | undefined;
  try {
    recognizer = await GestureRecognizer.createFromOptions(
      { wasmLoaderPath, wasmBinaryPath },
      {
        baseOptions: { modelAssetPath: MODEL_PATH, delegate: "CPU" },
        runningMode: "VIDEO",
        numHands: 1,
        minHandDetectionConfidence: 0.5,
        minHandPresenceConfidence: 0.5,
        minTrackingConfidence: 0.5,
        cannedGesturesClassifierOptions: { scoreThreshold: MIN_SCORE },
      },
    );
  } catch (error) {
    for (const track of stream.getTracks()) track.stop();
    throw error;
  }

  const interpreter = new GestureHoldInterpreter();
  const fingerMotion = new FingerMotionInterpreter();
  let stopped = false;
  let animationFrame = 0;
  let lastRun = -Infinity;
  let lastVideoTime = -1;

  const update = (now: number) => {
    if (stopped) return;
    animationFrame = requestAnimationFrame(update);
    if (video.readyState < HTMLMediaElement.HAVE_CURRENT_DATA || now - lastRun < FRAME_INTERVAL_MS || video.currentTime === lastVideoTime) return;
    lastRun = now;
    lastVideoTime = video.currentTime;
    const result = recognizer.recognizeForVideo(video, now);
    const classified = topGesture(result);
    const held = interpreter.update(classified.gesture, now);
    const motion = fingerMotion.update(deriveFingerMotionInput(result.landmarks[0]), now);
    onState({
      status: "active",
      present: result.landmarks.length > 0,
      gesture: motion.label ?? classified.gesture,
      score: classified.score,
      holdProgress: Math.max(held.progress, motion.progress),
      message: motion.hint || undefined,
    });
    if (held.action) onAction(held.action);
    for (const action of motion.actions) onAction(action);
  };
  animationFrame = requestAnimationFrame(update);

  return () => {
    stopped = true;
    cancelAnimationFrame(animationFrame);
    recognizer?.close();
    for (const track of stream.getTracks()) track.stop();
    video.srcObject = null;
  };
}
