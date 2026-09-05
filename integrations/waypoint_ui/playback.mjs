// Visual timing only: 5 m/s horizontal, 2 m/s vertical. Not flight dynamics.
export function timeline(segments) {
  let total=0;
  const legs=[];
  for(const segment of segments){
    const points=segment.points;
    if(segment.type==='hold'){
      legs.push({start:points[0],end:points[0],begin:total,duration:segment.duration_s,command:segment.command_index});
      total+=segment.duration_s;continue;
    }
    for(let i=1;i<points.length;i++){
      const start=points[i-1],end=points[i];
      const duration=Math.hypot(end.x-start.x,end.y-start.y,end.z-start.z)/(segment.type==='vertical'?2:5);
      if(duration>0){legs.push({start,end,begin:total,duration,command:segment.command_index});total+=duration;}
    }
  }
  return {legs,total};
}
export function sample(track,time){
  if(!track.legs.length)return null;
  const t=Math.max(0,Math.min(time,track.total));
  const leg=track.legs.find(l=>t<l.begin+l.duration)||track.legs.at(-1);
  const f=Math.max(0,Math.min(1,(t-leg.begin)/leg.duration));
  return {point:Object.fromEntries(['x','y','z'].map(k=>[k,leg.start[k]+(leg.end[k]-leg.start[k])*f])),command:leg.command};
}
