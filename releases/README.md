# 乾坤镜发布包

每个版本的安装包和完整源码归档。

## v0.11.9m（当前最新）

| 包 | 文件 | 说明 |
|---|------|------|
| mingjing | `v0.11.9/mingjing-0.11.9a0-py3-none-any.whl` | 乾坤镜主包，`pip install` 即用 |
| mingjing | `v0.11.9/mingjing-0.11.9a0.tar.gz` | 乾坤镜源码包 |
| ming-probe-langchain | `v0.11.9/ming_probe_langchain-0.11.9-*.whl` | LangChain 自动探针，`pip install` 即用 |

## 从源码安装

```bash
git clone https://github.com/wulun811/Ming_qiankun.git
cd Ming_qiankun
pip install -e extensions/langchain/   # LangChain 探针
python src/ming.py                     # 乾坤镜 CLI
```
