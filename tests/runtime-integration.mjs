// 可选真实 Harness 工具运行时测试；不会访问 AMiner 或调用模型。
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdtempSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
import * as Budget from '../runtime/budget.mjs';

const repo = process.env.DEEPSEEK_HARNESS_REPO;
if (!repo) throw new Error('Set DEEPSEEK_HARNESS_REPO');
const require = createRequire(join(repo, 'packages/core/tools/package.json'));
const { Context } = await import(pathToFileURL(require.resolve('@deepseek-ai/cordis')));
const { default: SystemPrompt } = await import(pathToFileURL(join(repo, 'packages/core/system-prompt/src/index.ts')));
const { default: ToolRuntime, defineTool } = await import(pathToFileURL(join(repo, 'packages/core/tools/src/index.ts')));
const ctx = new Context();
const dir = mkdtempSync(join(tmpdir(), 'paperscout-runtime-'));
try {
  await ctx.plugin(SystemPrompt);
  await ctx.plugin(ToolRuntime);
  await ctx.plugin(Budget, { policyDir: dir });
  writeFileSync(join(dir, 'integration.json'), JSON.stringify({ tools: { mcp__aminer__get_paper_detail: 1 }, known_ids: [] }));
  let executed = 0;
  ctx.tools.register(defineTool({
    name: 'mcp__aminer__get_paper_detail', description: 'offline counter',
    parameters: { id: { type: 'string' } },
    output: { schema: { type: 'string' }, render: (_args, value) => [{ type: 'text', text: value }] },
    async execute() { executed++; return 'offline result'; },
  }));
  const agent = { session: { header: { id: 'integration' } } };
  const result = await Promise.all([1, 2, 3].map(i => ctx.tools.execute({
    agent, signal: new AbortController().signal, callId: `call-${i}`,
    name: 'mcp__aminer__get_paper_detail', arguments: { id: `paper-${i}` },
  })));
  assert.equal(executed, 1);
  assert.equal(result.filter(r => r.isError).length, 2);
  console.log('Real ToolRuntime: one execution, two requests blocked before dispatch.');
} finally {
  await ctx.fiber.dispose();
  rmSync(dir, { recursive: true });
}
