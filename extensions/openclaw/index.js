// index.js —— OpenClaw 乾坤镜探针插件入口
// 使用运行时 SDK 解析，无需编译捆绑，OpenClaw 升级后自动适配

import { MingjingProbeRuntime } from './probe-runtime.js';

let probeInstance = null;

export default {
  id: 'mingjing-probe',
  name: '乾坤镜探针',
  description: '乾坤镜 (Mingjing) 监控探针，采集 OpenClaw 运行时事件至热轨',
  register(api) {
    api.registerService({
      id: 'mingjing-probe',
      start: async (ctx) => {
        const pluginConfig = ctx?.config?.['mingjing-probe'] || {};
        probeInstance = new MingjingProbeRuntime({
          hotPath: pluginConfig.hotPath,
          heartbeatIntervalMs: pluginConfig.heartbeatIntervalMs,
          bufferSize: pluginConfig.bufferSize,
        });
        await probeInstance.start();
      },
      stop: async () => {
        if (probeInstance) {
          await probeInstance.stop();
          probeInstance = null;
        }
      },
    });
  },
};
