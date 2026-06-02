# 自定义技能目录

此目录存放**私人助手自己写下来的技能**（持久化的 Python 函数），server 启动时
会自动扫描加载到工具注册表。

## 文件命名约定

- 每个技能一个 `.py` 文件
- 文件名 `<name>.py` 必须跟内部主函数名一致（`def <name>(...)`)
- 以 `_` 开头的文件会被跳过（用作工具内部模块）

## 文件内容模板

```python
def my_skill(arg1: str, arg2: int = 10) -> str:
    """一行简短描述（会作为 LLM 看到的工具描述）。

    可选的多行说明。
    """
    # 函数体可以 import 任何标准库或预装库
    import hashlib
    return hashlib.sha256(arg1.encode()).hexdigest()[:arg2]
```

## 增删流程

通常**不需要手动编辑此目录**。让私人助手自己处理：

- 主人："以后我经常要计算 SHA256，做个工具" → 私人助手调 `define_skill(...)`，
  自动在这里创建文件
- 主人："不要那个 sha256 技能了" → 私人助手调 `delete_skill('sha256')`，
  自动删除文件 + 卸载注册

## 直接手动编辑

如果你想直接写 `.py` 文件，遵守上面的命名 + 模板规则，重启 server 即可加载。

加载失败时控制台会打印 `[skill] 跳过 xxx.py：...` 提示原因。

## 哪些是核心工具（不能被覆盖）

`web_search` / `fetch_webpage` / `calculate` / `get_current_datetime` /
`execute_code` / `remember` / `recall` / `read_file` / `write_file` /
`edit_file` / `grep` / `glob` / `define_skill` / `list_skills` / `delete_skill`

这些名字被保留，`define_skill` 会拒绝覆盖。
