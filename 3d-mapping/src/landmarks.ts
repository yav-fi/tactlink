export type Landmark = {
  name: string;
  latitude: number;
  longitude: number;
  aliases: string[];
};

// Safe viewing points beside prominent DC landmarks, rather than coordinates
// inside their building/monument geometry where collision avoidance would stop.
export const LANDMARKS: Landmark[] = [
  { name: "Washington Monument", latitude: 38.88950, longitude: -77.03435, aliases: ["washington monument", "the monument"] },
  { name: "Lincoln Memorial", latitude: 38.88930, longitude: -77.04925, aliases: ["lincoln memorial"] },
  { name: "White House", latitude: 38.89695, longitude: -77.03655, aliases: ["white house", "the white house"] },
  { name: "U.S. Capitol", latitude: 38.88980, longitude: -77.01200, aliases: ["us capitol", "u s capitol", "capitol", "capitol building"] },
  { name: "Jefferson Memorial", latitude: 38.88165, longitude: -77.03655, aliases: ["jefferson memorial"] },
  { name: "Martin Luther King Jr. Memorial", latitude: 38.88620, longitude: -77.04335, aliases: ["mlk memorial", "martin luther king memorial", "martin luther king jr memorial"] },
  { name: "World War II Memorial", latitude: 38.88940, longitude: -77.03970, aliases: ["world war ii memorial", "world war 2 memorial", "wwii memorial"] },
  { name: "Smithsonian Castle", latitude: 38.88880, longitude: -77.02545, aliases: ["smithsonian castle", "the castle"] },
];

const normalize = (value: string): string => value
  .toLowerCase()
  .replace(/[.’']/g, "")
  .replace(/[^a-z0-9]+/g, " ")
  .replace(/^the\s+/, "")
  .trim();

export function resolveLandmark(query: string): Landmark | undefined {
  const normalized = normalize(query);
  return LANDMARKS.find(landmark => landmark.aliases.some(alias => normalize(alias) === normalized));
}
