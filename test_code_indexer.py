"""code_indexer 符号名提取的回归测试。

护栏：tree-sitter 的 ``start_byte`` / ``end_byte`` 是【字节】偏移，而 ``_walk_py`` /
``_walk_js`` 收到的 ``code`` 是 ``str``。当符号前面有非 ASCII 内容（本仓库满是
中文注释）时，按字节切 str 会错位，提取出乱码符号名 —— 曾导致 code_search /
code_outline / code_references / code_dependencies 全面失灵（"未找到符号"）。

修复是改用 ``node.text.decode("utf-8")``。本测试钉死"符号前有中文时，名字仍被
正确提取"，防止回归。

可直接 ``python test_code_indexer.py`` 跑，也可 ``pytest test_code_indexer.py``。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tree_sitter import Language, Parser  # noqa: E402
import tree_sitter_python  # noqa: E402
import tree_sitter_javascript  # noqa: E402

from tools.code_indexer import CodeIndexer  # noqa: E402


def _walk(code: str, lang: str):
    """解析 code 并跑对应 walker，返回 (symbols, outlines)。"""
    if lang == "js":
        parser = Parser(Language(tree_sitter_javascript.language()))
    else:
        parser = Parser(Language(tree_sitter_python.language()))
    tree = parser.parse(bytes(code, "utf-8"))
    idx = CodeIndexer()
    symbols: list = []
    outlines: list = []
    walker = idx._walk_js if lang == "js" else idx._walk_py
    walker(tree.root_node, code, f"snippet.{lang}", symbols, outlines, file_calls={})
    return symbols, outlines


def _def_names(symbols) -> set:
    return {s.name for s in symbols if s.kind == "definition"}


def test_py_function_name_after_chinese_comment():
    # 注释占了 >1 字节/字，函数名是纯 ASCII —— 正是 bug 报告里的最小复现
    code = "# 中文注释占了很多字节\ndef hello_world():\n    pass\n"
    symbols, outlines = _walk(code, "py")
    assert _def_names(symbols) == {"hello_world"}
    assert [o.name for o in outlines] == ["hello_world"]


def test_py_class_and_method_after_chinese_comment():
    code = (
        "# 头部中文注释占字节\n"
        "class DataLoader:  # 类名后也有中文注释\n"
        "    # 方法前的中文注释\n"
        "    def read_batch(self):\n"
        "        pass\n"
    )
    symbols, _ = _walk(code, "py")
    names = _def_names(symbols)
    assert "DataLoader" in names
    assert "read_batch" in names


def test_py_unicode_identifier():
    # 更强护栏：符号名本身是中文（多字节），node.text.decode 必须完整还原
    code = "# 注释\ndef 读取数据():\n    pass\n"
    symbols, _ = _walk(code, "py")
    assert "读取数据" in _def_names(symbols)


def test_js_function_name_after_chinese_comment():
    code = "// 中文注释占了很多字节\nfunction helloWorld() {}\n"
    symbols, _ = _walk(code, "js")
    assert "helloWorld" in _def_names(symbols)


def test_js_class_after_chinese_comment():
    code = "// 头部中文\nclass DataLoader {\n  // 方法前中文\n  readBatch() {}\n}\n"
    symbols, _ = _walk(code, "js")
    names = _def_names(symbols)
    assert "DataLoader" in names
    assert "readBatch" in names


if __name__ == "__main__":
    import traceback

    failed = 0
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            try:
                _fn()
                print(f"PASS  {_name}")
            except Exception:
                failed += 1
                print(f"FAIL  {_name}")
                traceback.print_exc()
    sys.exit(1 if failed else 0)
