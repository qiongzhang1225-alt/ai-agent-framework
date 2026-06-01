"""代码全局感知（D3：tree-sitter 符号索引）。

让有希能精确搜索符号定义/引用，而非纯文本 grep。

架构：
- CodeIndexer 单例，管理符号表缓存
- 每索引过的目录存一份 IndexCache（符号表 + 文件 hash）
- 增量更新：只重新索引 hash 变化的文件

当前支持 Python + JavaScript（tree-sitter-python / tree-sitter-javascript）。
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ── tree-sitter 初始化（延迟加载，首次 import 时做） ──────────────────────────

_PY_PARSER = None
_JS_PARSER = None

def _get_py_parser():
    global _PY_PARSER
    if _PY_PARSER is not None:
        return _PY_PARSER
    import tree_sitter_python
    from tree_sitter import Language, Parser
    lang = Language(tree_sitter_python.language())
    _PY_PARSER = Parser(lang)
    return _PY_PARSER

def _get_js_parser():
    global _JS_PARSER
    if _JS_PARSER is not None:
        return _JS_PARSER
    import tree_sitter_javascript
    from tree_sitter import Language, Parser
    lang = Language(tree_sitter_javascript.language())
    _JS_PARSER = Parser(lang)
    return _JS_PARSER

def _get_parser_for_file(filename: str):
    """按文件扩展名返回对应的 parser。"""
    ext = os.path.splitext(filename)[1].lower()
    if ext in (".js", ".jsx"):
        return _get_js_parser()
    return _get_py_parser()


# ── 数据结构 ──────────────────────────────────────────────────────────────────

@dataclass
class SymbolEntry:
    """一个符号的定义或引用位置。"""
    name: str
    kind: str          # "definition" / "reference"
    symbol_type: str   # "function" / "class" / "method" / "variable"
    file: str          # 相对路径（相对于索引根目录）
    line: int
    col: int
    parent: str = ""   # 所属类（方法时有用）

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "symbol_type": self.symbol_type,
            "file": self.file,
            "line": self.line,
            "col": self.col,
            "parent": self.parent,
        }


@dataclass
class OutlineEntry:
    """code_outline 的一行。"""
    name: str
    symbol_type: str   # "function" / "class" / "method"
    line: int
    parent: str = ""   # 所属类名
    children: list = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "symbol_type": self.symbol_type,
            "line": self.line,
            "parent": self.parent,
            "children": [c.to_dict() for c in self.children],
        }


@dataclass
class IndexCache:
    """一个目录的索引缓存。"""
    root: str
    file_hashes: dict[str, str] = field(default_factory=dict)  # path → md5
    symbols: list[SymbolEntry] = field(default_factory=list)   # 全量符号列表
    outlines: dict[str, list[OutlineEntry]] = field(default_factory=dict)  # file → outlines
    # 调用图：{file: {caller_name: [callee_name, ...]}}
    call_graph: dict[str, dict[str, list[str]]] = field(default_factory=dict)


# ── Indexer ──────────────────────────────────────────────────────────────────

_INDEX_EXCLUDE_DIRS = {
    ".git", "__pycache__", "node_modules", ".venv", "venv",
    ".sandbox", ".memory", ".skills_trash", ".execute_trash",
    "dist", "build", ".egg-info",
}
_INDEX_EXCLUDE_EXT = {".pyc", ".pyo", ".so", ".dll", ".pyd"}


class CodeIndexer:
    """代码索引器单例。"""

    _instance: "CodeIndexer | None" = None

    def __new__(cls) -> "CodeIndexer":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        if self._initialized:
            return
        self._initialized = True
        self._caches: dict[str, IndexCache] = {}  # root → cache

    # ── 公开方法 ──────────────────────────────────────────────────────────

    def search(
        self,
        query: str,
        path: str = ".",
        lang: str = "",
        kind: str | None = None,     # "definition" / "reference" / None=all
    ) -> list[SymbolEntry]:
        """搜索符号。query 支持 * 通配符（如 "train_*"）。"""
        path = str(Path(path).resolve())
        cache = self._ensure_index(path, lang)
        if not cache:
            return []

        import fnmatch
        results = []
        ext_map = {"python": ".py", "javascript": ".js"}
        target_ext = ext_map.get(lang) if lang else None
        for sym in cache.symbols:
            if not fnmatch.fnmatch(sym.name, query):
                continue
            if kind and sym.kind != kind:
                continue
            if target_ext and not sym.file.endswith(target_ext):
                continue
            results.append(sym)
        return results

    def outline(self, path: str) -> dict[str, list[OutlineEntry]]:
        """列出文件或目录的符号轮廓。返回 {file: [outlines]}。"""
        path = str(Path(path).resolve())
        if os.path.isfile(path):
            # 单文件：直接解析
            root = os.path.dirname(path)
            relpath = os.path.basename(path)
            cache = self._ensure_index(root, "python")
            if cache and relpath in cache.outlines:
                return {relpath: cache.outlines[relpath]}
            return {}
        # 目录 → 索引后返回所有文件的 outline
        cache = self._ensure_index(path, "python")
        if not cache:
            return {}
        return cache.outlines

    def refresh(self, path: str) -> None:
        """强制刷新索引。"""
        path = str(Path(path).resolve())
        self._caches.pop(path, None)
        self._ensure_index(path, "python")

    # ── 内部方法 ──────────────────────────────────────────────────────────

    def _ensure_index(self, root: str, lang: str) -> IndexCache | None:
        """确保 root 已被索引；返回缓存或 None。"""
        if not os.path.isdir(root):
            return None

        # 命中缓存
        if root in self._caches:
            cache = self._caches[root]
            # 检查文件是否有变化（增量更新）
            changed = []
            for filepath, old_hash in cache.file_hashes.items():
                full = os.path.join(root, filepath)
                if not os.path.isfile(full):
                    changed.append(filepath)
                elif self._file_hash(full) != old_hash:
                    changed.append(filepath)
            # 新增文件
            all_current = set(self._collect_files(root))
            cached_files = set(cache.file_hashes.keys())
            added = all_current - cached_files

            if not changed and not added:
                return cache  # 没变化

            # 增量更新
            for f in changed:
                self._index_file(root, f, cache)
                cache.file_hashes.pop(f, None)
                new_hash = self._file_hash(os.path.join(root, f))
                if new_hash:
                    cache.file_hashes[f] = new_hash
                cache.outlines.pop(f, None)

            for f in added:
                self._index_file(root, f, cache)
                cache.file_hashes[f] = self._file_hash(os.path.join(root, f))

            return cache

        # 全量索引
        files = self._collect_files(root)
        if not files:
            return None

        cache = IndexCache(root=root)
        for f in files:
            self._index_file(root, f, cache)
            cache.file_hashes[f] = self._file_hash(os.path.join(root, f))
        self._caches[root] = cache
        return cache

    def _collect_files(self, root: str) -> list[str]:
        """收集所有需要索引的文件（相对路径）。"""
        files = []
        INDEX_EXT = {".py", ".js", ".jsx"}
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in _INDEX_EXCLUDE_DIRS]
            for f in filenames:
                ext = os.path.splitext(f)[1].lower()
                if ext in _INDEX_EXCLUDE_EXT:
                    continue
                if ext in INDEX_EXT:
                    rel = os.path.relpath(os.path.join(dirpath, f), root)
                    files.append(rel)
        return sorted(files)

    def _file_hash(self, path: str) -> str:
        """文件 MD5（用于检测变化）。"""
        try:
            with open(path, "rb") as f:
                return hashlib.md5(f.read()).hexdigest()
        except Exception:
            return ""

    def _index_file(self, root: str, relpath: str, cache: IndexCache) -> None:
        """索引单个文件，更新 cache。自动按扩展名选语言。"""
        full = os.path.join(root, relpath)
        try:
            with open(full, "r", encoding="utf-8", errors="replace") as f:
                code = f.read()
        except Exception:
            return

        parser = _get_parser_for_file(relpath)
        tree = parser.parse(bytes(code, "utf-8"))
        outlines: list[OutlineEntry] = []

        # 清理老数据（增量更新时）
        cache.symbols = [s for s in cache.symbols if s.file != relpath]
        cache.outlines.pop(relpath, None)
        cache.call_graph.pop(relpath, None)

        file_calls: dict[str, list[str]] = {}
        ext = os.path.splitext(relpath)[1].lower()

        if ext in (".js", ".jsx"):
            self._walk_js(tree.root_node, code, relpath, cache.symbols, outlines,
                          file_calls=file_calls)
        else:
            self._walk_py(tree.root_node, code, relpath, cache.symbols, outlines,
                          file_calls=file_calls)

        if outlines:
            cache.outlines[relpath] = outlines
        if file_calls:
            cache.call_graph[relpath] = file_calls

    def _walk_py(
        self,
        node,
        code: str,
        relpath: str,
        symbols: list[SymbolEntry],
        outlines: list[OutlineEntry],
        parent_class: str = "",
        file_calls: dict[str, list[str]] | None = None,
        current_func: str = "",
    ) -> None:
        """递归遍历 Python AST，提取符号。

        file_calls: 调用图累加器 {caller_name: [callee_names]}
        current_func: 当前所在的函数名（用于构建调用图）
        """
        if node.type == "class_definition":
            name_node = node.child_by_field_name("name")
            if name_node:
                name = code[name_node.start_byte:name_node.end_byte]
                symbols.append(SymbolEntry(
                    name=name, kind="definition", symbol_type="class",
                    file=relpath, line=node.start_point[0] + 1,
                    col=node.start_point[1] + 1,
                ))
                entry = OutlineEntry(name=name, symbol_type="class", line=node.start_point[0] + 1)
                outlines.append(entry)
                body = node.child_by_field_name("body")
                if body:
                    for child in body.children:
                        self._walk_py(child, code, relpath, symbols, entry.children,
                                       parent_class=name, file_calls=file_calls,
                                       current_func=current_func)
            return

        if node.type == "function_definition":
            name_node = node.child_by_field_name("name")
            if name_node:
                name = code[name_node.start_byte:name_node.end_byte]
                sym_type = "method" if parent_class else "function"
                qualified = f"{parent_class}.{name}" if parent_class else name
                symbols.append(SymbolEntry(
                    name=name, kind="definition", symbol_type=sym_type,
                    file=relpath, line=node.start_point[0] + 1,
                    col=node.start_point[1] + 1, parent=parent_class,
                ))
                outlines.append(OutlineEntry(
                    name=name, symbol_type=sym_type,
                    line=node.start_point[0] + 1, parent=parent_class,
                ))
                # 进入函数体 → 切换 current_func
                body = node.child_by_field_name("body")
                if body and file_calls is not None:
                    file_calls.setdefault(qualified, [])
                    for child in body.children:
                        self._walk_py(child, code, relpath, symbols, outlines,
                                       parent_class=parent_class, file_calls=file_calls,
                                       current_func=qualified)
                    return
            return

        if node.type == "decorated_definition":
            for child in node.children:
                self._walk_py(child, code, relpath, symbols, outlines, parent_class,
                               file_calls=file_calls, current_func=current_func)
            return

        # 调用表达式 → 记录为引用 + 加入调用图
        if node.type == "call":
            func_node = node.child_by_field_name("function")
            if func_node:
                name = code[func_node.start_byte:func_node.end_byte]
                if "." not in name and not name.startswith("self."):
                    symbols.append(SymbolEntry(
                        name=name, kind="reference", symbol_type="function",
                        file=relpath, line=node.start_point[0] + 1,
                        col=node.start_point[1] + 1,
                    ))
                    # 调用图：谁调了谁
                    if file_calls is not None and current_func:
                        file_calls.setdefault(current_func, [])
                        if name not in file_calls[current_func]:
                            file_calls[current_func].append(name)
            return

        # 继续遍历子节点
        for child in node.children:
            self._walk_py(child, code, relpath, symbols, outlines, parent_class,
                           file_calls=file_calls, current_func=current_func)

    def _walk_js(
        self,
        node,
        code: str,
        relpath: str,
        symbols: list[SymbolEntry],
        outlines: list[OutlineEntry],
        parent_class: str = "",
        file_calls: dict[str, list[str]] | None = None,
        current_func: str = "",
    ) -> None:
        """递归遍历 JavaScript AST，提取符号。"""
        # class Foo { ... }
        if node.type == "class_declaration":
            name_node = node.child_by_field_name("name")
            if name_node:
                name = code[name_node.start_byte:name_node.end_byte]
                symbols.append(SymbolEntry(
                    name=name, kind="definition", symbol_type="class",
                    file=relpath, line=node.start_point[0] + 1,
                    col=node.start_point[1] + 1,
                ))
                entry = OutlineEntry(name=name, symbol_type="class", line=node.start_point[0] + 1)
                outlines.append(entry)
                body = node.child_by_field_name("body")
                if body:
                    for child in body.children:
                        self._walk_js(child, code, relpath, symbols, entry.children,
                                       parent_class=name, file_calls=file_calls,
                                       current_func=current_func)
            return

        # method foo() { ... } (class method)
        if node.type == "method_definition":
            name_node = node.child_by_field_name("name")
            if name_node:
                name = code[name_node.start_byte:name_node.end_byte]
                qualified = f"{parent_class}.{name}" if parent_class else name
                symbols.append(SymbolEntry(
                    name=name, kind="definition", symbol_type="method",
                    file=relpath, line=node.start_point[0] + 1,
                    col=node.start_point[1] + 1, parent=parent_class,
                ))
                outlines.append(OutlineEntry(
                    name=name, symbol_type="method",
                    line=node.start_point[0] + 1, parent=parent_class,
                ))
                body = node.child_by_field_name("body")
                if body and file_calls is not None:
                    file_calls.setdefault(qualified, [])
                    for child in body.children:
                        self._walk_js(child, code, relpath, symbols, outlines,
                                       parent_class=parent_class, file_calls=file_calls,
                                       current_func=qualified)
                    return
            return

        # function foo() { ... }
        if node.type == "function_declaration":
            name_node = node.child_by_field_name("name")
            if name_node:
                name = code[name_node.start_byte:name_node.end_byte]
                symbols.append(SymbolEntry(
                    name=name, kind="definition", symbol_type="function",
                    file=relpath, line=node.start_point[0] + 1,
                    col=node.start_point[1] + 1,
                ))
                outlines.append(OutlineEntry(
                    name=name, symbol_type="function", line=node.start_point[0] + 1,
                ))
                body = node.child_by_field_name("body")
                if body and file_calls is not None:
                    file_calls.setdefault(name, [])
                    for child in body.children:
                        self._walk_js(child, code, relpath, symbols, outlines,
                                       file_calls=file_calls, current_func=name)
                    return
            return

        # const foo = () => { ... } / const foo = function() { ... }
        if node.type == "variable_declarator":
            name_node = node.child_by_field_name("name")
            value_node = node.child_by_field_name("value")
            if name_node and value_node and value_node.type in ("arrow_function", "function"):
                name = code[name_node.start_byte:name_node.end_byte]
                symbols.append(SymbolEntry(
                    name=name, kind="definition", symbol_type="function",
                    file=relpath, line=node.start_point[0] + 1,
                    col=node.start_point[1] + 1,
                ))
                outlines.append(OutlineEntry(
                    name=name, symbol_type="function", line=node.start_point[0] + 1,
                ))
                body = value_node.child_by_field_name("body")
                if body and file_calls is not None:
                    file_calls.setdefault(name, [])
                    # 箭头函数体的 children 遍历
                    children_list = body.children if hasattr(body, 'children') else []
                    if value_node.type == "arrow_function" and body.type == "statement_block":
                        children_list = body.children
                    else:
                        children_list = []
                    for child in children_list:
                        self._walk_js(child, code, relpath, symbols, outlines,
                                       file_calls=file_calls, current_func=name)
                return
            # 非函数赋值不做特殊处理
            for child in node.children:
                self._walk_js(child, code, relpath, symbols, outlines,
                               file_calls=file_calls, current_func=current_func)
            return

        # export { ... } / export default class / export function
        if node.type in ("export_statement", "export_default"):
            for child in node.children:
                self._walk_js(child, code, relpath, symbols, outlines, parent_class,
                               file_calls=file_calls, current_func=current_func)
            return

        # arrow_function 不在 variable_declarator 下时（回调参数等）忽略

        # 调用表达式 foo() → 记录引用
        if node.type == "call_expression":
            func_node = node.child_by_field_name("function")
            if func_node:
                name = code[func_node.start_byte:func_node.end_byte]
                if not name.startswith("this.") and not name.startswith("console."):
                    symbols.append(SymbolEntry(
                        name=name, kind="reference", symbol_type="function",
                        file=relpath, line=node.start_point[0] + 1,
                        col=node.start_point[1] + 1,
                    ))
                    # 调用图
                    if file_calls is not None and current_func:
                        file_calls.setdefault(current_func, [])
                        if name not in file_calls[current_func]:
                            file_calls[current_func].append(name)
            return

        # 继续遍历子节点
        for child in node.children:
            self._walk_js(child, code, relpath, symbols, outlines, parent_class,
                           file_calls=file_calls, current_func=current_func)


_indexer = CodeIndexer()


def get_indexer() -> CodeIndexer:
    return _indexer


# ── Tool 封装 ────────────────────────────────────────────────────────────────

from ai_agent import tool


@tool
def code_search(
    query: str,
    path: str = ".",
    lang: str = "",
    kind: str = "",
    config: dict = {},
) -> str:
    """**精确搜索**代码中的符号（函数名、类名、变量名），返回定义和引用位置。

    比 grep 更精确：能区分"定义"和"引用"，且不受注释/字符串中的同名干扰。
    当前支持 Python（lang='python'）和 JavaScript（lang='javascript'），
    默认搜索全部语言（lang=''）。

    参数：
        query: 符号名，支持 * 通配符（如 "train_*"、"*Loader"）
        path: 搜索路径（文件或目录，默认当前工作目录）
        lang: 语言过滤——"python" 只看 .py，"javascript" 只看 .js/.jsx，
              空字符串（默认）搜索所有语言
        kind: 过滤类型——"definition" 只看定义，"reference" 只看引用，
              空字符串（默认）全部显示

    返回：符号列表，含位置和类型。
    """
    from paths import DEFAULT_WORKDIR

    # 解析路径
    cfg = config.get("configurable", {}) if config else {}
    base = Path(cfg.get("workdir") or str(DEFAULT_WORKDIR)).resolve()
    search_path = str((base / path).resolve()) if not os.path.isabs(path) else path

    indexer = get_indexer()
    kind_filter = kind.strip() or None
    results = indexer.search(query, search_path, lang, kind_filter)

    if not results:
        return f"未找到符号 {query!r}（搜索路径: {search_path}）"

    # 按文件分组
    by_file: dict[str, list[SymbolEntry]] = {}
    for r in results:
        by_file.setdefault(r.file, []).append(r)

    lines = [f"🔍 搜索 {query!r} — 共 {len(results)} 处"]
    for file in sorted(by_file):
        entries = by_file[file]
        lines.append(f"\n  📄 {file}")
        for e in entries:
            kind_icon = "▸" if e.kind == "definition" else "·"
            type_tag = f"[{e.symbol_type}]" if e.kind == "definition" else ""
            parent_tag = f" ← {e.parent}" if e.parent else ""
            lines.append(
                f"    {kind_icon} {e.name} {type_tag}{parent_tag}"
                f"  L{e.line}:{e.col}"
            )

    return "\n".join(lines)


@tool
def code_outline(
    path: str = ".",
    config: dict = {},
) -> str:
    """列出代码文件的**结构轮廓**（类、函数、方法），支持树形显示。

    参数：
        path: 文件或目录路径（默认当前工作目录）

    返回：树形结构，含行号和类型。目录时按文件分组。
    """
    from paths import DEFAULT_WORKDIR

    cfg = config.get("configurable", {}) if config else {}
    base = Path(cfg.get("workdir") or str(DEFAULT_WORKDIR)).resolve()
    search_path = str((base / path).resolve()) if not os.path.isabs(path) else path

    indexer = get_indexer()
    outlines = indexer.outline(search_path)

    if not outlines:
        return f"未找到代码文件（路径: {search_path}）"

    lines = [f"📋 代码轮廓 — {search_path}\n"]
    for file in sorted(outlines):
        entries = outlines[file]
        lines.append(f"  📄 {file}")
        for e in entries:
            icon = "├──"
            sup = f"  (继承: {e.parent})" if e.parent else ""
            lines.append(f"    {icon} {e.symbol_type} {e.name}  L{e.line}{sup}")
            for child in e.children:
                lines.append(f"    │  ├── {child.symbol_type} {child.name}  L{child.line}")
    return "\n".join(lines)


# ── 辅助函数（供 code_references / code_dependencies 使用） ──────────────────

def _read_code_line(root: str, file_rel: str, line_no: int) -> str:
    """读取某文件的指定行，返回代码文本。"""
    full = os.path.join(root, file_rel)
    if not os.path.isfile(full):
        return ""
    try:
        with open(full, "r", encoding="utf-8", errors="replace") as f:
            for i, line in enumerate(f, 1):
                if i == line_no:
                    return line.rstrip("\n").strip()
    except Exception:
        pass
    return ""


def _find_call_line(root: str, file_rel: str, callee: str) -> int:
    """近似查找某文件中调用某符号的行号。"""
    full = os.path.join(root, file_rel)
    if not os.path.isfile(full):
        return 0
    try:
        with open(full, "r", encoding="utf-8", errors="replace") as f:
            for i, line in enumerate(f, 1):
                if callee in line and "(" in line:
                    return i
    except Exception:
        pass
    return 0


@tool
def code_references(
    name: str,
    path: str = ".",
    config: dict = {},
) -> str:
    """查询某符号的所有引用处，含代码上下文行。"""
    from paths import DEFAULT_WORKDIR
    cfg = config.get("configurable", {}) if config else {}
    base = Path(cfg.get("workdir") or str(DEFAULT_WORKDIR)).resolve()
    search_path = str((base / path).resolve()) if not os.path.isabs(path) else path
    indexer = get_indexer()
    defs = indexer.search(name, search_path, kind="definition")
    refs = indexer.search(name, search_path, kind="reference")
    if not defs and not refs:
        return f"未找到符号 {name!r}"
    lines = []
    if defs:
        lines.append(f"  {name} 的定义:")
        for d in defs:
            ctx = _read_code_line(search_path, d.file, d.line)
            tag = f"[{d.symbol_type}]" + (f" belongs to {d.parent}" if d.parent else "")
            lines.append(f"    {d.file}  L{d.line}  {tag}")
            if ctx:
                lines.append(f"      {ctx}")
        lines.append("")
    if refs:
        lines.append(f"  引用 {name} ({len(refs)} 处):")
        by_file = {}
        for r in refs:
            by_file.setdefault(r.file, []).append(r)
        for file in sorted(by_file):
            entries = by_file[file]
            lines.append(f"    {file}")
            for r in entries:
                ctx = _read_code_line(search_path, r.file, r.line)
                lines.append(f"      L{r.line}:{r.col}  {ctx}")
    else:
        lines.append(f"  ({name} 没有被其他代码调用)")
    return "\n".join(lines)


@tool
def code_dependencies(
    symbol: str,
    file: str = "",
    config: dict = {},
) -> str:
    """查询某符号的调用图——它调了谁和谁调了它。"""
    from paths import DEFAULT_WORKDIR
    cfg = config.get("configurable", {}) if config else {}
    base = Path(cfg.get("workdir") or str(DEFAULT_WORKDIR)).resolve()
    search_path = str((base / file).resolve()) if file and not os.path.isabs(file) else (file or str(base))
    indexer = get_indexer()
    root_path = search_path if os.path.isdir(search_path) else os.path.dirname(search_path)
    cache = indexer._ensure_index(root_path, "python")
    if not cache:
        return f"未找到代码"
    all_defs = indexer.search(symbol, search_path, kind="definition")
    if not all_defs:
        return f"未找到符号 {symbol!r} 的定义"
    def dn(e):
        return f"{e.parent}.{e.name}" if e.parent else e.name
    lines = [f"  {symbol} 的调用关系\n"]
    for d in all_defs:
        caller_name = dn(d)
        lines.append(f"  -- {caller_name}  定义于 {d.file}:L{d.line}")
        file_calls = cache.call_graph.get(d.file, {})
        callees = file_calls.get(caller_name, [])
        if callees:
            lines.append(f"     调用了 ({len(callees)}):")
            for c in sorted(set(callees)):
                lines.append(f"       -> {c}")
        else:
            lines.append(f"     没有直接调用其他函数")
        incoming = []
        for fname, fcg in cache.call_graph.items():
            for cc, cl in fcg.items():
                if d.name in cl:
                    ctx = _read_code_line(root_path, fname, _find_call_line(root_path, fname, d.name))
                    incoming.append((fname, f"{cc} -> {ctx}"))
        all_refs = indexer.search(d.name, root_path, kind="reference")
        seen = set()
        for ref in all_refs:
            key = (ref.file, ref.line)
            if key not in seen:
                seen.add(key)
                ctx = _read_code_line(root_path, ref.file, ref.line)
                incoming.append((ref.file, ctx))
        if incoming:
            lines.append(f"     被调用 ({len(incoming)}):")
            for fname, detail in incoming:
                lines.append(f"       <- {fname}  {detail}")
        else:
            lines.append(f"     没有被其他代码调用")
    return "\n".join(lines)
