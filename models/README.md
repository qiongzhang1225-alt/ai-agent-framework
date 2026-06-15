# 本地 Embedding 模型

此目录存放 ChromaDB 用的 **bge-base-zh-v1.5** 中文 embedding 模型。
模型本体 390MB，不进 git 仓库；首次部署时手动下载。

## 为什么用 bge-base-zh-v1.5

之前 ChromaDB 用默认的 `all-MiniLM-L6-v2`（英文优化的小模型），对中文检索几乎失效 ——
不同的中文 query 返回完全相同的 distance 值。换 bge-base-zh 后中文语义检索正常。

## 为什么用本地路径不联网

`sentence_transformers` / `transformers` 库通过 HF 镜像下载时不稳定
（曾在 Windows 上踩过 SSL 中断 + 反复重试）。改用本地路径加载彻底绕开网络问题。

`memory.py` 启动时会设置 `HF_HUB_OFFLINE=1`，强制离线模式。

## 文件清单（共 10 个，总计 ~390 MB）

```
models/bge-base-zh-v1.5/
├── 1_Pooling/
│   └── config.json
├── config.json
├── config_sentence_transformers.json
├── modules.json
├── pytorch_model.bin                    ← 大头，约 390 MB
├── sentence_bert_config.json
├── special_tokens_map.json
├── tokenizer.json
├── tokenizer_config.json
└── vocab.txt
```

## 下载方法

模型来源：[BAAI/bge-base-zh-v1.5](https://huggingface.co/BAAI/bge-base-zh-v1.5)

### 方法 1：浏览器逐个下载（推荐）

3 个镜像任选其一（按速度 / 稳定性优先选）：

| 镜像 | 基础 URL |
|---|---|
| **ModelScope（阿里，国内最稳）** | `https://modelscope.cn/models/AI-ModelScope/bge-base-zh-v1.5/resolve/master/<file>` |
| **HF 国内镜像** | `https://hf-mirror.com/BAAI/bge-base-zh-v1.5/resolve/main/<file>` |
| **HF 官方** | `https://huggingface.co/BAAI/bge-base-zh-v1.5/resolve/main/<file>` |

把 `<file>` 替换为下面 10 个文件名，逐个下载到对应位置：

```
config.json
config_sentence_transformers.json
modules.json
pytorch_model.bin                    ← 这个大，建议用 IDM/迅雷 之类支持断点续传的工具
sentence_bert_config.json
special_tokens_map.json
tokenizer.json
tokenizer_config.json
vocab.txt
1_Pooling/config.json                ← 注意是子目录
```

### 方法 2：git clone（如果 git LFS 配好了）

```bash
git lfs install
git clone https://hf-mirror.com/BAAI/bge-base-zh-v1.5 models/bge-base-zh-v1.5
```

### 方法 3：huggingface_hub SDK

```python
import os
os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"   # 国内走镜像
os.environ["HF_HUB_DISABLE_XET"] = "1"                # 关掉 xet，大文件才走镜像能代理的经典下载
from huggingface_hub import snapshot_download
snapshot_download(
    "BAAI/bge-base-zh-v1.5",
    local_dir="models/bge-base-zh-v1.5",              # 相对项目目录；别写死成别人机器的绝对路径
    local_dir_use_symlinks=False,
)
```

注意：方法 3 在某些网络环境下也不稳，方法 1 + IDM 最可靠。

## 验证

下载完跑这段确认无误：

```python
from sentence_transformers import SentenceTransformer
m = SentenceTransformer('models/bge-base-zh-v1.5')
print(m.get_embedding_dimension())   # 应输出 768
print(m.encode(['你好'])[0][:5])      # 应输出一个浮点向量
```
