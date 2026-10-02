// 单请求 JSONL 桥接：Pi 负责模型/工具循环，Python 负责受控业务操作及审计。
import { Agent } from '@earendil-works/pi-agent-core';
import { streamSimple } from '@earendil-works/pi-ai/api/openai-completions';
import { createInterface } from 'node:readline';

const input = createInterface({ input: process.stdin, crlfDelay: Infinity });
const pending = new Map();
let agent, key = '', started = false, sequence = 0;
const redact = value => String(value).split(key || '\0').join('[已隐去密钥]');
function emit(value) {
  const line = JSON.stringify(value);
  if (Buffer.byteLength(line) > 1024 * 1024) throw new Error('Pi 桥接消息超过大小上限');
  return new Promise((resolve, reject) => process.stdout.write(line + '\n', error => error ? reject(error) : resolve()));
}
function fail(error) {
  console.error(redact(error?.stack || error));
  void emit({ type: 'error', message: 'Pi 执行失败，请检查模型配置及服务端日志' }).catch(error => {
    console.error(redact(error?.stack || error));
  }).finally(() => {
    input.close(); process.exitCode = 1;
  });
}
function abort() {
  agent?.abort();
  for (const { reject } of pending.values()) reject(new Error('Pi 会话已取消'));
  pending.clear();
}
process.on('SIGTERM', abort);
input.on('close', abort);

async function run(request) {
  key = request.api_key;
  const provider = 'someip-gateway';
  const model = {
    id: request.model, name: request.model, provider, api: 'openai-completions',
    baseUrl: request.base_url, input: ['text'], reasoning: false,
    cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
    contextWindow: 32768, maxTokens: 4096,
    compat: { supportsDeveloperRole: false, supportsStore: false,
      supportsReasoningEffort: false, supportsUsageInStreaming: false, supportsFinishReason: true },
  };
  let rounds = 0, toolCount = 0, outputBytes = 0, hadText = false, turnText = false;
  const executed = new Set();
  const tools = request.tools.map(tool => ({
    name: tool.name, label: tool.name, description: tool.description,
    parameters: tool.parameters, executionMode: 'sequential',
    execute: async (callId, args, signal) => {
      if (++toolCount > 16) throw new Error('工具调用数量超过安全上限');
      if (signal?.aborted) throw new Error('Pi 会话已取消');
      executed.add(callId);
      const id = ++sequence;
      const result = await new Promise((resolve, reject) => {
        pending.set(id, { resolve, reject });
        void emit({ type: 'tool_call', id, call_id: callId, name: tool.name, arguments: args })
          .catch(error => { pending.delete(id); reject(error); });
      });
      return { content: [{ type: 'text', text: JSON.stringify(result) }], details: result };
    },
  }));
  // 历史只有文本；不允许外部注入 system/tool 消息或历史写授权。
  const usage = { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, totalTokens: 0,
    cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 } };
  const history = request.history.map(item => item.role === 'user'
    ? { role: 'user', content: item.content, timestamp: Date.now() }
    : { role: 'assistant', content: [{ type: 'text', text: item.content }],
      api: model.api, model: model.id, provider, usage, stopReason: 'stop', timestamp: Date.now() });
  agent = new Agent({
    initialState: { model, systemPrompt: request.system_prompt, tools, thinkingLevel: 'off' },
    toolExecution: 'sequential', getApiKey: () => key,
    afterToolCall: async ({ result }) => result.details?.error ? { isError: true } : undefined,
    // 网络重定向禁用；只访问 Python 已校验的网关。凭据不从环境/Skills/插件解析。
    streamFn: (selected, context, options) => streamSimple(selected, context, {
      ...options, apiKey: key, temperature: request.temperature,
      timeoutMs: request.timeout_ms, maxRetries: 0, env: {},
      fetch: (url, init) => fetch(url, { ...init, redirect: 'error' }),
      onResponse: response => {
        if (!response.headers['content-type']?.includes('text/event-stream'))
          throw new Error('模型网关未返回 SSE 流');
      },
    }),
    prepareRequest: async () => {
      if (++rounds > 4) throw new Error('工具调用轮次超过安全上限');
    },
    finishTurn: ({ message }) => {
      if (rounds >= 4 && message.stopReason === 'toolUse') return { action: 'end' };
    },
  });
  agent.subscribe(async event => {
    if (event.type === 'turn_start') {
      turnText = false;
      await emit({ type: 'status', round: rounds + 1 });
    } else if (event.type === 'message_update' && event.assistantMessageEvent.type === 'text_delta') {
      let text = event.assistantMessageEvent.delta;
      if (hadText && !turnText) text = '\n\n' + text;
      hadText = true; turnText = true;
      outputBytes += Buffer.byteLength(text);
      if (outputBytes > 256 * 1024) throw new Error('回答超过大小上限');
      await emit({ type: 'delta', text });
    } else if (event.type === 'tool_execution_end' && !executed.has(event.toolCallId)) {
      await emit({ type: 'tool_rejected', call_id: event.toolCallId,
        name: event.toolName, result: { error: 'Pi 拒绝未知工具或不符合工具 schema 的参数' } });
    }
  });
  await agent.prompt([...history, { role: 'user', content: request.message, timestamp: Date.now() }]);
  const last = agent.state.messages.findLast(message => message.role === 'assistant');
  if (agent.state.errorMessage || !last || last.stopReason !== 'stop') {
    console.error(redact(agent.state.errorMessage || `Pi 未正常完成: ${last?.stopReason}`));
    await emit({ type: 'error', message: rounds >= 4 && last?.stopReason === 'toolUse'
      ? '工具调用轮次超过安全上限，请缩小问题范围后重试'
      : 'Pi 模型响应未正常完成，请检查网关日志后重试' });
    process.exitCode = 1;
  } else if (!last.content.some(item => item.type === 'text' && item.text.trim())) {
    await emit({ type: 'error', message: '模型未返回文本内容，请重试' });
    process.exitCode = 1;
  } else await emit({ type: 'done', runtime: 'pi-agent-core', model: model.id });
  input.close();
}

input.on('line', line => {
  try {
    if (Buffer.byteLength(line) > 1024 * 1024) throw new Error('Pi 请求超过大小上限');
    const message = JSON.parse(line);
    if (!started && message.type === 'start') {
      started = true;
      void run(message).catch(fail);
    } else if (message.type === 'tool_result' && pending.has(message.id)) {
      const reply = pending.get(message.id); pending.delete(message.id); reply.resolve(message.result);
    } else throw new Error('Pi 桥接消息类型或请求 ID 无效');
  } catch (error) { abort(); fail(error); }
});
