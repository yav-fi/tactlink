export function worldCoordinates(lat, lon, zoom) {
  const scale = 256 * 2 ** zoom, s = Math.sin(lat * Math.PI / 180);
  return {x: (lon + 180) / 360 * scale, y: (.5 - Math.log((1 + s) / (1 - s)) / (4 * Math.PI)) * scale};
}
export function geographicCoordinates(x, y, zoom) {
  const scale = 256 * 2 ** zoom;
  return {lon: ((x / scale * 360) % 360 + 360) % 360 - 180,
    lat: Math.max(-85, Math.min(85, Math.atan(Math.sinh(Math.PI * (1 - 2 * y / scale))) * 180 / Math.PI))};
}
