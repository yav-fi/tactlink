import {worldCoordinates, geographicCoordinates} from './map-math.mjs';
import {prepareAddition} from './command-entry.mjs';
import {timeline,sample} from './playback.mjs';
const $ = id => document.getElementById(id);
const R = 6378137, rad = Math.PI / 180;
let lines = [], preview = null, partialPreview = null, revision = 0, timer, zoom = 18;
let aiProposal=null;
let playbackTrack=null, playbackSource=null, playbackTime=0, playing=false, playbackLast=0, playbackFrame=0;
function playbackControls(){
  const source=aiProposal?null:preview;
  if(source!==playbackSource){
    cancelAnimationFrame(playbackFrame);playing=false;playbackTime=0;playbackSource=source;
    playbackTrack=source?timeline(source.segments):null;
  }
  const enabled=!!playbackTrack?.total;
  $('play-mission').disabled=$('restart-mission').disabled=!enabled;
  $('play-mission').textContent=playing?'Pause':'Play';
  const state=enabled?sample(playbackTrack,playbackTime):null;
  $('play-status').textContent=state?`${playbackTime>=playbackTrack.total?'Complete':playing?'Playing':'Paused'} · ${playbackTime.toFixed(1)} / ${playbackTrack.total.toFixed(1)} s · Command ${state.command+1} · Altitude ${state.point.z.toFixed(1)} m`:'Validate a mission to enable playback.';
  [...$('commands').children].forEach((row,i)=>{row.style.outline=state?.command===i?'2px solid #da8a16':'';});
  return state;
}
function playbackTick(now){
  if(!playing)return;
  playbackTime=Math.min(playbackTrack.total,playbackTime+(now-playbackLast)/1000*Number($('play-speed').value));
  playbackLast=now;
  if(playbackTime>=playbackTrack.total)playing=false;
  draw();if(playing)playbackFrame=requestAnimationFrame(playbackTick);
}
$('play-mission').onclick=()=>{
  if(!playbackTrack?.total)return;
  if(playing){playing=false;cancelAnimationFrame(playbackFrame);draw();return;}
  if(playbackTime>=playbackTrack.total)playbackTime=0;
  playing=true;playbackLast=performance.now();draw();playbackFrame=requestAnimationFrame(playbackTick);
};
$('restart-mission').onclick=()=>{playing=false;cancelAnimationFrame(playbackFrame);playbackTime=0;draw();};
document.addEventListener('visibilitychange',()=>{if(document.hidden&&playing){playing=false;cancelAnimationFrame(playbackFrame);draw();}});
function discardProposal(){aiProposal=null;$('ai-review').hidden=true;exportState();}
let center = {lat:38.8895, lon:-77.0353};
const map = $('map'), svg = $('drawing');
const number = id => $(id).value.trim() === '' ? NaN : Number($(id).value);
const home = () => ({latitude_deg:number('latitude'), longitude_deg:number('longitude'), altitude_msl_m:number('home-altitude')});
const validLocation = h => Number.isFinite(h.latitude_deg) && Math.abs(h.latitude_deg) <= 85 && Number.isFinite(h.longitude_deg) && Math.abs(h.longitude_deg) <= 180;
function inputFeedback(text, error=false) {
  $('input-feedback').textContent=text;
  $('input-feedback').style.color=error?'#974d2f':'';
}
function message(text, error=false) {
  $('status').textContent=text; $('status').classList.toggle('error', error);
  $('placement-feedback').textContent=text;
  const match=error && text.match(/Command (\d+):/i);
  $('show-command-error').hidden=!match || Number(match[1]) > lines.length;
  if(match){
    const index=Number(match[1]);
    $('show-command-error').textContent=`Show command ${index}`;
    $('show-command-error').onclick=()=>{
      const input=$('commands').children[index-1]?.querySelector('input');
      input?.scrollIntoView({behavior:'smooth',block:'center'});input?.focus({preventScroll:true});
    };
  }
  if(error)inputFeedback(text,true);
}
async function post(url, data) {
  const response = await fetch(url, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(data)});
  const body = await response.json();
  if (!response.ok) throw new Error(typeof body.detail === 'string' ? body.detail : 'Check the input values.');
  return body;
}
function save() {
  try { localStorage.setItem('waypoint-draft-v1', JSON.stringify({lines, home:home()})); } catch { /* Browser storage is optional. */ }
}
try {
  const draft = JSON.parse(localStorage.getItem('waypoint-draft-v1') || 'null');
  if (draft && Array.isArray(draft.lines) && draft.lines.length <= 100 && draft.lines.every(x=>typeof x==='string')) lines=draft.lines;
  if (draft && validLocation(draft.home)) {
    $('latitude').value=draft.home.latitude_deg; $('longitude').value=draft.home.longitude_deg;
    if (Number.isFinite(draft.home.altitude_msl_m)) $('home-altitude').value=draft.home.altitude_msl_m;
    center={lat:draft.home.latitude_deg,lon:draft.home.longitude_deg};
  }
} catch { /* Ignore malformed drafts. */ }
function exportState() { $('export').disabled = !!aiProposal || !preview || !validLocation(home()) || !Number.isFinite(home().altitude_msl_m) || !$('home-confirmed').checked; }
function changed() {
  discardProposal();
  revision++; preview=null; partialPreview=null; exportState(); save(); draw(); renderCommands();
  clearTimeout(timer);
  if (!lines.length) { message('Add a takeoff command to begin.'); $('summary').textContent=''; return; }
  message('Validating draft…');
  const current=revision;
  timer=setTimeout(async()=>{
    try {
      const h=home();
      if (!validLocation(h)) throw new Error('Enter a valid latitude and longitude for the map.');
      const result=await post('/api/preview', {text:lines.join(', '), origin:Number.isFinite(h.altitude_msl_m)?h:null,map_origin:{latitude_deg:h.latitude_deg,longitude_deg:h.longitude_deg}});
      if (current!==revision) return;
      preview=result; draw(); exportState();
      const end=result.end_state;
      message(`Validated · ${result.commands.length} commands. ${end.airborne?'Mission ends airborne.':'Mission ends on the ground.'}`);
      $('summary').textContent=`Ends ${end.north_m.toFixed(1)} m north, ${end.east_m.toFixed(1)} m east, ${end.altitude_m.toFixed(1)} m above home.`;
    } catch(error) { if(current===revision){
      message(error.message,true); $('summary').textContent='Export is unavailable until the draft is valid.';
      const index=Number(error.message.match(/Command (\d+):/i)?.[1])-1;
      if(index>0)try {
        const result=await post('/api/preview',{text:lines.slice(0,index).join(', '),publish:false});
        if(current!==revision)return;
        partialPreview=result;draw();
        message(`${error.message} Showing only the valid first ${index} commands. Fix the sequence or start fresh with a backup.`,true);
      }catch { /* Keep the original validation error visible. */ }
    } }
  },200);
}
function renderCommands() {
  for(const id of ['insert-position','map-position']){
    const selected=$(id).value;
    $(id).replaceChildren(new Option('At the end of the mission','end'));
    lines.forEach((line,index)=>$(id).add(new Option(`Before ${index+1}: ${line}`,String(index))));
    if([...$(id).options].some(option=>option.value===selected))$(id).value=selected;
  }
  $('commands').replaceChildren(); $('count').textContent=lines.length; $('empty').hidden=lines.length>0;
  lines.forEach((line,index)=>{
    const li=document.createElement('li'), head=document.createElement('div'); head.className='row-head';
    const label=document.createElement('strong'); label.textContent=`${String(index+1).padStart(2,'0')} · COMMAND`; head.append(label);
    for(const [text,action,name] of [['↑',()=>{if(index){[lines[index-1],lines[index]]=[lines[index],lines[index-1]];changed();}},'Move up'],['↓',()=>{if(index<lines.length-1){[lines[index+1],lines[index]]=[lines[index],lines[index+1]];changed();}},'Move down'],['×',()=>{lines.splice(index,1);changed();},'Delete']]) {
      const b=document.createElement('button'); b.className='secondary'; b.textContent=text; b.ariaLabel=`${name} command ${index+1}`; b.onclick=action;
      b.disabled=(text==='↑'&&index===0)||(text==='↓'&&index===lines.length-1); head.append(b);
    }
    const input=document.createElement('input'); input.value=line; input.ariaLabel=`Command ${index+1}`;
    input.oninput=()=>{discardProposal();revision++;preview=null;partialPreview=null;exportState();draw();clearTimeout(timer);message('Command changed. Press Enter or leave the field to validate.');};
    input.onchange=()=>{lines[index]=input.value;changed();};
    input.onkeydown=e=>{if(e.key==='Enter')input.blur();}; li.append(head,input); $('commands').append(li);
  });
}
let addingPlacement=false;
async function add(line) {
  if(addingPlacement)return;
  addingPlacement=true;const current=revision;
  message('Checking map command...');
  try {
    const result=await prepareAddition([...lines],line,$('map-position').value,post);
    if(current!==revision)throw new Error('Draft changed while checking. Try placing again.');
    lines=result.lines;changed();
    message(`Added command ${result.index+1}: ${line}`);
  }catch(error){message(`Nothing added. ${error.message} Choose an insertion point before Land, or start fresh with a backup.`,true);}
  finally{addingPlacement=false;}
}
$('fresh-start').onclick=()=>{
  try{localStorage.setItem('waypoint-draft-backup-v1',JSON.stringify({lines,home:home()}));}
  catch{message('Cannot save a backup. Your draft has been kept.',true);return;}
  lines=[`take off to ${number('altitude')} meters`];
  $('map-position').value=$('insert-position').value='end';
  $('action').value='waypoint';placementFields();
  $('restore-draft').hidden=false;changed();
};
$('restore-draft').onclick=()=>{
  try{
    const draft=JSON.parse(localStorage.getItem('waypoint-draft-backup-v1'));
    if(!Array.isArray(draft?.lines))throw new Error('No saved backup is available.');
    lines=draft.lines;
    for(const [id,key] of [['latitude','latitude_deg'],['longitude','longitude_deg'],['home-altitude','altitude_msl_m']])$(id).value=draft.home[key]??'';
    $('home-confirmed').checked=false;changed();
  }catch(error){message(error.message,true);}
};
try{$('restore-draft').hidden=!localStorage.getItem('waypoint-draft-backup-v1');}catch{}
function placementFields() {
  const kind=$('action').value;
  $('alt-field').hidden=!['waypoint','takeoff','change_altitude'].includes(kind);
  $('radius-field').hidden=$('orbit-fields').hidden=kind!=='orbit';
  $('duration-field').hidden=kind!=='hover';
  $('add-placement').hidden=['waypoint','orbit'].includes(kind);
  $('placement-help').textContent=kind==='orbit'?'Click an orbit center. The orbit keeps the current planned altitude.':kind==='waypoint'?'Click a destination on the map. Add takeoff first.':'Set the value, then add the command.';
}
$('action').onchange=placementFields;
$('instruction').addEventListener('input',()=>{discardProposal();draw();});
$('insert-position').addEventListener('change',()=>{discardProposal();draw();});
$('dismiss-ai').onclick=()=>{discardProposal();draw();};
$('interpret').onclick=async()=>{
  discardProposal();const current=revision,text=$('instruction').value,position=$('insert-position').value;
  if(!text.trim()){inputFeedback('Type or speak an instruction first.',true);return;}
  $('interpret').disabled=true;inputFeedback('Interpreting with OpenAI...');
  try{
    const result=await post('/api/planner/interpret',{text,lines:[...lines],position:position==='end'?lines.length:Number(position)});
    if(current!==revision||text!==$('instruction').value||position!==$('insert-position').value)throw new Error('The instruction or mission changed. Interpret again.');
    if(result.question){inputFeedback(`${result.question} Update your instruction with the answer, then interpret again.`);return;}
    aiProposal={...result,revision:current,text,position};
    $('ai-explanation').textContent=result.explanation;
    $('ai-lines').textContent=result.lines.map((line,i)=>`${i+1}. ${line}`).join('\n');
    $('ai-review').hidden=false;exportState();draw();message('AI proposal preview — review before adding to the mission.');
    inputFeedback('Review the proposed route and commands, then Add reviewed commands.');
  }catch(error){inputFeedback(error.message,true);}
  finally{$('interpret').disabled=false;}
};
$('accept-ai').onclick=async()=>{
  const proposal=aiProposal;if(!proposal)return;
  $('accept-ai').disabled=true;
  try{
    if(proposal.revision!==revision||proposal.text!==$('instruction').value||proposal.position!==$('insert-position').value)throw new Error('Proposal is stale. Interpret again.');
    const result=await prepareAddition([...lines],proposal.lines.join(', '),proposal.position,post);
    if(aiProposal!==proposal||proposal.revision!==revision)throw new Error('Draft changed. Interpret again.');
    lines=result.lines;$('instruction').value='';changed();inputFeedback(`Added ${result.count} reviewed commands.`);
  }catch(error){discardProposal();draw();inputFeedback(error.message,true);}
  finally{$('accept-ai').disabled=false;}
};
fetch('/api/planner/ai/status').then(r=>r.json()).then(s=>{
  $('ai-status').textContent=s.available?'AI ready. Interpret sends text and mission coordinates to OpenAI; audio stays local.':'AI needs OPENAI_API_KEY on the backend. Explicit commands and local speech still work.';
}).catch(()=>{$('ai-status').textContent='Cannot reach AI configuration service.';});
$('add-placement').onclick=()=>{
  const kind=$('action').value;
  const line={takeoff:`take off to ${number('altitude')} meters`,change_altitude:`change altitude to ${number('altitude')} meters`,hover:`hover for ${number('duration')} seconds`,return_home:'return home',land:'land'}[kind];
  if(line)add(line);
};
$('add-text').onclick=async()=>{
  $('add-text').disabled=true;
  $('add-text').textContent='Checking…';
  inputFeedback('Checking your commands against the mission…');
  const current=revision;
  try {
    const text=$('instruction').value;
    const result=await prepareAddition([...lines],text,$('insert-position').value,post);
    if(current!==revision)throw new Error('The draft changed while checking. Please add the instruction again.');
    lines=result.lines;
    if($('instruction').value===text)$('instruction').value='';
    changed();
    inputFeedback(`Added ${result.count} command${result.count===1?'':'s'} starting at position ${result.index+1}.`);
  } catch(error) {
    const explanation=error.message.includes('Take off before')
      ? `${error.message} An airborne command cannot follow Land. Insert it before Land, or add an explicit takeoff to begin another flight.`
      : error.message;
    inputFeedback(`Nothing added. ${explanation}`,true);
    message(`Nothing added. ${explanation}`,true);
  } finally {$('add-text').disabled=false;$('add-text').textContent='Add to mission';}
};
$('clear').onclick=()=>{lines=[];changed();};
for(const id of ['latitude','longitude','home-altitude']) $(id).oninput=()=>{
  $('home-confirmed').checked=false;
  const h=home(); if(validLocation(h))center={lat:h.latitude_deg,lon:h.longitude_deg}; changed();
};
$('home-confirmed').onchange=exportState;
$('export').onclick=async()=>{
  const current=revision; $('export').disabled=true;
  try {
    const response=await fetch('/api/planner/export',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text:lines.join(', '),origin:home()})});
    if(!response.ok){const data=await response.json();throw new Error(typeof data.detail==='string'?data.detail:'Export failed.');}
    const blob=await response.blob(); if(current!==revision)throw new Error('Draft changed during export. Download the updated draft again.');
    const url=URL.createObjectURL(blob), a=document.createElement('a'); a.href=url;a.download='mission.waypoints';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
    message('Downloaded mission.waypoints. Open it in Mission Planner to review the mission.');
  } catch(error){message(error.message,true);} finally{exportState();}
};

// Mercator basemap projection. Route geometry remains the backend's ENU frame.
function world(lat,lon) {return worldCoordinates(lat,lon,zoom);}
function geographic(x,y) {return geographicCoordinates(x,y,zoom);}
function pixel(lat,lon) {const a=world(lat,lon),b=world(center.lat,center.lon),scale=256*2**zoom;let dx=a.x-b.x;dx=((dx+scale/2)%scale+scale)%scale-scale/2;return{x:dx+map.clientWidth/2,y:a.y-b.y+map.clientHeight/2};}
function geoPoint(point) {const h=home();return{lat:h.latitude_deg+point.y/R/rad,lon:h.longitude_deg+point.x/(R*Math.cos(h.latitude_deg*rad))/rad};}
function pointPixel(point){const g=geoPoint(point);return pixel(g.lat,g.lon);}
function element(type,attributes,text){const e=document.createElementNS('http://www.w3.org/2000/svg',type);for(const[k,v]of Object.entries(attributes))e.setAttribute(k,v);if(text!==undefined)e.textContent=text;svg.append(e);return e;}
function draw() {
  const playback=playbackControls();
  svg.replaceChildren(); const h=home();if(!validLocation(h))return;
  const w=map.clientWidth,ht=map.clientHeight;svg.setAttribute('viewBox',`0 0 ${w} ${ht}`);
  for(let n=-500;n<=500;n+=50){
    for(const pair of [[{x:n,y:-500,z:0},{x:n,y:500,z:0}],[{x:-500,y:n,z:0},{x:500,y:n,z:0}]]){
      const a=pointPixel(pair[0]),b=pointPixel(pair[1]);element('line',{x1:a.x,y1:a.y,x2:b.x,y2:b.y,stroke:'#aec3ad','stroke-width':n===0?1.5:.6,opacity:$('street-map').checked?.25:.65});
    }
  }
  const boundary=Array.from({length:73},(_,i)=>pointPixel({x:200*Math.cos(i*Math.PI/36),y:200*Math.sin(i*Math.PI/36),z:0}));
  element('polyline',{points:boundary.map(p=>`${p.x},${p.y}`).join(' '),fill:'none',stroke:'#809882','stroke-dasharray':'6 6','stroke-width':1.5});
  const displayed=aiProposal?.preview||preview||partialPreview;
  if(displayed){
    const pts=displayed.segments.flatMap(s=>s.points.map(pointPixel));
    element('polyline',{points:pts.map(p=>`${p.x},${p.y}`).join(' '),fill:'none',stroke:'#197350','stroke-width':3,'stroke-linejoin':'round'});
    const endpoints=new Map();
    for(const s of displayed.segments){
      endpoints.set(s.command_index,s.end);
      if(s.type==='arc'){const p=pointPixel(s.center);element('circle',{cx:p.x,cy:p.y,r:5,fill:'#e7a643',stroke:'white','stroke-width':2});element('text',{x:p.x+9,y:p.y-9,fill:'#80510c','font-size':11},'Orbit center');}
    }
    for(const[index,point]of endpoints){const p=pointPixel(point);element('circle',{cx:p.x,cy:p.y,r:11,fill:'#197350',stroke:'white','stroke-width':2});element('text',{x:p.x,y:p.y+4,'text-anchor':'middle',fill:'white','font-size':10,'font-weight':700},index+1);}
  }
  const p=pointPixel({x:0,y:0,z:0});element('rect',{x:p.x-10,y:p.y-10,width:20,height:20,rx:5,fill:'#fff',stroke:'#345c45','stroke-width':2});element('text',{x:p.x,y:p.y+4,'text-anchor':'middle',fill:'#345c45','font-size':11,'font-weight':700},'H');
  if(playback){const p=pointPixel(playback.point);element('circle',{cx:p.x,cy:p.y,r:8,fill:'#f6a623',stroke:'#513500','stroke-width':2});element('text',{x:p.x+12,y:p.y-12,fill:'#513500','font-size':12},`Drone · ${playback.point.z.toFixed(1)} m`);}
  drawTiles();
}
function drawTiles(){
  const tiles=$('tiles');if(!$('street-map').checked){tiles.replaceChildren();return;}
  const c=world(center.lat,center.lon),left=c.x-map.clientWidth/2,top=c.y-map.clientHeight/2,n=2**zoom;
  const existing=new Map([...tiles.children].map(e=>[e.dataset.key,e])),keep=new Set();
  for(let x=Math.floor(left/256);x<=Math.floor((left+map.clientWidth)/256);x++)for(let y=Math.floor(top/256);y<=Math.floor((top+map.clientHeight)/256);y++){
    if(y<0||y>=n)continue;const key=`${zoom}/${x}/${y}`;keep.add(key);let img=existing.get(key);
    if(!img){img=document.createElement('img');img.alt='';img.dataset.key=key;img.src=`https://tile.openstreetmap.org/${zoom}/${((x%n)+n)%n}/${y}.png`;img.onerror=()=>{$('map-caption').textContent='Street tiles unavailable · coordinate grid remains usable';};tiles.append(img);}
    img.style.left=`${x*256-left}px`;img.style.top=`${y*256-top}px`;
  }
  for(const[key,img]of existing)if(!keep.has(key))img.remove();
}
$('street-map').onchange=()=>{$('attribution').hidden=!$('street-map').checked;$('map-caption').textContent=$('street-map').checked?'Street map · drag to pan':'Offline coordinate grid · 50 m spacing';draw();};
$('zoom-in').onclick=()=>{zoom=Math.min(20,zoom+1);draw();};$('zoom-out').onclick=()=>{zoom=Math.max(15,zoom-1);draw();};
$('recenter').onclick=()=>{const h=home();if(validLocation(h)){center={lat:h.latitude_deg,lon:h.longitude_deg};draw();}};
let drag=null;
map.onpointerdown=e=>{if(e.target.closest('button,a'))return;drag={x:e.clientX,y:e.clientY,center:world(center.lat,center.lon)};map.setPointerCapture(e.pointerId);};
map.onpointermove=e=>{
  const rect=map.getBoundingClientRect(),c=world(center.lat,center.lon),g=geographic(c.x+e.clientX-rect.left-map.clientWidth/2,c.y+e.clientY-rect.top-map.clientHeight/2),h=home();
  const east=(g.lon-h.longitude_deg)*rad*R*Math.cos(h.latitude_deg*rad),north=(g.lat-h.latitude_deg)*rad*R;
  $('cursor').textContent=`E ${east.toFixed(1)} · N ${north.toFixed(1)} m`;
};
map.onpointerup=e=>{
  if(!drag)return;const d=drag;drag=null;
  if(Math.hypot(e.clientX-d.x,e.clientY-d.y)>5){center=geographic(d.center.x-(e.clientX-d.x),d.center.y-(e.clientY-d.y));draw();return;}
  const h=home();if(!validLocation(h)){message('Set a valid home location first.',true);return;}
  const rect=map.getBoundingClientRect(),c=world(center.lat,center.lon),g=geographic(c.x+e.clientX-rect.left-map.clientWidth/2,c.y+e.clientY-rect.top-map.clientHeight/2);
  const east=(((g.lon-h.longitude_deg+540)%360)-180)*rad*R*Math.cos(h.latitude_deg*rad),north=(g.lat-h.latitude_deg)*rad*R;
  if($('action').value==='waypoint')add(`go to point ${north.toFixed(2)} ${east.toFixed(2)} at altitude ${number('altitude')} meters`);
  else if($('action').value==='orbit')add(`orbit point ${north.toFixed(2)} ${east.toFixed(2)} at radius ${number('radius')} meters ${$('direction').value} ${number('laps')} laps`);
};
map.onpointercancel=()=>drag=null;
new ResizeObserver(draw).observe(map);

// Push-to-talk captures locally and sends PCM only to this local Python server.
let wanted=false,starting=false,recording=null,transcribing=false;
async function startRecording(){
  if(starting||recording||transcribing)return;wanted=true;starting=true;
  let stream,context;
  try{
    if(!navigator.mediaDevices?.getUserMedia)throw new Error('Microphone capture requires localhost or HTTPS in a supported browser.');
    stream=await navigator.mediaDevices.getUserMedia({audio:{channelCount:1,echoCancellation:true}});
    if(!wanted){stream.getTracks().forEach(t=>t.stop());return;}
    context=new AudioContext();await context.resume();await context.audioWorklet.addModule('recorder-worklet.js');
    if(!wanted){stream.getTracks().forEach(t=>t.stop());await context.close();return;}
    const source=context.createMediaStreamSource(stream),node=new AudioWorkletNode(context,'capture'),mute=context.createGain();mute.gain.value=0;
    const chunks=[];node.port.onmessage=e=>chunks.push(e.data);source.connect(node);node.connect(mute);mute.connect(context.destination);
    recording={stream,context,node,chunks,source,mute,timer:setTimeout(stopRecording,30000)};
    $('record').classList.add('recording');$('record').textContent='● Recording… release';$('speech-status').textContent='Recording locally · release to transcribe · maximum 30 seconds';
  }catch(error){stream?.getTracks().forEach(t=>t.stop());if(context&&context.state!=='closed')await context.close();$('speech-status').textContent=error.message;wanted=false;}
  finally{starting=false;}
}
async function stopRecording(){
  wanted=false;if(!recording)return;const capture=recording;recording=null;clearTimeout(capture.timer);
  capture.node.disconnect();capture.source.disconnect();capture.stream.getTracks().forEach(t=>t.stop());await capture.context.close();
  $('record').classList.remove('recording');$('record').textContent='● Hold to speak';transcribing=true;$('record').disabled=true;
  try{
    const length=capture.chunks.reduce((n,c)=>n+c.length,0);if(length/capture.context.sampleRate<.25)throw new Error('Hold the button while speaking, then release.');
    const audio=new Float32Array(length);let offset=0;for(const chunk of capture.chunks){audio.set(chunk,offset);offset+=chunk.length;}
    const offline=new OfflineAudioContext(1,Math.min(480000,Math.round(length*16000/capture.context.sampleRate)),16000);
    const buffer=offline.createBuffer(1,length,capture.context.sampleRate);buffer.copyToChannel(audio,0);
    const source=offline.createBufferSource();source.buffer=buffer;source.connect(offline.destination);source.start();
    const samples=(await offline.startRendering()).getChannelData(0),wav=new ArrayBuffer(44+samples.length*2),view=new DataView(wav);
    function ascii(at,text){for(let i=0;i<text.length;i++)view.setUint8(at+i,text.charCodeAt(i));}
    ascii(0,'RIFF');view.setUint32(4,wav.byteLength-8,true);ascii(8,'WAVEfmt ');view.setUint32(16,16,true);view.setUint16(20,1,true);view.setUint16(22,1,true);view.setUint32(24,16000,true);view.setUint32(28,32000,true);view.setUint16(32,2,true);view.setUint16(34,16,true);ascii(36,'data');view.setUint32(40,samples.length*2,true);
    samples.forEach((v,i)=>view.setInt16(44+i*2,Math.round(Math.max(-1,Math.min(1,v))*32767),true));
    $('speech-status').textContent='Transcribing locally… First use may download the speech model.';
    const response=await fetch('/api/speech/transcribe',{method:'POST',headers:{'Content-Type':'audio/wav'},body:wav});
    const result=await response.json();if(!response.ok)throw new Error(result.detail||'Transcription failed.');
    $('instruction').value=result.normalized_text;$('speech-status').textContent=`Heard: “${result.transcript}” Review the text, then Add to mission.`;
  }catch(error){$('speech-status').textContent=error.message;}finally{transcribing=false;$('record').disabled=false;}
}
$('record').onpointerdown=e=>{e.preventDefault();$('record').setPointerCapture(e.pointerId);startRecording();};
$('record').onpointerup=stopRecording;$('record').onpointercancel=stopRecording;
$('record').onkeydown=e=>{if([' ','Enter'].includes(e.key)){e.preventDefault();if(!e.repeat)startRecording();}};
$('record').onkeyup=e=>{if([' ','Enter'].includes(e.key))stopRecording();};
window.addEventListener('blur',stopRecording);
fetch('/api/speech/status').then(r=>r.json()).then(s=>{$('speech-status').textContent=s.available?'Local speech ready · hold the button while speaking.':'Install requirements-speech.txt to enable local speech.';$('record').disabled=!s.available;}).catch(()=>{$('speech-status').textContent='Cannot reach the local speech service.';});
placementFields();changed();
