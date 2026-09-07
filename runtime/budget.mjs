import { appendFileSync, readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

export const name = 'paperscout-budget';
export const inject = ['tools'];

export function apply(ctx, config) {
  const sessions = new Map();
  ctx.on('tools/pre-execute', async (exec, next) => {
    const session = exec.agent?.session.header.id;
    if (!session || !/^[a-zA-Z0-9_-]+$/.test(session)) {
      return { kind: 'deny', reason: 'Paper Scout: 缺少有效预算会话' };
    }
    let state = sessions.get(session);
    if (!state) {
      // 策略缺失或损坏时拒绝调用，不能让配置失效变成无限预算。
      try {
        state = { policy: JSON.parse(readFileSync(join(config.policyDir, `${session}.json`), 'utf8')), used: {}, seen: new Set() };
      } catch (error) {
        return { kind: 'deny', reason: `Paper Scout: 无法读取预算策略: ${error.message}` };
      }
      sessions.set(session, state);
    }
    const limit = state.policy.tools[exec.name] ?? 0;
    const signature = `${exec.name}:${JSON.stringify(exec.arguments)}`;
    const ids = Object.values(exec.arguments ?? {}).flat();
    let reason;
    if ((state.used[exec.name] ?? 0) >= limit) reason = '该工具未授权或额度已耗尽，请用已有证据完成输出';
    else if (state.seen.has(signature)) reason = '本会话已执行相同查询，请复用结果';
    else if (exec.name.endsWith('__get_paper_detail') && ids.some(id => state.policy.known_ids.includes(id))) {
      reason = '该论文已有摘要，请复用已提供的证据';
    }
    if (reason) return { kind: 'deny', reason: `Paper Scout: ${reason}` };
    const downstream = await next();
    if (downstream.kind === 'deny') return downstream;
    // next() 可能异步；返回后再次预留额度，避免并行工具调用穿透上限。
    if ((state.used[exec.name] ?? 0) >= limit || state.seen.has(signature)) {
      return { kind: 'deny', reason: 'Paper Scout: 并发调用已占用额度或相同查询' };
    }
    state.used[exec.name] = (state.used[exec.name] ?? 0) + 1;
    state.seen.add(signature);
    appendFileSync(join(config.policyDir, `${session}.calls.jsonl`), JSON.stringify({ call_id: exec.callId, tool: exec.name }) + '\n');
    return downstream;
  });
  if (config.readyFile) writeFileSync(config.readyFile, 'ready');
}
