import * as Cesium from 'cesium';
import {previewCoordinate, type FlightPreview} from './flight-preview';

// Read-only overlay, independent of world-map flight and gesture controls.
export function startPlannerLink(viewer: Cesium.Viewer) {
  const panel=document.createElement('div');
  panel.style.cssText='background:#182b25;color:white;padding:12px;margin:12px 0;border-radius:8px';
  const button=document.createElement('button');button.textContent='Disconnect planner';
  const label=document.createElement('p');label.textContent='Connecting to the local waypoint planner…';
  panel.append(button,label);
  const worldStatus=document.getElementById('world-status');
  if(worldStatus)worldStatus.insertAdjacentElement('afterend',panel);
  else document.body.append(panel);
  const base=(import.meta.env.VITE_FLIGHT_BRIDGE_URL as string|undefined)||'http://127.0.0.1:8766';
  let connected=true, route:Cesium.Entity|undefined, marker:Cesium.Entity|undefined;
  let mission='', anchor:{latitude:number;longitude:number;altitude:number}|undefined;
  function clear(){if(route)viewer.entities.remove(route);if(marker)viewer.entities.remove(marker);route=marker=undefined;mission='';anchor=undefined;}
  button.onclick=()=>{connected=!connected;button.textContent=connected?'Disconnect planner':'Connect waypoint planner';if(!connected){clear();label.textContent='Disconnected';}};
  async function poll(){
    if(connected)try{
      const response=await fetch(base+'/api/simulation',{signal:AbortSignal.timeout(2000)});
      if(!response.ok)throw new Error('Bridge unavailable');
      const data=await response.json();if(!connected)return;
      const preview=data.preview as (FlightPreview & {origin?:{latitude_deg:number;longitude_deg:number},map_origin?:{latitude_deg:number;longitude_deg:number}})|null;
      const origin=preview?.map_origin||preview?.origin;
      if(!preview||!origin){clear();label.textContent='Reload the waypoint planner to publish its home latitude and longitude.';return;}
      if(mission!==preview.mission_id){
        clear();
        const lat=origin.latitude_deg,lon=origin.longitude_deg;
        let terrain=Cesium.Cartographic.fromDegrees(lon,lat);
        if(viewer.terrainProvider.availability){
          [terrain]=await Cesium.sampleTerrainMostDetailed(viewer.terrainProvider,[terrain]);
        }
        if(!connected)return;
        anchor={latitude:lat,longitude:lon,altitude:terrain.height||0};
        const positions=preview.segments.flatMap(s=>s.points).map(p=>{
          const g=previewCoordinate(p,anchor!);return Cesium.Cartesian3.fromDegrees(g.longitude,g.latitude,g.altitude);
        });
        route=viewer.entities.add({name:'Planner route (simulation)',polyline:{positions,width:3,material:Cesium.Color.ORANGE}});
        marker=viewer.entities.add({name:'Python simulated drone',position:positions[0],point:{pixelSize:14,color:Cesium.Color.YELLOW,disableDepthTestDistance:Number.POSITIVE_INFINITY}});
        mission=preview.mission_id;void viewer.flyTo(route);
      }
      const sim=data.simulation;
      if(marker)marker.show=!data.stale&&sim?.mission_id===mission;
      if(marker?.show&&anchor){
        const g=previewCoordinate(sim,anchor);marker.position=new Cesium.ConstantPositionProperty(Cesium.Cartesian3.fromDegrees(g.longitude,g.latitude,g.altitude));
      }
      label.textContent=data.stale?'Route loaded. Python simulation disconnected or needs L to reload.':`Python simulation: ${sim.complete?'complete':sim.paused?'paused':'playing'} · ${sim.z.toFixed(1)} m above home`;
    }catch{if(marker)marker.show=false;label.textContent='Planner bridge is starting. It will reconnect automatically.';}
    finally{setTimeout(poll,500);}
    else setTimeout(poll,500);
  }
  void poll();
}
