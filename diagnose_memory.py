#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""记忆库专项诊断 —— 绕过所有 try/except，把真实报错打出来。

为什么需要它：
  check_install.py 只检查模型文件在不在 / 大小对不对，**从不真正加载模型、
  从不建 chromadb 客户端、从不做一次 embedding**。所以它"通过"不代表记忆能用。
  而 server / 工具层把记忆的真实异常都吞了（warmup 只打日志、recall 把异常变字符串
  喂给 LLM、health 把 -1 当正常），于是"记忆用不了但查不到原因"。

  本脚本把整条链路逐段拆开单独试，任何一段失败都打印**完整 traceback**，
  最后给一句话判定。把整段输出贴回来即可定位。

用法（务必用 venv 的 python）：
  Windows : .venv\\Scripts\\python.exe diagnose_memory.py
  Unix    : .venv/bin/python diagnose_memory.py
"""
from __future__ import annotations

import sys
import io
import os
import traceback

# 保证中文/emoji 在 Windows GBK 终端不炸
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass


def line(s=""):
    print(s, flush=True)


def section(title):
    line()
    line("=" * 64)
    line(f"  {title}")
    line("=" * 64)


verdict = []


# ── 0. 运行环境 ───────────────────────────────────────────────────────────────
section("0. 运行环境 / 版本")
line(f"  OS            : {sys.platform}  ({os.name})")
line(f"  Python        : {sys.version.split()[0]}  @ {sys.executable}")

# sqlite 是 chromadb 的硬门槛：必须 >= 3.35.0
try:
    import sqlite3
    sv = sqlite3.sqlite_version
    ok = tuple(map(int, sv.split("."))) >= (3, 35, 0)
    line(f"  sqlite3       : {sv}   ({'OK' if ok else '太旧！chromadb 需 >= 3.35.0'})")
    if not ok:
        verdict.append(
            "sqlite 版本过低（chromadb 需 >= 3.35.0）。换 python.org 的 Python 3.11+，"
            "或按 chromadb 文档用 pysqlite3-binary 顶替。"
        )
except Exception:
    line("  sqlite3       : 取不到")

for mod in ("chromadb", "sentence_transformers", "transformers",
            "torch", "huggingface_hub", "tokenizers", "numpy"):
    try:
        m = __import__(mod)
        line(f"  {mod:20}: {getattr(m, '__version__', '?')}")
    except Exception as e:
        line(f"  {mod:20}: 导入失败 -> {type(e).__name__}: {e}")
        verdict.append(f"{mod} 导入就失败：{e}")


# ── 1. 项目路径 / 模型文件 ────────────────────────────────────────────────────
section("1. 项目路径 + 模型文件")
try:
    from paths import PROJECT_ROOT
    line(f"  PROJECT_ROOT  : {PROJECT_ROOT}")
    # 路径是否含非 ASCII（中文/空格）—— Windows 上偶尔会卡住模型/库加载
    non_ascii = any(ord(c) > 127 for c in str(PROJECT_ROOT))
    if non_ascii:
        line("  ⚠ 路径含非 ASCII 字符（中文等），Windows 上个别库可能因此加载异常")
        verdict.append("安装路径含中文/非 ASCII，可尝试移到纯英文路径（如 D:\\yuki）排除。")
    model_dir = PROJECT_ROOT / "models" / "bge-base-zh-v1.5"
    line(f"  模型目录       : {model_dir}  (exists={model_dir.exists()})")
    if model_dir.exists():
        for f in ("config.json", "pytorch_model.bin", "tokenizer.json",
                  "vocab.txt", "modules.json", "config_sentence_transformers.json",
                  "1_Pooling/config.json"):
            p = model_dir / f
            sz = p.stat().st_size if p.exists() else -1
            flag = "" if sz > 0 else "  <-- 缺失/空!"
            line(f"     {f:42} {sz:>12} bytes{flag}")
except Exception:
    line("  路径检查异常：")
    traceback.print_exc()


# ── 2. 只建 chromadb 客户端（隔离 sqlite / chromadb 自身问题）──────────────────
section("2. 单独测 chromadb.PersistentClient（不碰模型）")
try:
    import tempfile
    import chromadb
    c = chromadb.PersistentClient(path=tempfile.mkdtemp(prefix="yuki_diag_"))
    line("  ✓ PersistentClient 建立成功 —— chromadb / sqlite 这一段没问题")
except Exception:
    line("  ✗ PersistentClient 失败（chromadb 或 sqlite 层）：")
    traceback.print_exc()
    verdict.append("chromadb 客户端就建不起来 —— 重点看上面这段 traceback（多半是 sqlite）。")


# ── 3. 只加载 bge 模型（隔离 sentence-transformers / torch / 网络）─────────────
section("3. 单独测 bge 模型加载（不碰 chromadb）")
try:
    import memory  # noqa
    fn = memory._get_embedding_fn()
    vec = fn(["测试一句话"])
    dim = len(vec[0]) if vec else 0
    line(f"  ✓ 模型加载成功，输出向量维度 = {dim}（bge-base-zh 应为 768）")
except Exception:
    line("  ✗ bge 模型加载失败：")
    traceback.print_exc()
    verdict.append(
        "bge 模型加载失败 —— 看上面 traceback：若提到 huggingface.co/连接/timeout "
        "则是联网校验（离线变量没生效）；若提到 weights_only/torch.load 则是 torch 版本；"
        "若提到 config/keyerror 则是 transformers 版本与模型不配。"
    )


# ── 4. 完整链路：建集合 + 存 + 查（和 server / 工具走同一条路）──────────────────
section("4. 完整链路：_get_collection + add + search")
try:
    import memory
    before = memory.count_memories()
    line(f"  当前记忆条数 = {before}")
    mid = memory.add_memory("诊断用临时记忆：用户喜欢喝美式咖啡", category="other", importance=1)
    line(f"  ✓ add_memory OK  -> id={mid[:8]}")
    res = memory.search_memory("喜欢什么咖啡", top_k=1)
    if res:
        line(f"  ✓ search_memory OK -> 命中：{res[0]['text']!r}  dist={res[0].get('distance')}")
    else:
        line("  ⚠ search 没返回结果（库可能为空或检索异常）")
    memory.delete_memory(mid)
    line("  ✓ 已删除诊断记忆，恢复原状")
    line()
    line("  >>> 记忆全链路通过：这台机器上记忆库本身是好的。 <<<")
except Exception:
    line("  ✗ 完整链路失败：")
    traceback.print_exc()
    verdict.append("完整链路失败 —— 结合第 2/3 段定位是 chromadb 还是模型。")


# ── 总结 ──────────────────────────────────────────────────────────────────────
section("判定")
if not verdict:
    line("  本机记忆库正常。若朋友机器仍坏，请在朋友机器上跑本脚本，对照差异。")
else:
    for i, v in enumerate(verdict, 1):
        line(f"  [{i}] {v}")
line()
