import {test} from 'node:test';
import assert from 'node:assert/strict';
import {prepareAddition} from '../integrations/waypoint_ui/command-entry.mjs';
test('failed airborne addition after land leaves the draft unchanged',async()=>{
  const original=['take off to 10 meters','land'];
  const post=async(url)=>{
    if(url.endsWith('/parse'))return {lines:['hover for 10 seconds']};
    throw new Error('Command 3: Take off before issuing airborne commands.');
  };
  await assert.rejects(prepareAddition(original,'hover for ten seconds','end',post),/Command 3/);
  assert.deepEqual(original,['take off to 10 meters','land']);
});
test('inserting before land validates the complete candidate before returning it',async()=>{
  const original=['take off to 10 meters','land'];let checked;
  const post=async(url,data)=>{
    if(url.endsWith('/parse'))return {lines:['hover for 10 seconds']};
    checked=data.text;return {};
  };
  const result=await prepareAddition(original,'hover for ten seconds','1',post);
  assert.equal(checked,'take off to 10 meters, hover for 10 seconds, land');
  assert.deepEqual(result.lines,['take off to 10 meters','hover for 10 seconds','land']);
  assert.equal(result.count,1);assert.equal(original.length,2);
});
test('blank input never calls the server',async()=>{
  await assert.rejects(prepareAddition([],'  ','end',()=>{throw new Error('unexpected request');}),/record an instruction/);
});
