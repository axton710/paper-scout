import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, writeFileSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { apply } from '../runtime/budget.mjs';

function setup(t, tools, known_ids = []) {
  const dir = mkdtempSync(join(tmpdir(), 'paperscout-budget-'));
  t.after(() => rmSync(dir, { recursive: true }));
  writeFileSync(join(dir, 'session.json'), JSON.stringify({ tools, known_ids }));
  let handler;
  apply({ on: (event, callback) => { assert.equal(event, 'tools/pre-execute'); handler = callback; } }, { policyDir: dir });
  const call = (name, args = {}, callId = 'call') => handler({ name, arguments: args, callId, agent: { session: { header: { id: 'session' } } } }, async () => ({ kind: 'allow' }));
  return { call, dir };
}

test('parallel detail calls cannot exceed the admitted budget', async t => {
  const { call, dir } = setup(t, { mcp__aminer__get_paper_detail: 2 });
  const decisions = await Promise.all(Array.from({ length: 8 }, (_, i) => call('mcp__aminer__get_paper_detail', { id: `p${i}` }, `c${i}`)));
  assert.equal(decisions.filter(d => d.kind === 'allow').length, 2);
  assert.equal(readFileSync(join(dir, 'session.calls.jsonl'), 'utf8').trim().split('\n').length, 2);
});

test('known details, repeated queries and unrelated tools are denied', async t => {
  const { call } = setup(t, { mcp__aminer__get_paper_detail: 4, mcp__aminer__search_paper: 3 }, ['known']);
  assert.equal((await call('mcp__aminer__get_paper_detail', { id: 'known' })).kind, 'deny');
  assert.equal((await call('mcp__aminer__search_paper', { query: 'diffusion' })).kind, 'allow');
  assert.equal((await call('mcp__aminer__search_paper', { query: 'diffusion' })).kind, 'deny');
  assert.equal((await call('bash', { command: 'curl example.com' })).kind, 'deny');
});

test('no-tool roles have zero admitted calls', async t => {
  const { call } = setup(t, {});
  assert.equal((await call('mcp__aminer__search_paper', { query: 'test' })).kind, 'deny');
});
