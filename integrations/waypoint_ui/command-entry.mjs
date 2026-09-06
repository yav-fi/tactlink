// Validate a candidate before committing it, so a failed Add never changes the draft.
export async function prepareAddition(lines, text, position, post) {
  if (!text.trim()) throw new Error('Type or record an instruction first.');
  const parsed = await post('/api/planner/parse', {text});
  const index = position === 'end' ? lines.length : Number(position);
  if (!Number.isInteger(index) || index < 0 || index > lines.length) throw new Error('Choose a valid insertion position.');
  const candidate = [...lines.slice(0,index), ...parsed.lines, ...lines.slice(index)];
  await post('/api/preview', {text:candidate.join(', '),publish:false});
  return {lines:candidate, count:parsed.lines.length, index};
}
