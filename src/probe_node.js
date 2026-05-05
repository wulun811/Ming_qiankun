// probe_node.js —— v0.11.9m 乾坤镜 Node.js 探针
// 职责：纯粹的无状态热轨写入器，零数据库依赖，零 npm 依赖
// 依赖：Node.js 标准库 only (fs, path, os, process)
// 禁止：任何网络通信、任何第三方包

const fs = require('fs');
const path = require('path');
const os = require('os');

const DEFAULT_HOT_DIR = path.join(os.homedir(), '.ming', 'hot');
const FALLBACK_DIR = path.join(os.tmpdir(), 'ming_fallback');
const DISK_MIN_MB = 500;
const SCHEMA_VERSION = '0.11.9m';
const BATCH_SIZE = 10;
const FLUSH_INTERVAL_MS = 200;
const DISK_CHECK_INTERVAL_MS = 5000;

const REQUIRED_LAYERS = {
  llm_invoke: ['layer_agent', 'layer_llm', 'layer_network'],
  tool_call: ['layer_agent', 'layer_tool', 'layer_network'],
  memory_retrieve: ['layer_agent', 'layer_memory'],
  agent_step: ['layer_agent'],
  error: ['layer_agent']
};

let _hotDir = DEFAULT_HOT_DIR;
let _selfErrors = [];
let _batch = [];
let _lastFlush = 0;
let _lastDiskCheck = 0;
let _diskFreeMb = 9999;
let _lamport = 0;
let _lastWall = 0;
let _lastMono = 0;
let _system = '';
let _mode = 'white';
let _pid = 0;
let _baseIntegrity = 1.0;
let _flushTimer = null;

function _init(system, mode, pid) {
  _system = system;
  _mode = mode || 'white';
  _pid = pid || process.pid;
  _baseIntegrity = _mode === 'black' ? 0.6 : 1.0;
  _lastFlush = Date.now();
  _hotDir = process.env.MING_HOT_DIR || DEFAULT_HOT_DIR;

  try {
    fs.mkdirSync(_hotDir, { recursive: true });
    const test = path.join(_hotDir, '.probe_write_test');
    fs.writeFileSync(test, '1');
    fs.unlinkSync(test);
  } catch (e) {
    _hotDir = FALLBACK_DIR;
    fs.mkdirSync(_hotDir, { recursive: true });
    _selfErrors.push('hot_dir_fallback_to_tmp');
  }

  emit('__register__', {
    pid: _pid, mode: _mode, schema_version: SCHEMA_VERSION,
    registered_at: Date.now() / 1000, _base_integrity: _baseIntegrity
  });

  _flushTimer = setInterval(_flush, FLUSH_INTERVAL_MS);
  _flushTimer.unref();
  process.on('exit', _flush);
  process.on('SIGINT', () => { _flush(); process.exit(0); });
  process.on('SIGTERM', () => { _flush(); process.exit(0); });
}

function _validateLayers(eventType, payload) {
  const required = REQUIRED_LAYERS[eventType] || [];
  const missing = required.filter(l => !(l in payload));
  if (missing.length) {
    payload._incomplete = missing;
    payload._integrity_hint = 'partial';
  }
}

function _clockSkew(payload) {
  const nowWall = Date.now() / 1000;
  const nowMono = Date.now();
  if (_lastWall > 0) {
    const wallDelta = nowWall - _lastWall;
    const monoDelta = (nowMono - _lastMono) / 1000;
    if (Math.abs(wallDelta - monoDelta) > 5) {
      payload._clock_skew = true;
      payload._time_reliable = false;
    } else {
      payload._time_reliable = true;
    }
  }
  _lastWall = nowWall;
  _lastMono = nowMono;
}

function _pausedPath() {
  return path.join(os.homedir(), '.ming', '.paused', _system);
}

let _pausedUntil = 0;

function _isPaused() {
  const now = Date.now();
  if (now < _pausedUntil) return true;
  try {
    if (fs.existsSync(_pausedPath())) {
      _pausedUntil = now + 5000; // 缓存 5 秒，避免高频 stat
      return true;
    }
  } catch {}
  _pausedUntil = 0;
  return false;
}

function emit(eventType, payload) {
  if (_isPaused()) return;
  _validateLayers(eventType, payload);
  _clockSkew(payload);

  if (_mode === 'black') {
    payload._base_integrity = 0.6;
  }

  _batch.push({
    system: _system, mode: _mode, event_type: eventType,
    payload: payload, timestamp: Date.now() / 1000,
    monotonic_ms: Date.now(), _pid: _pid,
    _schema_version: SCHEMA_VERSION
  });

  if (_batch.length >= BATCH_SIZE) _flush();
}

function _flush() {
  if (!_batch.length) return;

  const now = Date.now();
  if (now - _lastDiskCheck > DISK_CHECK_INTERVAL_MS) {
    try {
      const stat = fs.statfsSync(_hotDir);
      _diskFreeMb = (stat.bavail * stat.bsize) / (1024 * 1024);
    } catch (e) {
      _diskFreeMb = -1;
    }
    _lastDiskCheck = now;
  }

  if (_diskFreeMb < DISK_MIN_MB) {
    _batch = _batch.filter(ev => ev.event_type.startsWith('__') || ev.event_type === 'error');
    if (!_batch.length) return;
  }

  for (let i = 0; i < _batch.length; i++) {
    _lamport++;
    _batch[i].lamport = _lamport;
  }

  const ts = new Date().toISOString().replace(/[-:T.]/g, '').slice(0, 14);
  const filepath = path.join(_hotDir, `${_system}_${ts}_${_pid}.jsonl`);
  const lines = _batch.map(ev => JSON.stringify(ev) + '\n').join('');

  try {
    fs.appendFileSync(filepath, lines, 'utf8');
  } catch (e) {
    _selfErrors.push(e.message);
    process.stderr.write(`[MING-DROP] ${e.message}\n`);
  }

  _batch = [];
  _lastFlush = Date.now();
}

function expect(eventType, withinSeconds) {
  emit('__expect__', {
    expected_event: eventType,
    deadline: Date.now() / 1000 + withinSeconds,
    fulfilled: false
  });
}

function fulfill(eventType) {
  emit('__fulfill__', { expected_event: eventType });
}

function touch() {
  emit('__touch__', { pid: _pid });
}

function health() {
  emit('__health__', {
    emit_success_count_1m: _batch.length,
    emit_drop_count_1m: 0,
    last_errors: _selfErrors.slice(-5),
    disk_free_mb: _diskFreeMb,
    buffer_queue_size: _batch.length
  });
}

module.exports = { init: _init, emit, flush: _flush, expect, fulfill, touch, health };
