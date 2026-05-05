// probe-runtime.js —— OpenClaw 乾坤镜探针运行时
// 职责：订阅 OpenClaw 全量运行时事件，转发至乾坤镜热轨
// 原则：零入侵，纯事件订阅，静默降级

import { resolveAll } from './sdk-resolver.js';
import { existsSync, mkdirSync, appendFileSync, readFileSync, readdirSync, statSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { homedir, freemem } from 'node:os';
import { createHash } from 'node:crypto';

const __dirname = dirname(fileURLToPath(import.meta.url));

let _sdk = null;

// ─── 工具函数 ───

function _computeHash(text) {
  if (!text) return null;
  return createHash('sha256').update(String(text)).digest('hex').substring(0, 16);
}

function _truncate(s, max) {
  if (!s) return s;
  const str = String(s);
  const envMax = process.env.MING_CONTENT_MAX_LEN;
  if (envMax !== undefined) {
    const configured = parseInt(envMax, 10);
    if (configured === 0) return str;
    if (configured > 0) max = configured;
  }
  return str.length > max ? str.slice(0, max) + '...' : str;
}

function _resolveHotPath(configured) {
  const path = configured || process.env.MING_HOT_PATH || '~/.ming/hot';
  return path.replace(/^~/, homedir());
}

function _ensureHotDir(hotPath) {
  if (!existsSync(hotPath)) {
    try {
      mkdirSync(hotPath, { recursive: true });
    } catch (err) {
      console.warn(`[mingjing-probe] Failed to create hot dir: ${err.message}`);
      return false;
    }
  }
  return true;
}

function _hotFile(hotPath) {
  const ts = new Date().toISOString().slice(0, 13).replace(/[-:]/g, '');
  return join(hotPath, `mingjing-${ts}.jsonl`);
}

const _pausedFile = join(homedir(), '.ming', '.paused', 'openclaw');

function _isPaused() {
  try { return existsSync(_pausedFile); } catch { return false; }
}

function _collectPlatformMetrics() {
  const m = { platform_unreliable: false };

  try {
    const status = readFileSync('/proc/self/status', 'utf8');
    const vmSize = status.match(/^VmSize:\s+(\d+)/m);
    if (vmSize) m.vm_size_kb = parseInt(vmSize[1], 10);
    const threads = status.match(/^Threads:\s+(\d+)/m);
    if (threads) m.threads = parseInt(threads[1], 10);
  } catch {
    m.platform_unreliable = true;
  }

  try {
    m.fd_count = readdirSync('/proc/self/fd').length;
  } catch {
    m.fd_count = 0;
    m.platform_unreliable = true;
  }

  m.mem_free_mb = Math.round(freemem() / (1024 * 1024));

  try {
    const io = readFileSync('/proc/self/io', 'utf8');
    const rb = io.match(/^read_bytes:\s+(\d+)/m);
    if (rb) m.io_read_bytes = parseInt(rb[1], 10);
    const wb = io.match(/^write_bytes:\s+(\d+)/m);
    if (wb) m.io_write_bytes = parseInt(wb[1], 10);
  } catch {
    m.io_read_bytes = 0;
    m.io_write_bytes = 0;
  }

  return m;
}

// ─── 探针运行时 ───

class MingjingProbeRuntime {
  constructor(config = {}) {
    this.hotPath = _resolveHotPath(config.hotPath);
    this.heartbeatIntervalMs = config.heartbeatIntervalMs || 30000;
    this.bufferSize = config.bufferSize || 100;
    this.maxBufferSize = this.bufferSize * 5;
    this.buffer = [];
    this.heartbeatTimer = null;
    this.unsubscribeFns = [];
    this.running = false;
    this.runState = new Map();
    this._pendingTimeouts = new Set();
    this.toolStartTimes = new Map();
    this.emitSuccessCount = 0;
    this.emitDropCount = 0;
    this.lastErrors = [];
    this.healthWindowStart = Date.now() / 1000;
  }

  // ─── 热轨写入 ───

  _writeToHotRail(event) {
    const line = JSON.stringify(event) + '\n';
    if (this.buffer.length >= this.maxBufferSize) {
      this.buffer.shift();
      this.emitDropCount++;
      console.warn('[mingjing-probe] Buffer full, dropping oldest event');
      return;
    }
    this.buffer.push(line);

    if (this.buffer.length >= this.bufferSize) {
      this._flush();
    }
  }

  _flush() {
    if (this.buffer.length === 0) return;

    if (!_ensureHotDir(this.hotPath)) return;

    const file = _hotFile(this.hotPath);
    try {
      appendFileSync(file, this.buffer.join(''), 'utf8');
      this.emitSuccessCount += this.buffer.length;
      this.buffer = [];
    } catch (err) {
      this.emitDropCount += this.buffer.length;
      this.lastErrors.push(`${new Date().toISOString()}: ${err.message}`);
      if (this.lastErrors.length > 10) this.lastErrors.shift();
      console.warn(`[mingjing-probe] Hot rail write failed: ${err.message}`);
    }
  }

  _emit(eventType, data) {
    if (!this.running) return;
    if (_isPaused()) return;
    let payload;
    let meta = {};
    const ts = Date.now() / 1000;
    if ('payload' in data) {
      payload = data.payload || {};
      if (data._ingest_channel) meta._ingest_channel = data._ingest_channel;
      if (data.pid !== undefined) meta.pid = data.pid;
    } else {
      const { _ingest_channel, pid, ...rest } = data;
      payload = rest;
      if (_ingest_channel) meta._ingest_channel = _ingest_channel;
      if (pid !== undefined) meta.pid = pid;
    }
    payload.timestamp = ts;
    payload.system = 'openclaw';
    const event = {
      event_type: eventType,
      system: 'openclaw',
      timestamp: ts,
      payload,
      ...meta,
    };
    this._writeToHotRail(event);
  }

  // ─── 事件订阅 ───

  _subscribeAgentEvents() {
    const unsubscribe = _sdk.onAgentEvent((event) => {
      const { runId, sessionKey, seq, stream, data, ts } = event;

      switch (stream) {
        case 'item':
          this._handleAgentItem(runId, sessionKey, seq, data, ts);
          break;
        case 'plan':
          this._handleAgentPlan(runId, sessionKey, seq, data, ts);
          break;
        case 'approval':
          this._handleAgentApproval(runId, sessionKey, seq, data, ts);
          break;
        case 'command_output':
          this._handleCommandOutput(runId, sessionKey, seq, data, ts);
          break;
        case 'patch':
          this._handleAgentPatch(runId, sessionKey, seq, data, ts);
          break;
        case 'lifecycle':
          this._handleLifecycle(runId, sessionKey, data, ts);
          break;
        case 'tool':
          this._handleToolStream(runId, sessionKey, seq, data, ts);
          break;
        case 'assistant':
          if (data?.text) {
            this._emit('llm_output', {
              layer_agent: {
                step_id: `${runId}:${seq}`,
                session_id: sessionKey || 'unknown',
                agent_name: 'openclaw',
                role: 'agent',
              },
              layer_llm: {
                output_text: _truncate(data.text, 2000),
                output_text_hash: _computeHash(data.text),
              },
              _ingest_channel: 'openclaw',
            });
          }
          this._emit('agent_event', {
            layer_agent: {
              run_id: runId,
              session_id: sessionKey,
              agent_name: 'openclaw',
              seq,
              stream,
              decision_summary: null,
              role: 'agent',
            },
            raw: data,
            _ingest_channel: 'openclaw',
          });
          break;
        default:
          const decisionSummary = data?.summary || data?.decision || data?.reasoning || null;
          const role = data?.role || data?.agent_role || data?.actor || 'agent';
          this._emit('agent_event', {
            layer_agent: {
              run_id: runId,
              session_id: sessionKey,
              agent_name: 'openclaw',
              seq,
              stream,
              decision_summary: decisionSummary ? _truncate(decisionSummary, 500) : null,
              role: role,
            },
            raw: data,
            _ingest_channel: 'openclaw',
          });
      }
    });
    this.unsubscribeFns.push(unsubscribe);
  }

  _handleAgentItem(runId, sessionKey, seq, data, ts) {
    // LLM 调用项
    if (data?.type === 'tool_use' || data?.type === 'text') {
      const isToolCall = data?.type === 'tool_use';

      if (isToolCall) {
        const toolInputHash = _computeHash(JSON.stringify(data?.input || {}));
        this.toolStartTimes.set(`${runId}:${seq}`, Date.now());
        this._emit('tool_call', {
          layer_agent: {
            step_id: `${runId}:${seq}`,
            session_id: sessionKey || 'unknown',
            agent_name: 'openclaw',
            decision_summary: data?.summary || data?.reasoning || null,
            role: data?.role || data?.actor || 'agent',
          },
          layer_tool: {
            tool_name: data?.name || 'unknown',
            tool_args: data?.input || {},
            tool_input_hash: toolInputHash,
            tool_status: 'start',
          },
          _ingest_channel: 'openclaw',
        });
      } else if (data?.type === 'text') {
        const content = data?.text || '';
        this._emit('llm_output', {
          layer_agent: {
            step_id: `${runId}:${seq}`,
            session_id: sessionKey || 'unknown',
            agent_name: 'openclaw',
          },
          layer_llm: {
            output_text: _truncate(content, 2000),
            output_text_hash: _computeHash(content),
          },
          _ingest_channel: 'openclaw',
        });
      }
    }

    // Agent 步骤开始
    this._emit('agent_step_start', {
      layer_agent: {
        step_id: `${runId}:${seq}`,
        session_id: sessionKey || 'unknown',
        agent_name: 'openclaw',
        step_status: 'start',
        seq,
        decision_summary: data?.summary || data?.reasoning || null,
        role: data?.role || data?.actor || 'agent',
      },
      _ingest_channel: 'openclaw',
    });
  }

  _handleAgentPlan(runId, sessionKey, seq, data, ts) {
    this._emit('agent_step_start', {
      layer_agent: {
        step_id: `${runId}:${seq}`,
        session_id: sessionKey || 'unknown',
        agent_name: 'openclaw',
        step_status: 'plan',
        turn: seq,
        action: 'plan',
        decision_summary: data?.summary || null,
        role: data?.role || 'agent',
      },
      layer_tool: {
        tool_name: 'plan',
        tool_args: { plan: _truncate(JSON.stringify(data), 500) },
      },
      _ingest_channel: 'openclaw',
    });
  }

  _handleAgentApproval(runId, sessionKey, seq, data, ts) {
    const toolArgs = data?.input || data?.args || {};
    this._emit('tool_call', {
      layer_agent: {
        step_id: `${runId}:${seq}`,
        session_id: sessionKey || 'unknown',
        agent_name: 'openclaw',
        decision_summary: data?.summary || null,
        role: data?.role || 'agent',
      },
      layer_tool: {
        tool_name: data?.tool || data?.name || 'unknown',
        tool_args: toolArgs,
        tool_input_hash: _computeHash(JSON.stringify(toolArgs)),
        tool_status: data?.status || 'pending_approval',
      },
      _ingest_channel: 'openclaw',
    });
  }

  _handleCommandOutput(runId, sessionKey, seq, data, ts) {
    const toolCallId = data?.toolCallId || `${runId}:${seq}`;
    const startTime = this.toolStartTimes.get(toolCallId);
    const executionMs = startTime ? Date.now() - startTime : null;
    if (startTime) this.toolStartTimes.delete(toolCallId);
    const command = data?.name || toolCallId;
    const hash = _computeHash(command);
    const exitCode = data?.exitCode;
    const status = data?.status || (exitCode === 0 ? 'success' : exitCode == null ? 'success' : 'fail');
    const resultText = _truncate(data?.output || data?.content || '', 500);

    this._emit('tool_call', {
      layer_agent: {
        step_id: `${runId}:${seq}`,
        session_id: sessionKey || 'unknown',
        agent_name: 'openclaw',
        decision_summary: data?.summary || null,
        role: 'agent',
      },
      layer_tool: {
        tool_name: data?.name || 'bash',
        tool_args: { command: _truncate(command, 200) },
        tool_input_hash: hash,
        tool_result: resultText,
        tool_status: status === 'failed' ? 'fail' : status === 'completed' || status === 'success' ? 'success' : 'fail',
        execution_ms: executionMs,
      },
      _ingest_channel: 'openclaw',
    });
    if (status === 'failed' || (exitCode !== undefined && exitCode !== 0)) {
      this._emit('error', {
        layer_agent: {
          session_id: sessionKey || 'unknown',
          agent_name: 'openclaw',
        },
        error_type: 'tool_failure',
        error_msg: _truncate(`Exit code ${exitCode}: ${resultText}`, 500),
        _ingest_channel: 'openclaw',
      });
    }
  }

  _handleAgentPatch(runId, sessionKey, seq, data, ts) {
    const stepKey = `${runId}:${seq}`;
    const startTime = this.toolStartTimes.get(stepKey);
    const executionMs = startTime ? Date.now() - startTime : null;
    if (startTime) this.toolStartTimes.delete(stepKey);

    this._emit('agent_step_finish', {
      layer_agent: {
        step_id: stepKey,
        session_id: sessionKey || 'unknown',
        agent_name: 'openclaw',
        step_status: 'finish',
        decision_summary: data?.summary || null,
        role: data?.role || 'agent',
      },
      layer_tool: {
        tool_name: 'patch',
        tool_result: _truncate(JSON.stringify(data), 500),
        execution_ms: executionMs,
      },
      _ingest_channel: 'openclaw',
    });
  }

  _handleToolStream(runId, sessionKey, seq, data, ts) {
    const toolName = data?.name || 'unknown';
    const toolCallId = data?.toolCallId || `${runId}:${seq}`;
    const meta = data?.meta || '';
    const phase = data?.phase || 'result';

    if (phase === 'start') {
      this.toolStartTimes.set(toolCallId, Date.now());
      this._emit('tool_call', {
        layer_agent: {
          step_id: `${runId}:${seq}`,
          session_id: sessionKey || 'unknown',
          agent_name: 'openclaw',
          decision_summary: meta ? _truncate(meta, 500) : null,
          role: 'agent',
        },
        layer_tool: {
          tool_name: toolName,
          tool_args: { tool_call_id: toolCallId },
          tool_input_hash: _computeHash(toolCallId + meta),
          tool_status: 'start',
        },
        _ingest_channel: 'openclaw',
      });
    } else {
      const startTime = this.toolStartTimes.get(toolCallId);
      const executionMs = startTime ? Date.now() - startTime : null;
      if (startTime) this.toolStartTimes.delete(toolCallId);
      const resultText = data?.result?.content?.map(c => c.text || '').join('\n') || '';
      const toolInputHash = _computeHash(toolCallId + meta);
      this._emit('tool_call', {
        layer_agent: {
          step_id: `${runId}:${seq}`,
          session_id: sessionKey || 'unknown',
          agent_name: 'openclaw',
          decision_summary: meta ? _truncate(meta, 500) : null,
          role: 'agent',
        },
        layer_tool: {
          tool_name: toolName,
          tool_args: { tool_call_id: toolCallId },
          tool_input_hash: toolInputHash,
          tool_result: _truncate(resultText, 2000),
          tool_status: data?.isError ? 'fail' : 'success',
          execution_ms: executionMs,
        },
        _ingest_channel: 'openclaw',
      });
      if (data?.isError) {
        this._emit('error', {
          layer_agent: {
            session_id: sessionKey || 'unknown',
            agent_name: 'openclaw',
          },
          error_type: 'tool_failure',
          error_msg: _truncate(`Tool ${toolName} failed: ${resultText}`, 500),
          _ingest_channel: 'openclaw',
        });
      }
    }
  }

  _handleLifecycle(runId, sessionKey, data, ts) {
    if (data?.phase === 'start') {
      // P0-11: runState size limit
      if (this.runState.size >= 1000) {
        const oldestKey = this.runState.keys().next().value;
        this.runState.delete(oldestKey);
      }
      this.runState.set(runId, { sessionKey, startedAt: data.startedAt });
    } else if (data?.phase === 'end') {
      const state = this.runState.get(runId);
      const startedAt = state?.startedAt || data.startedAt;
      const sKey = state?.sessionKey || sessionKey;
      const latencyMs = data.endedAt - startedAt;

      this._emit('agent_step_finish', {
        layer_agent: {
          step_id: runId,
          session_id: sKey || 'unknown',
          agent_name: 'openclaw',
          step_status: 'finish',
          role: 'agent',
          latency_ms: latencyMs,
        },
        _ingest_channel: 'openclaw',
      });

      // P0-12: Track timeout for cleanup on stop()
      const timeoutId = setTimeout(() => {
        this._pendingTimeouts.delete(timeoutId);
        const sessionData = this._findSessionUsage(sKey, startedAt, data.endedAt);

        this._emit('llm_invoke', {
          payload: {
            layer_agent: {
              run_id: runId,
              session_id: sKey || 'unknown',
              agent_name: 'openclaw',
            },
            layer_llm: {
              model: sessionData?.model || null,
              provider: sessionData?.provider || null,
              input_tokens: sessionData?.input_tokens || null,
              output_tokens: sessionData?.output_tokens || null,
              total_tokens: sessionData?.total_tokens || null,
              latency_ms: latencyMs,
              finish_reason: sessionData?.finish_reason || null,
            },
          },
          _ingest_channel: 'openclaw',
        });

        this.runState.delete(runId);
      }, 500);
      this._pendingTimeouts.add(timeoutId);
    }
  }

  _findSessionUsage(sessionKey, startedAt, endedAt) {
    try {
      const sessionsDir = join(
        homedir(),
        '.openclaw',
        'agents',
        'main',
        'sessions'
      );
      if (!existsSync(sessionsDir)) return null;

      let sessionFile = null;

      // 优先通过 sessions.json 精确匹配
      if (sessionKey) {
        const sessionsJsonPath = join(sessionsDir, 'sessions.json');
        if (existsSync(sessionsJsonPath)) {
          const sessions = JSON.parse(readFileSync(sessionsJsonPath, 'utf8'));
          const sessionEntry = sessions[sessionKey];
          if (sessionEntry?.sessionFile) {
            sessionFile = sessionEntry.sessionFile;
          }
        }
      }

      // 如果没有找到，扫描所有 session 文件
      if (!sessionFile) {
        const files = readdirSync(sessionsDir)
          .filter(f => f.endsWith('.jsonl') && !f.includes('.trajectory.'))
          .map(f => ({
            name: f,
            mtime: statSync(join(sessionsDir, f)).mtimeMs,
          }))
          .sort((a, b) => b.mtime - a.mtime);

        if (files.length === 0) return null;
        sessionFile = join(sessionsDir, files[0].name);
      }

      const content = readFileSync(sessionFile, 'utf8');
      const lines = content.trim().split('\n');

      // 从后往前找第一条 stopReason 不是 toolUse 的 assistant message
      for (let i = lines.length - 1; i >= 0; i--) {
        const obj = JSON.parse(lines[i]);
        if (
          obj.type === 'message' &&
          obj.message?.role === 'assistant' &&
          obj.message?.usage
        ) {
          const m = obj.message;
          if (m.stopReason !== 'toolUse') {
            return {
              model: m.model || null,
              provider: m.provider || null,
              input_tokens: m.usage.input || null,
              output_tokens: m.usage.output || null,
              total_tokens: m.usage.totalTokens || null,
              finish_reason: m.stopReason || m.finishReason || null,
            };
          }
        }
      }
    } catch (err) {
      console.warn(
        `[mingjing-probe] Session usage lookup failed: ${err.message}`
      );
    }
    return null;
  }

  _subscribeDiagnosticEvents() {
    const unsubscribe = _sdk.onDiagnosticEvent((event) => {
      if (!event) return;
      const eventType = event?.type || event?.error_type || 'diagnostic';
      this._emit(eventType, {
        layer_agent: {
          session_id: event?.sessionKey || 'unknown',
          agent_name: 'openclaw',
        },
        ...event,
        _ingest_channel: 'openclaw',
      });
    });
    this.unsubscribeFns.push(unsubscribe);
  }

  _subscribeSessionEvents() {
    const unsubscribe = _sdk.onSessionTranscriptUpdate((event) => {
      this._emit('session_event', {
        layer_agent: {
          session_id: event?.sessionKey || 'unknown',
          agent_name: 'openclaw',
        },
        payload: {
          update_type: event?.type || 'transcript_update',
          content_length: event?.content?.length || 0,
        },
        _ingest_channel: 'openclaw',
      });
    });
    this.unsubscribeFns.push(unsubscribe);
  }

  // ─── 心跳 ───

  _beat() {
    if (_isPaused()) return;
    const memUsage = process.memoryUsage();
    const pid = process.pid;
    const vmRssKb = Math.round(memUsage.rss / 1024);
    const pm = _collectPlatformMetrics();
    this._emit('__touch__', { payload: { pid, vm_rss_kb: vmRssKb }, pid });
    this._emit('platform_snapshot', {
      payload: {
        vm_rss_kb: vmRssKb,
        vm_heap_used_kb: Math.round(memUsage.heapUsed / 1024),
        vm_heap_total_kb: Math.round(memUsage.heapTotal / 1024),
        pid: pid,
        vm_size_kb: pm.vm_size_kb || 0,
        fd_count: pm.fd_count || 0,
        threads: pm.threads || 0,
        mem_free_mb: pm.mem_free_mb || 0,
        io_read_bytes: pm.io_read_bytes || 0,
        io_write_bytes: pm.io_write_bytes || 0,
        platform_unreliable: pm.platform_unreliable,
      },
      pid: pid,
    });
    this._emit('__health__', {
      payload: {
        emit_success_count_1m: this.emitSuccessCount,
        emit_drop_count_1m: this.emitDropCount,
        mem_free_mb: pm.mem_free_mb || 0,
        last_errors: this.lastErrors.slice(-10),
        buffer_queue_size: this.buffer.length,
        _incomplete: this.lastErrors.length > 0 ? 1 : 0,
        _source: 'openclaw-probe-v0.2',
        _clock_skew: 0,
        integrity: this.emitDropCount === 0 ? 1.0 : 0.99,
        vm_rss_kb: vmRssKb,
      },
    });
    this.emitSuccessCount = 0;
    this.emitDropCount = 0;
    this._flush();
  }

  _startHeartbeat() {
    this.heartbeatTimer = setInterval(() => this._beat(), this.heartbeatIntervalMs);
    if (this.heartbeatTimer.unref) {
      this.heartbeatTimer.unref();
    }
  }

  // ─── 生命周期 ───

  async start() {
    if (this.running) return;
    this.running = true;

    console.log('[mingjing-probe] Starting probe runtime...');
    console.log(`[mingjing-probe] Hot path: ${this.hotPath}`);

    _ensureHotDir(this.hotPath);

    // 运行时解析 SDK（进程级缓存，只在第一次加载时扫描文件系统）
    if (!_sdk) {
      try {
        _sdk = await resolveAll();
      } catch (err) {
        console.error(`[mingjing-probe] SDK resolve failed: ${err.message}`);
        return;
      }
    }

    this._subscribeAgentEvents();
    this._subscribeDiagnosticEvents();
    this._subscribeSessionEvents();
    this._startHeartbeat();
    this._beat(); // 启动时立即发射心跳，加速信号链跑通

    // 发射注册事件
    this._emit('__register__', {
      pid: process.pid,
      mode: 'white',
      schema_version: '0.1.0',
      registered_at: Date.now() / 1000,
      openclaw_version: process.env.npm_package_version || 'unknown',
      hot_path: this.hotPath,
    });

    this._flush();
    console.log('[mingjing-probe] Probe runtime started');
  }

  async stop() {
    if (!this.running) return;
    this.running = false;

    console.log('[mingjing-probe] Stopping probe runtime...');

    // 取消订阅
    for (const fn of this.unsubscribeFns) {
      try { fn(); } catch {}
    }
    this.unsubscribeFns = [];

    // 停止心跳
    if (this.heartbeatTimer) {
      clearInterval(this.heartbeatTimer);
      this.heartbeatTimer = null;
    }

    // P0-12: 清除所有 pending timeouts
    for (const tid of this._pendingTimeouts) {
      clearTimeout(tid);
    }
    this._pendingTimeouts.clear();

    // 刷新缓冲区
    this._flush();

    console.log('[mingjing-probe] Probe runtime stopped');
  }
}

export { MingjingProbeRuntime };
