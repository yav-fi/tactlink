import {test} from 'node:test';
import assert from 'node:assert/strict';
import {worldCoordinates,geographicCoordinates} from '../integrations/waypoint_ui/map-math.mjs';
test('map clicks round-trip longitude without a half-world offset',()=>{
  for(const[lat,lon]of [[38.8895,-77.0353],[0,0],[-34,151],[70,179.999]]){
    for(const zoom of [15,18,20]){
      const p=worldCoordinates(lat,lon,zoom),g=geographicCoordinates(p.x,p.y,zoom);
      assert(Math.abs(g.lat-lat)<1e-8);assert(Math.abs(g.lon-lon)<1e-8);
    }
  }
});
test('a click to the right moves east and upward moves north',()=>{
  const p=worldCoordinates(38.8895,-77.0353,18),g=geographicCoordinates(p.x+50,p.y-50,18);
  assert(g.lon>-77.0353);assert(g.lat>38.8895);
  assert(g.lon+77.0353<.001);assert(g.lat-38.8895<.001);
});
