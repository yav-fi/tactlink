export type PlannedFlightStep = { speed: number; distance: number };

/**
 * Accelerates toward cruise speed, then brakes early enough to arrive without
 * an abrupt snap. The returned distance is always bounded by the destination.
 */
export function plannedFlightStep(
  currentSpeed: number,
  cruiseSpeed: number,
  remainingDistance: number,
  deltaSeconds: number,
  acceleration = 34,
  deceleration = 46,
): PlannedFlightStep {
  if (!Number.isFinite(remainingDistance) || remainingDistance <= 0 || !Number.isFinite(deltaSeconds) || deltaSeconds <= 0) {
    return { speed: 0, distance: 0 };
  }
  const safeCruise = Math.max(0.1, Number.isFinite(cruiseSpeed) ? cruiseSpeed : 0.1);
  const safeCurrent = Math.max(0, Number.isFinite(currentSpeed) ? currentSpeed : 0);
  const brakingSpeed = Math.sqrt(2 * Math.max(0.1, deceleration) * remainingDistance);
  const targetSpeed = Math.min(safeCruise, brakingSpeed);
  const rate = targetSpeed >= safeCurrent ? acceleration : deceleration;
  const nextSpeed = targetSpeed >= safeCurrent
    ? Math.min(targetSpeed, safeCurrent + rate * deltaSeconds)
    : Math.max(targetSpeed, safeCurrent - rate * deltaSeconds);
  const distance = Math.min(remainingDistance, Math.max(0.01, (safeCurrent + nextSpeed) * 0.5 * deltaSeconds));
  return { speed: distance >= remainingDistance ? 0 : nextSpeed, distance };
}
