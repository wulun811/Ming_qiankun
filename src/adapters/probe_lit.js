// probe_lit.js —— 0.11.9m 乾坤镜 LIT TypeScript/Node.js 适配层
// 职责：在 LIT Client/Server 事件流中注入乾坤镜探针调用
// 依赖：_js_base.js, probe_node.js（零 npm 依赖）

const crypto = require('crypto');
const { makeAdapterInit, intercept } = require('./_js_base');
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

function _patch(probe) {
  if (typeof globalThis.McpClient !== 'undefined') {
    const C = globalThis.McpClient;
    intercept(C.prototype, 'callTool', 'tool_call',
      (r, lat, args) => ({
        layer_agent: { step_id: 'unknown', session_id: 'unknown', agent_name: 'mcp_client' },
        layer_tool: { tool_name: args[0], tool_args: args[1] || {}, tool_result: JSON.stringify(r).slice(0, 500), execution_ms: lat },
        layer_network: { target_host: 'mcp_server', status_code: 200 }
      }),
      { layer_agent: { step_id: 'unknown', session_id: 'unknown', agent_name: 'mcp_client' } }
    );
    intercept(C.prototype, 'readResource', 'memory_retrieve',
      (r, lat, args) => ({
        layer_agent: { step_id: 'unknown', session_id: 'unknown', agent_name: 'mcp_client' },
        layer_memory: { memory_type: 'mcp_resource', query: String(args[0]), results_count: 1, latency_ms: lat }
      }),
      { layer_agent: { step_id: 'unknown', session_id: 'unknown', agent_name: 'mcp_client' } }
    );
  }

  if (typeof globalThis.McpServer !== 'undefined') {
    const S = globalThis.McpServer;
    const origHandle = S.prototype.handleRequest;
    S.prototype.handleRequest = async function(...args) {
      const start = Date.now();
      const result = await origHandle.apply(this, args);
      const latencyMs = Date.now() - start;

      // 发射 llm_invoke 事件
      probe.emit('llm_invoke', {
        layer_agent: { step_id: 'unknown', session_id: 'unknown', agent_name: 'mcp_server' },
        layer_llm: { model: 'unknown', input_tokens: 0, output_tokens: 0, latency_ms: latencyMs, cache_hit: false },
        layer_network: { target_host: 'mcp_client', status_code: 200 }
      });

      // 发射 llm_output 事件（如果有输出）
      const outputText = result && (result.content || result.text || (typeof result === 'string' ? result : null));
      if (outputText) {
        probe.emit('llm_output', {
          layer_agent: { step_id: 'unknown', session_id: 'unknown', agent_name: 'mcp_server' },
          layer_llm: {
            output_text: truncate(outputText),
            output_text_hash: _computeHash(outputText)
          },
          layer_network: { target_host: 'mcp_client' }
        });
      }

      return result;
    };
  }
}

module.exports = { initLitProbe: makeAdapterInit('lit', _patch) };
