from __future__ import annotations

import importlib
import pathlib
import re
import subprocess
import tempfile
from dataclasses import dataclass
from typing import Any

cindex = None
try:
    cindex = importlib.import_module("clang.cindex")
except Exception:
    pass

cst = None
try:
    cst = importlib.import_module("libcst")
except Exception:
    pass


@dataclass
class CFunctionSpan:
    name: str
    open_brace: int
    close_brace: int


def safe_read_text(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def safe_write_text(path: pathlib.Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def source_language(path: pathlib.Path) -> str:
    ext = path.suffix.lower()
    if ext == ".py":
        return "python"
    if ext in {".cpp", ".cxx", ".cc", ".hpp", ".hh", ".hxx"}:
        return "cpp"
    if ext in {".c", ".h"}:
        return "c"
    return "unknown"


def candidate_source_files(repo: pathlib.Path, language: str) -> list[pathlib.Path]:
    skips = {".git", "build", "dist", "venv", ".venv", "external", "install", "artifacts", "logs"}
    all_files: list[pathlib.Path] = []
    for path in repo.rglob("*"):
        if not path.is_file():
            continue
        if any(part in skips for part in path.parts):
            continue
        all_files.append(path)

    lang = language.lower()
    if lang == "python":
        return [path for path in all_files if source_language(path) == "python"]
    if lang in {"cpp", "c++"}:
        return [path for path in all_files if source_language(path) in {"cpp", "c"}]
    if lang == "c":
        return [path for path in all_files if source_language(path) == "c"]

    cpp_like = [path for path in all_files if source_language(path) in {"cpp", "c"}]
    if cpp_like:
        return cpp_like
    return [path for path in all_files if source_language(path) == "python"]


def find_matching_brace(text: str, open_idx: int) -> int | None:
    depth = 0
    i = open_idx
    while i < len(text):
        ch = text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return None


def include_insert_offset(text: str) -> int:
    match = re.search(r"^(#include\s+.+\n)+", text, flags=re.MULTILINE)
    if match:
        return match.end()
    return 0


def collect_c_functions_with_llvm(path: pathlib.Path, text: str) -> list[CFunctionSpan]:
    if cindex is None:
        return []

    suffix = path.suffix if path.suffix else ".c"
    tmp_name = ""
    spans: list[CFunctionSpan] = []
    try:
        with tempfile.NamedTemporaryFile("w", suffix=suffix, delete=False, encoding="utf-8") as tf:
            tf.write(text)
            tmp_name = tf.name

        lang = source_language(path)
        xarg = "c++" if lang == "cpp" else "c"
        std = "-std=c++17" if lang == "cpp" else "-std=c11"

        idx = cindex.Index.create()
        tu = idx.parse(tmp_name, args=["-x", xarg, std], options=cindex.TranslationUnit.PARSE_INCOMPLETE)

        function_kinds = {cindex.CursorKind.FUNCTION_DECL}
        for kind_name in ("CXX_METHOD", "CONSTRUCTOR", "DESTRUCTOR", "FUNCTION_TEMPLATE", "CONVERSION_FUNCTION"):
            kind = getattr(cindex.CursorKind, kind_name, None)
            if kind is not None:
                function_kinds.add(kind)

        for cur in tu.cursor.walk_preorder():
            if cur.kind not in function_kinds or not cur.is_definition():
                continue
            if cur.location.file is None or cur.location.file.name != tmp_name:
                continue

            start = max(0, int(cur.extent.start.offset))
            end = min(len(text), int(cur.extent.end.offset))
            if end <= start:
                continue

            snippet = text[start:end]
            rel_open = snippet.find("{")
            if rel_open < 0:
                continue
            open_idx = start + rel_open
            close_idx = find_matching_brace(text, open_idx)
            if close_idx is None or close_idx <= open_idx:
                continue

            spans.append(CFunctionSpan(name=cur.spelling, open_brace=open_idx, close_brace=close_idx))
    except Exception:
        return []
    finally:
        if tmp_name:
            try:
                pathlib.Path(tmp_name).unlink(missing_ok=True)
            except Exception:
                pass

    spans.sort(key=lambda span: span.open_brace)
    return spans


def entrypoint_function_names(path: pathlib.Path) -> set[str]:
    return {"main", f"{path.stem}_main"}


def detect_indent_unit(body: str) -> str:
    for line in body.splitlines():
        stripped = line.lstrip(" \t")
        if stripped:
            indent = line[: len(line) - len(stripped)]
            if indent:
                return indent
    return "    "


def indent_block(text: str, indent: str) -> str:
    return "\n".join(f"{indent}{line}" if line else "" for line in text.splitlines())


def strip_legacy_dftracer_code(body: str) -> str:
    updated = body
    patterns = [
        r"(?ms)^\s*if \(dftracer_init == 1\) \{\s*DFTRACER_(?:C|CPP)_FINI\(\);\s*dftracer_init = 0;\s*\}\s*",
        r"(?m)^\s*int dftracer_init = 1;\s*$\n?",
        r"(?m)^\s*DFTRACER_(?:C|CPP)_INIT(?:_NO_BIND)?\([^\n;]*\);\s*$\n?",
        r"(?m)^\s*DFTRACER_C_REGION_(?:START|END|UPDATE_[A-Z_]+)\([^\n;]*\);\s*$\n?",
        r"(?m)^\s*DFTRACER_C_FUNCTION_(?:START|END|UPDATE_[A-Z_]+)\([^\n;]*\);\s*$\n?",
        r"(?m)^\s*DFTRACER_CPP_FUNCTION(?:_UPDATE(?:_TYPE)?)?\([^\n;]*\);\s*$\n?",
    ]
    for pattern in patterns:
        updated = re.sub(pattern, "", updated)
    updated = strip_previous_return_wrappers(updated)
    return updated


def strip_previous_return_wrappers(body: str) -> str:
    updated = body
    patterns = [
        re.compile(
            r"(?ms)do \{\s*(?:if \(dftracer_init == 1\) \{\s*DFTRACER_C_FINI\(\);\s*dftracer_init = 0;\s*\}\s*)?"
            r"DFTRACER_C_FUNCTION_END\(\);\s*(return\s*.*?;)\s*\} while \(0\);"
        ),
        re.compile(r"(?ms)do \{\s*(return\s*.*?;)\s*\} while \(0\);"),
    ]
    changed = True
    while changed:
        changed = False
        for pattern in patterns:
            updated_next, count = pattern.subn(r"\1", updated)
            if count:
                updated = updated_next
                changed = True
    return updated


def normalize_entrypoint_finalize_section(body: str) -> str:
    updated = body
    patterns = [
        (
            re.compile(
                r"(?ms)(?P<indent>^[ \t]*)MPI_CHECK\(\s*(?:(?:if \(dftracer_init == 1\) \{.*?\}\s*)|(?:DFTRACER_C_FUNCTION_END\(\);\s*))*MPI_Finalize\(\)\s*,\s*\"(?P<msg>[^\"]*)\"\s*\);",
                re.MULTILINE,
            ),
            lambda m: f"{m.group('indent')}MPI_CHECK(MPI_Finalize(), \"{m.group('msg')}\");",
        ),
        (
            re.compile(
                r"(?ms)(?P<indent>^[ \t]*)(?:(?:if \(dftracer_init == 1\) \{.*?\}\s*)|(?:DFTRACER_C_FUNCTION_END\(\);\s*))*MPI_Finalize\(\);",
                re.MULTILINE,
            ),
            lambda m: f"{m.group('indent')}MPI_Finalize();",
        ),
    ]
    for pattern, repl in patterns:
        updated = pattern.sub(repl, updated)
    return updated


def find_keyword_positions(text: str, keyword: str) -> list[int]:
    positions: list[int] = []
    i = 0
    in_line_comment = False
    in_block_comment = False
    in_string: str | None = None
    while i < len(text):
        ch = text[i]
        nxt = text[i + 1] if i + 1 < len(text) else ""

        if in_line_comment:
            if ch == "\n":
                in_line_comment = False
            i += 1
            continue

        if in_block_comment:
            if ch == "*" and nxt == "/":
                in_block_comment = False
                i += 2
            else:
                i += 1
            continue

        if in_string is not None:
            if ch == "\\":
                i += 2
                continue
            if ch == in_string:
                in_string = None
            i += 1
            continue

        if ch == "/" and nxt == "/":
            in_line_comment = True
            i += 2
            continue
        if ch == "/" and nxt == "*":
            in_block_comment = True
            i += 2
            continue
        if ch in {'"', "'"}:
            in_string = ch
            i += 1
            continue

        if text.startswith(keyword, i):
            before = text[i - 1] if i > 0 else ""
            after = text[i + len(keyword)] if i + len(keyword) < len(text) else ""
            if (not before or not (before.isalnum() or before == "_")) and (not after or not (after.isalnum() or after == "_")):
                positions.append(i)
                i += len(keyword)
                continue
        i += 1
    return positions


def find_statement_end(text: str, start: int) -> int | None:
    i = start
    paren_depth = 0
    bracket_depth = 0
    brace_depth = 0
    in_line_comment = False
    in_block_comment = False
    in_string: str | None = None

    while i < len(text):
        ch = text[i]
        nxt = text[i + 1] if i + 1 < len(text) else ""

        if in_line_comment:
            if ch == "\n":
                in_line_comment = False
            i += 1
            continue

        if in_block_comment:
            if ch == "*" and nxt == "/":
                in_block_comment = False
                i += 2
            else:
                i += 1
            continue

        if in_string is not None:
            if ch == "\\":
                i += 2
                continue
            if ch == in_string:
                in_string = None
            i += 1
            continue

        if ch == "/" and nxt == "/":
            in_line_comment = True
            i += 2
            continue
        if ch == "/" and nxt == "*":
            in_block_comment = True
            i += 2
            continue
        if ch in {'"', "'"}:
            in_string = ch
            i += 1
            continue

        if ch == "(":
            paren_depth += 1
        elif ch == ")":
            paren_depth = max(0, paren_depth - 1)
        elif ch == "[":
            bracket_depth += 1
        elif ch == "]":
            bracket_depth = max(0, bracket_depth - 1)
        elif ch == "{":
            brace_depth += 1
        elif ch == "}":
            brace_depth = max(0, brace_depth - 1)
        elif ch == ";" and paren_depth == 0 and bracket_depth == 0 and brace_depth == 0:
            return i
        i += 1
    return None


def wrap_returns_with_cleanup(body: str, cleanup_block: str, max_start: int | None = None) -> tuple[str, int]:
    if "return" not in body:
        return body, 0

    updated = body
    wrapped = 0
    for start in reversed(find_keyword_positions(updated, "return")):
        if max_start is not None and start >= max_start:
            continue
        end = find_statement_end(updated, start)
        if end is None:
            continue
        stmt = updated[start : end + 1].strip()
        line_start = updated.rfind("\n", 0, start) + 1
        indent_match = re.match(r"[ \t]*", updated[line_start:start])
        base_indent = indent_match.group(0) if indent_match else ""
        inner_indent = base_indent + detect_indent_unit(updated)
        replacement = (
            f"do {{\n{indent_block(cleanup_block, inner_indent)}\n"
            f"{indent_block(stmt, inner_indent)}\n{base_indent}}} while (0);"
        )
        updated = updated[:start] + replacement + updated[end + 1 :]
        wrapped += 1
    return updated, wrapped


def insert_after_pattern(body: str, pattern: str, block: str, fallback: int) -> str:
    match = re.search(pattern, body)
    if match:
        pos = match.end()
        return body[:pos] + "\n" + block + body[pos:]
    return body[:fallback] + block + body[fallback:]


def insert_before_pattern(body: str, pattern: str, block: str, fallback: int) -> str:
    match = re.search(pattern, body)
    if match:
        pos = match.start()
        return body[:pos] + block + "\n" + body[pos:]
    return body[:fallback] + "\n" + block + body[fallback:]


def make_fini_guard(indent: str, fini_macro: str) -> str:
    step = "\t" if "\t" in indent else indent
    return (
        "if (dftracer_init == 1) {\n"
        f"{step}{fini_macro}\n"
        f"{step}dftracer_init = 0;\n"
        "}"
    )


def ensure_entrypoint_init(body: str, indent: str, init_macro: str) -> tuple[str, bool]:
    if init_macro in body:
        return body, False

    init_block = f"{indent}int dftracer_init = 1;\n{indent}{init_macro}"
    fallback = body.find("\n") + 1 if "\n" in body else 0
    updated = insert_after_pattern(body, r"\bMPI_Init(?:_thread)?\s*\([^;]*\)\s*;", init_block, fallback)
    return updated, True


def ensure_entrypoint_fallthrough_fini(body: str, indent: str, fini_guard: str) -> tuple[str, bool]:
    mpi_finalize = re.search(r"\bMPI_Finalize\s*\([^;]*\)\s*;", body)
    if mpi_finalize:
        preceding = body[max(0, mpi_finalize.start() - 512) : mpi_finalize.start()]
        if "dftracer_init == 1" in preceding:
            return body, False
        return insert_before_pattern(body, r"\bMPI_Finalize\s*\([^;]*\)\s*;", indent_block(fini_guard, indent), len(body)), True

    stripped = body.rstrip()
    if stripped.endswith(indent_block(fini_guard, indent)):
        return body, False
    return body.rstrip() + "\n" + indent_block(fini_guard, indent) + "\n", True


def ensure_entrypoint_function_start(body: str, indent: str, start_macro: str) -> tuple[str, bool]:
    if start_macro in body:
        return body, False

    init_pattern = r"\bDFTRACER_(?:C|CPP)_INIT(?:_NO_BIND)?\([^;]*\)\s*;"
    fallback = body.find("\n") + 1 if "\n" in body else 0
    updated = insert_after_pattern(body, init_pattern, f"{indent}{start_macro}", fallback)
    return updated, True


def ensure_entrypoint_fallthrough_cleanup(body: str, indent: str, cleanup_block: str) -> tuple[str, bool]:
    mpi_finalize = find_finalize_statement(body)
    rendered = indent_block(cleanup_block, indent)
    if mpi_finalize:
        preceding = body[max(0, mpi_finalize.start() - max(512, len(rendered) + 32)) : mpi_finalize.start()]
        if rendered.strip() in preceding:
            return body, False
        return body[: mpi_finalize.start()] + rendered + "\n" + body[mpi_finalize.start() :], True

    stripped = body.rstrip()
    if stripped.endswith(rendered):
        return body, False
    return stripped + "\n" + rendered + "\n", True


def find_finalize_statement(body: str) -> re.Match[str] | None:
    patterns = [
        re.compile(r"(?m)^\s*MPI_CHECK\s*\(\s*MPI_Finalize\s*\([^)]*\)\s*,\s*\"[^\"]*\"\s*\)\s*;"),
        re.compile(r"(?m)^\s*MPI_Finalize\s*\([^;]*\)\s*;"),
    ]
    for pattern in patterns:
        match = pattern.search(body)
        if match:
            return match
    return None


def instrument_c_or_cpp_function(path: pathlib.Path, fn_name: str, body: str, lang: str) -> tuple[str, list[str]]:
    changes: list[str] = []
    is_entrypoint = fn_name in entrypoint_function_names(path)
    indent = detect_indent_unit(body)
    function_macro_present = "DFTRACER_C_FUNCTION_START()" in body if lang == "c" else "DFTRACER_CPP_FUNCTION()" in body
    init_macro = "DFTRACER_C_INIT(nullptr, nullptr, nullptr);" if lang == "c" else "DFTRACER_CPP_INIT(nullptr, nullptr, nullptr);"
    fini_macro = "DFTRACER_C_FINI();" if lang == "c" else "DFTRACER_CPP_FINI();"
    fini_guard = make_fini_guard(indent, fini_macro)

    updated = strip_legacy_dftracer_code(body)
    if is_entrypoint:
        updated = normalize_entrypoint_finalize_section(updated)

    if is_entrypoint:
        updated, init_added = ensure_entrypoint_init(updated, indent, init_macro)
        if init_added:
            changes.append(f"added DFTracer init in {fn_name}()")

        start_macro = "DFTRACER_C_FUNCTION_START();" if lang == "c" else "DFTRACER_CPP_FUNCTION();"
        updated, start_added = ensure_entrypoint_function_start(updated, indent, start_macro)
        if start_added:
            changes.append(f"added function tracer in {fn_name}()")

        if lang == "c":
            entry_cleanup = f"DFTRACER_C_FUNCTION_END();\n{fini_guard}"
            finalize_match = find_finalize_statement(updated)
            max_return_start = finalize_match.start() if finalize_match else None
            updated, wrapped_returns = wrap_returns_with_cleanup(updated, entry_cleanup, max_return_start)
            updated, cleanup_added = ensure_entrypoint_fallthrough_cleanup(updated, indent, entry_cleanup)
            if cleanup_added:
                changes.append(f"added function cleanup in {fn_name}()")
            if finalize_match is None:
                if wrapped_returns == 0 and not updated.rstrip().endswith(indent_block(entry_cleanup, indent)):
                    updated = updated.rstrip() + "\n" + indent_block(entry_cleanup, indent) + "\n"
                    changes.append(f"added DFTracer fini in {fn_name}()")
            else:
                changes.append(f"added DFTracer fini in {fn_name}()")
            return updated, changes

        updated, wrapped_returns = wrap_returns_with_cleanup(updated, fini_guard)
        updated, fini_added = ensure_entrypoint_fallthrough_fini(updated, indent, fini_guard)
        if fini_added:
            changes.append(f"added DFTracer fini in {fn_name}()")
        return updated, changes

    if not function_macro_present:
        if lang == "c":
            prologue = f"\n{indent}DFTRACER_C_FUNCTION_START();\n"
        else:
            prologue = f"\n{indent}DFTRACER_CPP_FUNCTION();\n"
        updated = prologue + updated.lstrip("\n")
        changes.append(f"added function tracer in {fn_name}()")

    if lang == "c":
        cleanup = "DFTRACER_C_FUNCTION_END();"
        updated, wrapped_returns = wrap_returns_with_cleanup(updated, cleanup)

        tail_cleanup = "DFTRACER_C_FUNCTION_END();"
        tail_block = indent_block(tail_cleanup, indent)
        if wrapped_returns == 0 and not updated.rstrip().endswith(tail_block):
            updated = updated.rstrip() + "\n" + tail_block + "\n"
            changes.append(f"added function cleanup in {fn_name}()")

    return updated, changes


def remove_stale_region_annotations(path: pathlib.Path, text: str) -> tuple[str, bool, list[str]]:
    if source_language(path) != "c":
        return text, False, []

    new_text = re.sub(
        r"(?m)^\s*DFTRACER_C_REGION_(?:START|END|UPDATE_[A-Z_]+)\([^\n;]*\);\s*$\n?",
        "",
        text,
    )
    if new_text == text:
        return text, False, []
    return new_text, True, ["removed stale DFTRACER_C_REGION annotations"]


def inject_cpp_or_c_annotations(path: pathlib.Path, text: str) -> tuple[str, bool, list[str]]:
    lang = source_language(path)
    changes: list[str] = []
    new_text = text

    include_line = "#include <dftracer/dftracer.h>"
    if include_line not in new_text:
        off = include_insert_offset(new_text)
        new_text = new_text[:off] + include_line + "\n" + new_text[off:]
        changes.append("added dftracer include")

    if lang == "c" and "#define nullptr" not in new_text:
        compat_block = "#ifndef __cplusplus\n#ifndef nullptr\n#define nullptr ((void*)0)\n#endif\n#endif\n"
        off = include_insert_offset(new_text)
        new_text = new_text[:off] + compat_block + new_text[off:]
        changes.append("added C nullptr compatibility define")

    spans = collect_c_functions_with_llvm(path, new_text)
    if spans:
        for span in reversed(spans):
            body_start = span.open_brace + 1
            body_end = span.close_brace
            body = new_text[body_start:body_end]
            updated_body, fn_changes = instrument_c_or_cpp_function(path, span.name, body, lang)
            if updated_body != body:
                new_text = new_text[:body_start] + updated_body + new_text[body_end:]
                changes.extend(fn_changes)

    return new_text, (new_text != text), changes


def inject_c_hotpath_regions(path: pathlib.Path, text: str) -> tuple[str, bool, list[str]]:
    return text, False, []


def inject_python_annotations(path: pathlib.Path, text: str) -> tuple[str, bool, list[str]]:
    if cst is None:
        return text, False, []

    try:
        module = cst.parse_module(text)
    except Exception:
        return text, False, []

    changes: list[str] = []
    body = list(module.body)

    has_df_import = False
    for stmt in body:
        if isinstance(stmt, cst.ImportFrom):
            mod = stmt.module
            if isinstance(mod, cst.Attribute):
                mod_code = module.code_for_node(mod)
            elif isinstance(mod, cst.Name):
                mod_code = mod.value
            else:
                mod_code = ""
            if mod_code == "dftracer.python":
                has_df_import = True
                break

    if not has_df_import:
        import_stmt = cst.parse_statement("from dftracer.python import dftracer, dft_fn\n")
        insert_at = 0
        for i, stmt in enumerate(body):
            if isinstance(stmt, (cst.Import, cst.ImportFrom)):
                insert_at = i + 1
            else:
                break
        body.insert(insert_at, import_stmt)
        changes.append("added dftracer python imports")

    if "dftracer.initialize_log(" not in text:
        init_stmt = cst.parse_statement("log_inst = dftracer.initialize_log(logfile=None, data_dir=None, process_id=-1)\n")
        insert_at = 0
        for i, stmt in enumerate(body):
            if isinstance(stmt, (cst.Import, cst.ImportFrom)):
                insert_at = i + 1
            else:
                break
        body.insert(insert_at, init_stmt)
        changes.append("added dftracer.initialize_log")

    decorated = 0
    max_auto = 8
    updated_body: list[Any] = []
    for stmt in body:
        if isinstance(stmt, cst.FunctionDef) and stmt.name.value == "main":
            main_code = module.code_for_node(stmt)
            if "log_inst.finalize(" not in main_code and isinstance(stmt.body, cst.IndentedBlock):
                finalize_stmt = cst.parse_statement("log_inst.finalize()\n")
                fn_body = list(stmt.body.body)
                insert_at = len(fn_body)
                for i in range(len(fn_body) - 1, -1, -1):
                    line = fn_body[i]
                    if isinstance(line, cst.SimpleStatementLine) and any(isinstance(node, cst.Return) for node in line.body):
                        insert_at = i
                        break
                fn_body.insert(insert_at, finalize_stmt)
                stmt = stmt.with_changes(body=stmt.body.with_changes(body=fn_body))
                changes.append("added log_inst.finalize() in main")

        if isinstance(stmt, cst.FunctionDef) and decorated < max_auto:
            fn = stmt.name.value
            has_decorator = any("dft_fn(" in module.code_for_node(dec.decorator) for dec in stmt.decorators)
            if not fn.startswith("_") and not has_decorator:
                dec = cst.Decorator(decorator=cst.parse_expression(f'dft_fn("{fn.upper()}").log'))
                stmt = stmt.with_changes(decorators=[*stmt.decorators, dec])
                decorated += 1
        updated_body.append(stmt)

    if decorated:
        changes.append(f"added decorators to {decorated} function(s)")

    new_module = module.with_changes(body=updated_body)
    new_text = new_module.code
    return new_text, (new_text != text), changes


def patch_build_linking(repo: pathlib.Path) -> dict[str, Any]:
    out: dict[str, Any] = {"modified": [], "notes": []}

    cmake = list(repo.rglob("CMakeLists.txt"))
    has_autotools = (repo / "configure.ac").exists()
    marker = "# DFTRACER_AUTO_LINK"
    cmake_block = "\n".join(
        [
            marker,
            "if(NOT DEFINED DFTRACER_INSTALL_DIR)",
            "  set(DFTRACER_INSTALL_DIR \"$ENV{DFTRACER_INSTALL_DIR}\")",
            "endif()",
            "if(DFTRACER_INSTALL_DIR)",
            "  include_directories(${DFTRACER_INSTALL_DIR}/include)",
            "  link_directories(${DFTRACER_INSTALL_DIR}/lib)",
            "  link_libraries(dftracer_core)",
            "endif()",
            "",
        ]
    )
    for path in cmake:
        text = safe_read_text(path)
        if marker in text:
            continue
        safe_write_text(path, text + ("\n" if not text.endswith("\n") else "") + cmake_block)
        out["modified"].append(str(path))

    configure_ac = repo / "configure.ac"
    ac_marker = "# DFTRACER_AUTO_LINK"
    ac_block = "\n".join(
        [
            ac_marker,
            "AC_ARG_VAR([DFTRACER_INSTALL_DIR], [DFTracer install prefix])",
            "AC_ARG_VAR([DFTRACER_INCLUDE_DIR], [DFTracer include directory override])",
            "AC_ARG_VAR([DFTRACER_LIBRARY_DIR], [DFTracer library directory override])",
            "AS_IF([test -z \"$DFTRACER_INSTALL_DIR\" -a \"x$prefix\" != \"xNONE\"], [",
            "  DFTRACER_INSTALL_DIR=\"$prefix\"",
            "])",
            "AS_IF([test -z \"$DFTRACER_INCLUDE_DIR\" -a -n \"$DFTRACER_INSTALL_DIR\"], [",
            "  for _d in $DFTRACER_INSTALL_DIR/lib64/python*/site-packages/dftracer/include $DFTRACER_INSTALL_DIR/lib/python*/site-packages/dftracer/include \"$DFTRACER_INSTALL_DIR/include\"; do",
            "    AS_IF([test -d \"$_d\"], [DFTRACER_INCLUDE_DIR=\"$_d\"; break])",
            "  done",
            "])",
            "AS_IF([test -z \"$DFTRACER_LIBRARY_DIR\" -a -n \"$DFTRACER_INSTALL_DIR\"], [",
            "  for _d in $DFTRACER_INSTALL_DIR/lib64/python*/site-packages/dftracer/lib64 $DFTRACER_INSTALL_DIR/lib/python*/site-packages/dftracer/lib64 \"$DFTRACER_INSTALL_DIR/lib64\" \"$DFTRACER_INSTALL_DIR/lib\"; do",
            "    AS_IF([test -d \"$_d\"], [DFTRACER_LIBRARY_DIR=\"$_d\"; break])",
            "  done",
            "])",
            "AS_IF([test -n \"$DFTRACER_INSTALL_DIR\" -o -n \"$DFTRACER_INCLUDE_DIR\" -o -n \"$DFTRACER_LIBRARY_DIR\"], [",
            "  DFTRACER_CPPFLAGS=\"\"",
            "  AS_IF([test -n \"$DFTRACER_INCLUDE_DIR\"], [DFTRACER_CPPFLAGS=\"-I$DFTRACER_INCLUDE_DIR\"])",
            "  DFTRACER_LDFLAGS=\"\"",
            "  AS_IF([test -n \"$DFTRACER_LIBRARY_DIR\"], [DFTRACER_LDFLAGS=\"-L$DFTRACER_LIBRARY_DIR -Wl,-rpath,$DFTRACER_LIBRARY_DIR\"])",
            "  DFTRACER_LIBS=\"\"",
            "  AS_IF([test -n \"$DFTRACER_LIBRARY_DIR\" -a -e \"$DFTRACER_LIBRARY_DIR/libdftracer_core.so\"], [",
            "    DFTRACER_LIBS=\"-ldftracer_core\"",
            "  ], [",
            "    AS_IF([test -n \"$DFTRACER_LIBRARY_DIR\" -a -e \"$DFTRACER_LIBRARY_DIR/libdftracer.so\"], [",
            "      DFTRACER_LIBS=\"-ldftracer\"",
            "    ], [",
            "      DFTRACER_LIBS=\"-ldftracer_core\"",
            "    ])",
            "  ])",
            "], [",
            "  DFTRACER_CPPFLAGS=\"\"",
            "  DFTRACER_LDFLAGS=\"\"",
            "  DFTRACER_LIBS=\"\"",
            "])",
            "AC_SUBST([DFTRACER_CPPFLAGS])",
            "AC_SUBST([DFTRACER_LDFLAGS])",
            "AC_SUBST([DFTRACER_LIBS])",
            "CPPFLAGS=\"$CPPFLAGS $DFTRACER_CPPFLAGS\"",
            "LDFLAGS=\"$LDFLAGS $DFTRACER_LDFLAGS\"",
            "LIBS=\"$LIBS $DFTRACER_LIBS\"",
            "",
        ]
    )
    if configure_ac.exists():
        text = safe_read_text(configure_ac)
        if ac_marker in text:
            text = text.split(ac_marker, 1)[0].rstrip() + "\n"

        ac_output_match = re.search(r"(?m)^\s*AC_OUTPUT\b", text)
        if ac_output_match:
            insert_at = ac_output_match.start()
            prefix = text[:insert_at].rstrip() + "\n\n"
            suffix = text[insert_at:].lstrip("\n")
            new_text = prefix + ac_block + "\n" + suffix
        else:
            new_text = text + ("\n" if not text.endswith("\n") else "") + ac_block

        safe_write_text(configure_ac, new_text)
        out["modified"].append(str(configure_ac))

    makefile_ams = list(repo.rglob("Makefile.am"))
    am_marker = "# DFTRACER_AUTO_LINK"
    for path in makefile_ams:
        original = safe_read_text(path)
        text = original
        if am_marker in text:
            text = text.split(am_marker, 1)[0].rstrip() + "\n"

        if has_autotools:
            if text != original:
                safe_write_text(path, text)
                out["modified"].append(str(path))
            continue

        has_am_cppflags = bool(re.search(r"(?m)^\s*AM_CPPFLAGS\s*[+:]?=", text))
        has_am_ldflags = bool(re.search(r"(?m)^\s*AM_LDFLAGS\s*[+:]?=", text))
        has_ldadd = bool(re.search(r"(?m)^\s*LDADD\s*[+:]?=", text))

        am_cpp_line = "AM_CPPFLAGS += $(DFTRACER_CPPFLAGS)" if has_am_cppflags else "AM_CPPFLAGS = $(DFTRACER_CPPFLAGS)"
        am_ldflags_line = "AM_LDFLAGS += $(DFTRACER_LDFLAGS)" if has_am_ldflags else "AM_LDFLAGS = $(DFTRACER_LDFLAGS)"
        ldadd_line = "LDADD += $(DFTRACER_LIBS)" if has_ldadd else "LDADD = $(DFTRACER_LIBS)"

        am_block = "\n".join(
            [
                am_marker,
                am_cpp_line,
                am_ldflags_line,
                ldadd_line,
                "",
            ]
        )

        new_text = text + ("\n" if not text.endswith("\n") else "") + am_block
        if new_text != original:
            safe_write_text(path, new_text)
            out["modified"].append(str(path))

    makefiles = [path for path in repo.rglob("Makefile") if path.is_file()]
    mk_marker = "# DFTRACER_AUTO_LINK"
    mk_block = "\n".join(
        [
            mk_marker,
            "DFTRACER_INSTALL_DIR ?= $(HOME)/dftracer",
            "CPPFLAGS += -I$(DFTRACER_INSTALL_DIR)/include",
            "LDFLAGS += -L$(DFTRACER_INSTALL_DIR)/lib -Wl,-rpath,$(DFTRACER_INSTALL_DIR)/lib",
            "LDLIBS += -ldftracer_core",
            "",
        ]
    )
    for path in makefiles:
        text = safe_read_text(path)
        if mk_marker in text:
            continue
        safe_write_text(path, text + ("\n" if not text.endswith("\n") else "") + mk_block)
        out["modified"].append(str(path))

    out["notes"].append("Set DFTRACER_INSTALL_DIR to the dftracer install prefix before building.")
    out["notes"].append("For autotools repositories, configure.ac is the authoritative place for DFTracer flags; Makefile.am marker blocks are removed to avoid automake regressions.")
    out["notes"].append("For manual builds, add -I${DFTRACER_INSTALL_DIR}/include -L${DFTRACER_INSTALL_DIR}/lib -ldftracer_core.")
    return out


def git_diff_patch(repo: pathlib.Path) -> dict[str, Any]:
    if not (repo / ".git").exists():
        return {"ok": False, "error": "repo_dir is not a git repository", "patch": ""}

    result = subprocess.run(
        ["git", "-C", str(repo), "diff", "--", "."],
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        return {"ok": False, "error": result.stderr.strip() or "git diff failed", "patch": ""}

    patch_text = result.stdout or ""
    files = []
    for line in patch_text.splitlines():
        if line.startswith("+++ b/"):
            files.append(line.replace("+++ b/", "", 1).strip())

    return {
        "ok": True,
        "error": "",
        "patch": patch_text,
        "files": sorted(set(files)),
        "line_count": len(patch_text.splitlines()),
    }