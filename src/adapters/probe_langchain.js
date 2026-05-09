// probe_langchain.js —— 0.11.9m 乾坤镜 LangChain.js 适配层
// 职责：在 LangChain.js LLM 调用和 Tool 执行中注入乾坤镜探针调用
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
  if (typeof globalThis.langchain === 'undefined') return;
  const lc = globalThis.langchain;

  if (lc.BaseLanguageModel) {
    const origInvoke = lc.BaseLanguageModel.prototype.invoke;
    lc.BaseLanguageModel.prototype.invoke = async function(...args) {
      const start = Date.now();
      const result = await origInvoke.apply(this, args);
      const latencyMs = Date.now() - start;

      // 1. 发射 llm_invoke 事件
      probe.emit('llm_invoke', {
        layer_agent: { step_id: 'invoke', session_id: 'unknown', agent_name: 'langchain-js' },
        layer_llm: { model: this._model || 'unknown', input_tokens: 0, output_tokens: 0, latency_ms: latencyMs, cache_hit: false },
        layer_network: { target_host: 'llm_api', status_code: 200 }
      });

      // 2. 发射 llm_output 事件
      const outputText = result && (result.content || result.text || (typeof result === 'string' ? result : null));
      if (outputText) {
        probe.emit('llm_output', {
          layer_agent: { step_id: 'invoke', session_id: 'unknown', agent_name: 'langchain-js' },
          layer_llm: {
            output_text: truncate(outputText),
            output_text_hash: _computeHash(outputText)
          },
          layer_network: { target_host: 'llm_api' }
        });
      }

      return result;
    };
  }

  if (lc.Tool) {
    intercept(lc.Tool.prototype, 'invoke', 'tool_call',
      (r, lat, args) => ({
        layer_agent: { step_id: 'tool', session_id: 'unknown', agent_name: 'langchain-js' },
        layer_tool: { tool_name: this.name || 'unknown', tool_args: args[0], tool_result: truncate(JSON.stringify(r)), execution_ms: lat },
        layer_network: { target_host: 'local', status_code: 200 }
      }),
      { layer_agent: { step_id: 'tool', session_id: 'unknown', agent_name: 'langchain-js' } }
    );
  }

  // 3. Chain 级别的 agent_step_start/finish
  if (lc.Chain) {
    const origCall = lc.Chain.prototype.call;
    lc.Chain.prototype.call = async function(...args) {
      probe.emit('agent_step_start', {
        layer_agent: { step_id: this.name || 'chain', session_id: 'unknown', agent_name: 'langchain-js', step_status: 'start' },
        layer_network: { target_host: 'local' }
      });
      try {
        const result = await origCall.apply(this, args);
        probe.emit('agent_step_finish', {
          layer_agent: { step_id: this.name || 'chain', session_id: 'unknown', agent_name: 'langchain-js', step_status: 'finish' },
          layer_network: { target_host: 'local' }
        });
        return result;
      } catch (e) {
        probe.emit('agent_step_finish', {
          layer_agent: { step_id: this.name || 'chain', session_id: 'unknown', agent_name: 'langchain-js', step_status: 'finish' },
          layer_network: { target_host: 'local' }
        });
        throw e;
      }
    };
  }
}

module.exports = { initLangChainJsProbe: makeAdapterInit('langchain-js', _patch) };
