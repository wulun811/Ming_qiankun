// probe_openclaw.js —— v0.11.9m 乾坤镜 OpenClaw 适配层
// 职责：在 OpenClaw Gateway 事件流中注入乾坤镜探针调用
// 依赖：_js_base.js, probe_node.js（零 npm 依赖）
// 方法：显式函数组合 chain()，利用框架官方 hooks 扩展点

const crypto = require('crypto');
const { makeAdapterInit } = require('./_js_base');
const probe = require('../probe_node');

function _computeHash(text) {
  if (!text) return null;
  return crypto.createHash('sha256').update(String(text)).digest('hex').substring(0, 16);
}

function _getContentMaxLen() {
  const val = process.env.MING_CONTENT_MAX_LEN;
  if (val === undefined || val === '') return 2000;
  const v = parseInt(val, 10);
  if (isNaN(v)) return 2000;
  return v === 0 ? 0 : v;
}

function truncate(s, maxLen) {
  if (maxLen === undefined || maxLen === null) maxLen = _getContentMaxLen();
  if (maxLen === 0) return s;
  const str = String(s);
  if (str.length > maxLen) return str.substring(0, maxLen) + '...';
  return str;
}

function chain(original, interceptor) {
  return function(...args) {
    interceptor.apply(this, args);
    if (typeof original === 'function') return original.apply(this, args);
  };
}

function _patch(probe) {
  if (typeof globalThis.openclaw === 'undefined' || !globalThis.openclaw.hooks) return;
  const h = globalThis.openclaw.hooks;

  h.onLLMCall = chain(h.onLLMCall, (ctx) => {
    probe.emit('llm_invoke', {
      layer_agent: { step_id: ctx.stepId || 'unknown', session_id: ctx.sessionId || 'unknown', agent_name: ctx.agentName || 'openclaw' },
      layer_llm: { model: ctx.model || 'unknown', input_tokens: ctx.inputTokens || 0, output_tokens: ctx.outputTokens || 0, latency_ms: ctx.latencyMs || 0, temperature: ctx.temperature, cache_hit: ctx.cacheHit || false },
      layer_network: { target_host: ctx.host || 'unknown', status_code: ctx.statusCode || 0, tcp_connected_ms: ctx.tcpMs || 0, tls_handshake_ms: ctx.tlsMs || 0 },
      _ingest_channel: 'hooks'
    });

    const outputText = ctx.output || ctx.content || (ctx.response && (ctx.response.content || ctx.response.text));
    if (outputText) {
      probe.emit('llm_output', {
        layer_agent: { step_id: ctx.stepId || 'unknown', session_id: ctx.sessionId || 'unknown', agent_name: ctx.agentName || 'openclaw' },
        layer_llm: {
          output_text: truncate(outputText),
          output_text_hash: _computeHash(outputText)
        },
        layer_network: { target_host: ctx.host || 'unknown' },
        _ingest_channel: 'hooks'
      });
    }
  });

  h.onToolCall = chain(h.onToolCall, (ctx) => {
    probe.emit('tool_call', {
      layer_agent: { step_id: ctx.stepId || 'unknown', session_id: ctx.sessionId || 'unknown', agent_name: ctx.agentName || 'openclaw' },
      layer_tool: { tool_name: ctx.toolName || 'unknown', tool_args: ctx.args || {}, tool_result: ctx.result || null, execution_ms: ctx.executionMs || 0 },
      layer_network: { target_host: ctx.host || 'local', status_code: ctx.success ? 200 : 500 },
      _ingest_channel: 'hooks'
    });
  });

  h.onSessionEvent = chain(h.onSessionEvent, (ctx) => {
    const eventType = (ctx.event || ctx.type || '').toLowerCase();
    const stepId = ctx.stepId || 'unknown';
    const sessionId = ctx.sessionId || 'unknown';
    const agentName = ctx.agentName || 'openclaw';

    if (eventType.includes('start') || eventType.includes('begin') || eventType === 'create') {
      probe.emit('agent_step_start', {
        layer_agent: { step_id: stepId, session_id: sessionId, agent_name: agentName, step_status: 'start' },
        layer_network: { target_host: 'local' },
        _ingest_channel: 'hooks'
      });
    } else if (eventType.includes('finish') || eventType.includes('end') || eventType.includes('complete') || eventType === 'close') {
      probe.emit('agent_step_finish', {
        layer_agent: { step_id: stepId, session_id: sessionId, agent_name: agentName, step_status: 'finish' },
        layer_network: { target_host: 'local' },
        _ingest_channel: 'hooks'
      });
    } else {
      probe.emit('agent_step', {
        layer_agent: { step_id: stepId, session_id: sessionId, agent_name: agentName, event: ctx.event || 'unknown' },
        _ingest_channel: 'hooks'
      });
    }
  });

  h.onError = chain(h.onError, (ctx) => {
    probe.emit('error', {
      layer_agent: { step_id: ctx.stepId || 'unknown', session_id: ctx.sessionId || 'unknown', agent_name: ctx.agentName || 'openclaw' },
      error_type: ctx.errorType || 'unknown', error_msg: ctx.errorMessage || '', stack_trace: ctx.stackTrace || '',
      _ingest_channel: 'hooks'
    });
  });
}

module.exports = { initOpenClawProbe: makeAdapterInit('openclaw', _patch) };
