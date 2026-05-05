// _js_base.js —— 0.11.9m 乾坤镜 JS 适配器基类
// 职责：提供工厂函数和拦截器，消除适配器样板代码
// 归属：官方参考实现内部工具，非协议标准

const probe = require('../probe_node');

function makeAdapterInit(defaultSystem, patchFn) {
  return function(config) {
    const system = (config && config.system) || defaultSystem;
    const mode = (config && config.mode) || 'white';
    probe.init(system, mode);
    patchFn(probe);
    return probe;
  };
}

function intercept(target, method, eventType, buildPayload, errorCtx) {
  const orig = target[method];
  if (!orig) return;
  target[method] = async function(...args) {
    const start = Date.now();
    try {
      const result = await orig.apply(this, args);
      probe.emit(eventType, buildPayload(result, Date.now() - start, args));
      return result;
    } catch (e) {
      const ctx = errorCtx || { layer_agent: { step_id: 'unknown', session_id: 'unknown', agent_name: 'unknown' } };
      probe.emit('error', { ...ctx, error_type: e.name, error_msg: e.message });
      throw e;
    }
  };
}

module.exports = { makeAdapterInit, intercept };
