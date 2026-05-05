// probe_tusunsun.js —— 0.11.9m 乾坤镜 土行孙 适配层
// 职责：订阅土行孙全量运行时事件，转发至乾坤镜热轨
// 依赖：_js_base.js, probe_node.js（零 npm 依赖）
// 协议版本：0.11.9m-draft
// 原则：零 monkey-patch，纯事件订阅，静默降级

const crypto = require('crypto');
const { makeAdapterInit } = require('./_js_base');
const probe = require('../probe_node');

// ─── 工具函数 ───

function _computeHash(text) {
  if (!text) return null;
  return crypto.createHash('sha256').update(String(text)).digest('hex').substring(0, 16);
}

function _truncate(s, max) {
  if (!s) return s;
  const str = String(s);
  // 支持环境变量覆盖：MING_CONTENT_MAX_LEN=0 表示不截取（审计模式）
  const envMax = process.env.MING_CONTENT_MAX_LEN;
  if (envMax !== undefined) {
    const configured = parseInt(envMax, 10);
    if (configured === 0) return str; // 审计模式：不截取
    if (configured > 0) max = configured;
  }
  return str.length > max ? str.slice(0, max) + '...' : str;
}

function _parseBaseUrl(baseUrl) {
  try {
    return new URL(baseUrl).hostname;
  } catch {
    return 'unknown';
  }
}

// ─── 探针初始化 ───

function initTusunsunProbe(core, config) {
  const system = (config && config.system) || 'tusunsun';
  const mode = (config && config.mode) || 'white';

  // 初始化探针
  probe.init(system, mode);

  // ─── 事件配对缓存 ───
  // 土行孙将 LLM 调用拆分为 call + response，需要配对后发射
  const pendingCalls = new Map();
  const pendingTools = new Map();

  // ─── LLM 调用事件 ───

  core.on('llm.call', (data) => {
    if (!data) return;
    // 缓存请求数据，等待 response 配对
    pendingCalls.set(data.timestamp || Date.now(), data);
  });

  core.on('llm.response', (data) => {
    if (!data) return;

    // 查找配对的 call 数据
    const callData = pendingCalls.get(data.timestamp) || {};
    pendingCalls.delete(data.timestamp);

    // 发射 llm_invoke 事件
    probe.emit('llm_invoke', {
      layer_agent: {
        step_id: data.stepId || callData.stepId || 'unknown',
        session_id: data.sessionId || callData.sessionId || 'unknown',
        agent_name: 'tusunsun',
      },
      layer_llm: {
        model: data.model || callData.model || 'unknown',
        input_tokens: data.inputTokens || callData.totalInputTokens || 0,
        output_tokens: data.outputTokens || 0,
        latency_ms: data.latencyMs || 0,
        cache_hit: data.cacheHit || false,
        finish_reason: data.finishReason || null,
      },
      layer_network: {
        target_host: data.baseUrl ? _parseBaseUrl(data.baseUrl) : 'unknown',
        status_code: data.statusCode || 200,
      },
      _ingest_channel: 'tusunsun',
    });

    // 发射 llm_output 事件（用于重复检测）
    if (data.content) {
      probe.emit('llm_output', {
        layer_agent: {
          step_id: data.stepId || callData.stepId || 'unknown',
          session_id: data.sessionId || callData.sessionId || 'unknown',
          agent_name: 'tusunsun',
        },
        layer_llm: {
          output_text: _truncate(data.content, 2000),
          output_text_hash: _computeHash(data.content),
        },
        layer_network: {
          target_host: data.baseUrl ? _parseBaseUrl(data.baseUrl) : 'unknown',
        },
        _ingest_channel: 'tusunsun',
      });
    }

    // 发射 agent_step_finish（LLM 响应完成）
    probe.emit('agent_step_finish', {
      layer_agent: {
        step_id: data.stepId || callData.stepId || 'unknown',
        session_id: data.sessionId || callData.sessionId || 'unknown',
        agent_name: 'tusunsun',
        step_status: 'finish',
      },
      layer_network: {
        target_host: 'local',
      },
      _ingest_channel: 'tusunsun',
    });
  });

  // ─── 工具执行事件 ───

  core.on('tool.execute', (data) => {
    if (!data) return;
    // 缓存执行数据，等待 result 配对
    pendingTools.set(data.timestamp || Date.now(), data);
  });

  core.on('tool.result', (data) => {
    if (!data) return;

    // 查找配对的 execute 数据
    const execData = pendingTools.get(data.timestamp) || {};
    pendingTools.delete(data.timestamp);

    // 发射 tool_call 事件
    probe.emit('tool_call', {
      layer_agent: {
        step_id: data.stepId || execData.stepId || 'unknown',
        session_id: data.sessionId || execData.sessionId || 'unknown',
        agent_name: 'tusunsun',
      },
      layer_tool: {
        tool_name: data.toolName || execData.toolName || 'unknown',
        tool_args: data.toolArgs || execData.toolArgs || {},
        tool_args_hash: data.toolArgsHash || execData.toolArgsHash || null,
        tool_input_hash: data.toolArgsHash || execData.toolArgsHash || null,
        tool_result: _truncate(data.toolResult || '', 500),
        execution_ms: data.executionMs || 0,
        tool_status: data.success !== false ? 'success' : 'fail',
        tool_ok: data.ok !== undefined ? data.ok : null,
      },
      layer_network: {
        target_host: 'local',
        status_code: data.success !== false ? 200 : 500,
      },
      _ingest_channel: 'tusunsun',
    });

    // 发射 agent_step_finish（工具执行完成）
    probe.emit('agent_step_finish', {
      layer_agent: {
        step_id: data.stepId || execData.stepId || 'unknown',
        session_id: data.sessionId || execData.sessionId || 'unknown',
        agent_name: 'tusunsun',
        step_status: 'finish',
      },
      layer_network: {
        target_host: 'local',
      },
      _ingest_channel: 'tusunsun',
    });
  });

  // ─── Agent 步骤事件 ───

  core.on('agent.step', (data) => {
    if (!data) return;

    // 发射 agent_step_start（步骤开始）
    probe.emit('agent_step_start', {
      layer_agent: {
        step_id: data.stepId || 'unknown',
        session_id: data.sessionId || 'unknown',
        agent_name: data.agentName || 'tusunsun',
        step_status: 'start',
        turn: data.turn || 0,
        max_turns: data.maxTurns || 0,
        action: data.action || 'unknown',
      },
      layer_network: {
        target_host: 'local',
      },
      _ingest_channel: 'tusunsun',
    });
  });

  // ─── 记忆事件 ───

  core.on('memory.store', (data) => {
    if (!data) return;

    probe.emit('memory_store', {
      layer_agent: {
        session_id: data.sessionId || 'unknown',
        agent_name: 'tusunsun',
      },
      layer_memory: {
        chunk_id: data.chunkId || '',
        content: _truncate(data.content, 500),
        content_length: data.contentLength || 0,
        metadata: data.metadata || {},
      },
      _ingest_channel: 'tusunsun',
    });
  });

  core.on('memory.retrieve', (data) => {
    if (!data) return;

    probe.emit('memory_retrieve', {
      layer_agent: {
        session_id: data.sessionId || 'unknown',
        agent_name: 'tusunsun',
      },
      layer_memory: {
        query: _truncate(data.query, 200),
        query_hash: data.queryHash || '',
        results_count: data.resultsCount || 0,
        avg_relevance: data.avgRelevance || 0,
        max_relevance: data.maxRelevance || 0,
        min_relevance: data.minRelevance || 0,
        latency_ms: data.latencyMs || 0,
        embedding_model: data.model || '',
      },
      _ingest_channel: 'tusunsun',
    });
  });

  // ─── 错误事件 ───

  core.on('error', (data) => {
    if (!data) return;

    probe.emit('error', {
      layer_agent: {
        step_id: data.stepId || 'unknown',
        session_id: data.sessionId || 'unknown',
        agent_name: data.agentName || 'tusunsun',
      },
      error_type: data.errorType || 'Error',
      error_msg: data.errorMessage || '',
      stack_trace: _truncate(data.stackTrace, 1000),
      _ingest_channel: 'tusunsun',
    });
  });

  // ─── v0.8.3 Phase 3: 新增 7 种事件类型 ───

  core.on('plugin.init_failed', (data) => {
    if (!data) return;
    probe.emit('plugin.init_failed', {
      layer_system: {
        plugin_name: data.pluginName || 'unknown',
        error: data.error || '',
        init_time_ms: data.initTimeMs || 0,
      },
      _ingest_channel: 'tusunsun',
    });
  });

  core.on('circuit.state_change', (data) => {
    if (!data) return;
    probe.emit('circuit.state_change', {
      layer_system: {
        tool: data.tool || 'unknown',
        from_state: data.from || 'CLOSED',
        to_state: data.to || 'CLOSED',
      },
      layer_agent: {
        session_id: data.sessionId || 'unknown',
        agent_name: 'tusunsun',
      },
      _ingest_channel: 'tusunsun',
    });
  });

  core.on('db.flush_error', (data) => {
    if (!data) return;
    probe.emit('db.flush_error', {
      layer_system: {
        db_mode: data.mode || 'sqlite',
        error: data.error || '',
        queue_size: data.queueSize || 0,
      },
      _ingest_channel: 'tusunsun',
    });
  });

  core.on('db.mode_degraded', (data) => {
    if (!data) return;
    probe.emit('db.mode_degraded', {
      layer_system: {
        reason: data.reason || '',
        mode: data.mode || 'jsonl',
      },
      _ingest_channel: 'tusunsun',
    });
  });

  core.on('memory.readonly_entered', (data) => {
    if (!data) return;
    probe.emit('memory.readonly_entered', {
      layer_system: {
        reason: data.reason || '',
        error_count: data.errorCount || 0,
      },
      layer_memory: {
        integrity: 'broken',
      },
      _ingest_channel: 'tusunsun',
    });
  });

  core.on('push.action_failed', (data) => {
    if (!data) return;
    probe.emit('push.action_failed', {
      layer_system: {
        fault_id: data.faultId || 'unknown',
        action: data.action || 'unknown',
        error: data.error || '',
      },
      layer_agent: {
        agent_name: 'tusunsun',
      },
      _ingest_channel: 'tusunsun',
    });
  });

  core.on('http.error', (data) => {
    if (!data) return;
    probe.emit('http.error', {
      layer_network: {
        method: data.method || 'GET',
        url: data.url || '/',
        status_code: data.statusCode || 500,
        error: data.error || '',
      },
      layer_system: {
        source_plugin: data.sourcePlugin || 'unknown',
      },
      _ingest_channel: 'tusunsun',
    });
  });

  // ─── 生命周期事件 ───

  core.on('ready', (data) => {
    probe.emit('__register__', {
      pid: process.pid,
      mode: mode,
      schema_version: '0.3.0',
      registered_at: Date.now() / 1000,
      tusunsun_version: (data && data.version) || 'unknown',
      plugins: (data && data.plugins) || [],
      services: (data && data.services) || [],
      capabilities: (data && data.capabilities) || [],
    });
  });

  core.on('gateway.listen', (data) => {
    if (!data) return;
    probe.emit('gateway_listen', {
      layer_agent: {
        agent_name: 'tusunsun',
      },
      layer_network: {
        target_host: data.host || '0.0.0.0',
        port: data.port || 0,
        protocols: data.protocols || [],
      },
      _ingest_channel: 'tusunsun',
    });
  });

  core.on('config.reloaded', (data) => {
    if (!data) return;
    probe.emit('config_reload', {
      layer_agent: {
        agent_name: 'tusunsun',
      },
      payload: {
        changed_keys: data.changedKeys || [],
      },
      _ingest_channel: 'tusunsun',
    });
  });

  // ─── 定时心跳 ───
  // 每 30 秒发射 touch 事件，保持探针活跃状态
  const heartbeat = setInterval(() => {
    probe.emit('__touch__', { pid: process.pid });
  }, 30000);

  // 允许进程退出时不等待 heartbeat
  if (heartbeat.unref) {
    heartbeat.unref();
  }

  // ─── 清理逻辑 ───
  // 当土行孙关闭时，清理缓存和定时器
  core.on('shutdown', () => {
    clearInterval(heartbeat);
    pendingCalls.clear();
    pendingTools.clear();
  });

  return probe;
}

module.exports = { initTusunsunProbe };
