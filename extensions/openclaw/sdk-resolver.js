// sdk-resolver.js —— 运行时动态解析 OpenClaw SDK
// 原理：在 OpenClaw 进程上下文中，process.cwd() 即 OpenClaw 安装目录。
// 扫描 node_modules/openclaw/dist/ 按前缀匹配 hash 文件名，
// 按函数 .name 属性匹配导出，不依赖 minified 变量名。
// 效果：OpenClaw 升级后无需重新编译探针，hash 和变量名变更自动适配。

import { resolve } from 'node:path';
import { readdirSync, existsSync } from 'node:fs';

let _cache = null;

async function _findExportByName(filePath, fnName) {
  const mod = await import(filePath);
  for (const [, v] of Object.entries(mod)) {
    if (typeof v === 'function' && v.name === fnName) return v;
  }
  throw new Error(`[mingjing-sdk] Export '${fnName}' not found in ${filePath}`);
}

async function _load() {
  if (_cache) return _cache;

  const cwd = process.cwd();
  const dist = resolve(cwd, 'node_modules/openclaw/dist');
  if (!existsSync(dist)) {
    throw new Error(`[mingjing-sdk] OpenClaw dist not found at ${dist}. Is OpenClaw running?`);
  }

  const allFiles = readdirSync(dist);
  const _match = (prefix) => {
    const f = allFiles.find(f => f.startsWith(prefix) && f.endsWith('.js'));
    if (!f) throw new Error(`[mingjing-sdk] ${prefix}*.js not found in ${dist}`);
    return resolve(dist, f);
  };

  _cache = {
    definePluginEntry: (await import(resolve(dist, 'plugin-sdk/plugin-entry.js'))).definePluginEntry,
    onAgentEvent: await _findExportByName(_match('agent-events-'), 'onAgentEvent'),
    onDiagnosticEvent: await _findExportByName(_match('diagnostic-events-'), 'onDiagnosticEvent'),
    onSessionTranscriptUpdate: await _findExportByName(_match('transcript-events-'), 'onSessionTranscriptUpdate'),
  };

  return _cache;
}

// 模块加载时立即启动 SDK 解析，start() 时直接取缓存，零延时
const _boot = _load();

export async function resolveAll() {
  return _boot;
}
