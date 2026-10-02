// 干净发行包验收：仅使用包内 Node 的标准库，真实 Pi + 本地 SSE 网关，不装 npm/插件/Skills。
import { createServer } from 'node:http';
import assert from 'node:assert/strict';

const base = process.argv[2] || 'http://127.0.0.1:8765/api/v1';
const gateway = createServer(async (request, response) => {
  let input = '';
  for await (const chunk of request) input += chunk;
  const payload = JSON.parse(input);
  response.writeHead(200, { 'content-type': 'text/event-stream' });
  const last = payload.messages.at(-1);
  const send = (delta, finish_reason) => response.write(`data: ${JSON.stringify({
    choices: [{ index: 0, delta, finish_reason }],
  })}\n\n`);
  if (last.role === 'tool') {
    send({ content: '发行包中的 Pi 已读取控制台证据。' }, 'stop');
  } else if (last.content.includes('取消测试')) {
    send({ content: '第一段取消测试文本' }, null);
    // 保持上游打开，必须由客户端取消传播到 Pi 进程后关闭。
    return;
  } else {
    send({ tool_calls: [{ index: 0, id: 'distribution_call', type: 'function',
      function: { name: 'get_monitor_summary', arguments: '{}' } }] }, 'tool_calls');
  }
  response.end('data: [DONE]\n\n');
});
await new Promise(resolve => gateway.listen(0, '127.0.0.1', resolve));
async function call(path, body) {
  const response = await fetch(base + path, { signal: AbortSignal.timeout(10000),
    ...(body === undefined ? {} : { method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify(body) }) });
  return { status: response.status, body: await response.json() };
}
try {
  const catalog = await call('/console/actions');
  assert.equal(catalog.body.runtime.ready, true);
  assert.equal(catalog.body.runtime.plugins_enabled, false);
  assert.equal(catalog.body.runtime.skills_enabled, false);
  assert.equal((await call('/console/actions/clear_monitor', {})).status, 403);
  assert.equal((await call('/console/actions/shell', { allow_mutation: true })).status, 404);
  const settings = await fetch(base + '/settings/llm', {
    method: 'PUT', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ base_url: `http://127.0.0.1:${gateway.address().port}/v1`,
      model: 'qwen3.5-plus', api_key: 'distribution-test-only-key', timeout_seconds: 10,
      temperature: 0.1 }), signal: AbortSignal.timeout(10000),
  });
  assert.equal(settings.status, 200);
  const stream = await fetch(base + '/agent/chat/stream', {
    method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ message: '发行包 Pi 集成测试' }), signal: AbortSignal.timeout(15000),
  });
  const content = await stream.text();
  assert.ok(content.includes('发行包中的 Pi 已读取控制台证据。'));
  assert.ok(content.includes('get_monitor_summary'));
  assert.ok(content.includes('"runtime": "pi-agent-core"'));
  assert.ok(content.includes('"status": "complete"'));
  assert.ok(!content.includes('distribution-test-only-key'));
  const controller = new AbortController();
  const cancellation = await fetch(base + '/agent/chat/stream', {
    method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ message: '取消测试' }), signal: controller.signal,
  });
  const reader = cancellation.body.getReader();
  let partial = '';
  while (!partial.includes('第一段取消测试文本')) {
    const { done, value } = await reader.read();
    assert.equal(done, false);
    partial += new TextDecoder().decode(value);
  }
  controller.abort();
  let active = 1;
  for (let attempt = 0; attempt < 50 && active; attempt++) {
    active = (await call('/console/actions')).body.runtime.active_sessions;
    if (active) await new Promise(resolve => setTimeout(resolve, 100));
  }
  assert.equal(active, 0);
  console.log(JSON.stringify({ status: 'verified', runtime: 'pi-agent-core',
    model_tool_loop: true, cancel_cleanup: true, plugins: false, skills: false,
    node: process.version }, null, 2));
} catch (error) {
  console.error('Pi 发行包验收失败', error.stack);
  process.exitCode = 1;
} finally {
  gateway.closeAllConnections();
  await new Promise(resolve => gateway.close(resolve));
}
