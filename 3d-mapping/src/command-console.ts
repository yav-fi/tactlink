export type CommandIntent =
  | { type: "help" }
  | { type: "deploy"; count: number; survey: boolean }
  | { type: "fly"; droneNumber?: number }
  | { type: "release" }
  | { type: "select"; droneNumber: number }
  | { type: "speed"; mph: number }
  | { type: "return"; all: boolean }
  | { type: "reset"; all: boolean }
  | { type: "goto"; latitude: number; longitude: number; altitude?: number; all: boolean }
  | { type: "move"; direction: "forward" | "back" | "left" | "right"; meters: number }
  | { type: "hover"; seconds: number }
  | { type: "orbit"; radius: number; seconds: number }
  | { type: "ai"; instruction: string };

export const COMMANDS = [
  { command: "/deploy", hint: "click the map to deploy · add a count or ‘survey’" },
  { command: "/fly", hint: "pilot the selected drone · optionally add its number" },
  { command: "/release", hint: "stop manual flight and hover" },
  { command: "/select", hint: "select a drone by number" },
  { command: "/speed", hint: "set selected drone speed in mph" },
  { command: "/goto", hint: "fly to latitude, longitude, and optional altitude" },
  { command: "/hover", hint: "hover for a number of seconds" },
  { command: "/orbit", hint: "orbit with optional radius and duration" },
  { command: "/return", hint: "return selected drone home · add ‘all’ for the fleet" },
  { command: "/reset", hint: "reset selected drone · add ‘all’ for the fleet" },
  { command: "/help", hint: "show commands and natural-language examples" },
] as const;

const numberAfter = (text: string, pattern: RegExp): number | undefined => {
  const match = text.match(pattern);
  const value = match ? Number(match[1]) : NaN;
  return Number.isFinite(value) ? value : undefined;
};

export function parseCommandInput(raw: string): CommandIntent {
  const instruction = raw.trim();
  const text = instruction.toLowerCase().replace(/\s+/g, " ");
  if (!text || text === "/" || text === "/help" || text === "help") return { type: "help" };

  const slash = text.startsWith("/");
  const body = slash ? text.slice(1).trim() : text;
  const droneNumber = numberAfter(body, /(?:drone|select|fly|pilot)\s*#?\s*(\d+)/);

  if (/^(deploy|add|launch)(?:\b|$)/.test(body)) {
    const count = numberAfter(body, /(?:deploy|add|launch)\s+(\d+)/) ?? 1;
    return { type: "deploy", count: Math.max(1, Math.min(100, Math.round(count))), survey: /\bsurvey\b/.test(body) };
  }
  if (/^(fly|pilot|control)(?:\b|$)/.test(body)) return { type: "fly", droneNumber };
  if (/^(release|stop flying|stop piloting)(?:\b|$)/.test(body)) return { type: "release" };
  if (/^select(?:\b|$)/.test(body) && droneNumber) return { type: "select", droneNumber };

  const speed = numberAfter(body, /(?:speed|at)\s+(\d+(?:\.\d+)?)\s*(?:mph)?/);
  if (/^(?:set\s+)?speed\b/.test(body) && speed) return { type: "speed", mph: speed };
  if (/^(?:return|come back|go home|return home)\b/.test(body)) return { type: "return", all: /\b(all|fleet|everyone)\b/.test(body) };
  if (/^reset\b/.test(body)) return { type: "reset", all: /\b(all|fleet|everyone)\b/.test(body) };

  const coordinateMatch = body.match(/(?:go|goto|send|fly|move)(?:\s+all\s+drones?)?.*?(?:to|lat(?:itude)?)?\s*(-?\d+(?:\.\d+)?)\s*[, ]+\s*(?:lon(?:gitude)?\s*)?(-?\d+(?:\.\d+)?)(?:\s*[, ]+\s*(?:at|alt(?:itude)?)?\s*(-?\d+(?:\.\d+)?))?/);
  if (coordinateMatch) {
    return {
      type: "goto",
      latitude: Number(coordinateMatch[1]),
      longitude: Number(coordinateMatch[2]),
      altitude: coordinateMatch[3] === undefined ? undefined : Number(coordinateMatch[3]),
      all: /\b(all|fleet|everyone)\b/.test(body),
    };
  }

  const moveMatch = body.match(/^(?:move|go|fly)\s+(forward|back(?:ward)?|left|right)(?:\s+(\d+(?:\.\d+)?))?/);
  if (moveMatch) {
    const direction = moveMatch[1].startsWith("back") ? "back" : moveMatch[1] as "forward" | "left" | "right";
    return { type: "move", direction, meters: Number(moveMatch[2] ?? 50) };
  }
  const hover = body.match(/^hover(?:\s+(?:for\s+)?)?(\d+(?:\.\d+)?)?/);
  if (hover) return { type: "hover", seconds: Number(hover[1] ?? 10) };
  const orbit = body.match(/^orbit(?:\s+(?:at\s+)?)?(\d+(?:\.\d+)?)?(?:\s*m(?:eters?)?)?(?:\s+(?:for\s+)?(\d+(?:\.\d+)?)\s*s(?:econds?)?)?/);
  if (orbit) return { type: "orbit", radius: Number(orbit[1] ?? 30), seconds: Number(orbit[2] ?? 20) };

  if (slash) throw new Error(`Unknown command “/${body.split(" ")[0]}”. Type / to see commands.`);
  return { type: "ai", instruction };
}
