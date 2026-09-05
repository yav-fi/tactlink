import {test} from 'node:test';
import assert from 'node:assert/strict';
import {timeline,sample} from '../integrations/waypoint_ui/playback.mjs';
test('playback interpolates climb, movement, hold and landing',()=>{
  const a={x:0,y:0,z:0},b={x:0,y:0,z:10},c={x:10,y:0,z:10},d={x:10,y:0,z:0};
  const track=timeline([
    {type:'vertical',points:[a,b],command_index:0},
    {type:'line',points:[b,c],command_index:1},
    {type:'hold',points:[c,c],duration_s:3,command_index:2},
    {type:'vertical',points:[c,d],command_index:3}]);
  assert.equal(track.total,15);
  assert.equal(sample(track,2.5).point.z,5);
  assert.equal(sample(track,6).point.x,5);
  assert.equal(sample(track,8).command,2);
  assert.deepEqual(sample(track,100).point,d);
});
test('orbit follows intermediate samples rather than its identical endpoints',()=>{
  const points=[{x:0,y:0,z:10},{x:5,y:0,z:10},{x:5,y:5,z:10},{x:0,y:0,z:10}];
  const track=timeline([{type:'arc',points,command_index:4}]);
  assert.deepEqual(sample(track,1.5).point,{x:5,y:2.5,z:10});
  assert.equal(sample(track,1.5).command,4);
  assert.equal(sample(timeline([]),0),null);
});
