#!/usr/bin/env python3
"""Run existing governance script main() functions in-process."""

from __future__ import annotations

import io
import sys
from contextlib import redirect_stderr, redirect_stdout
from typing import Callable


def invoke_main(
    main_func: Callable[[], int | None],
    argv: list[str],
    *,
    program: str,
) -> tuple[int, str]:
    old_argv = sys.argv
    buffer = io.StringIO()
    code = 0
    try:
        sys.argv = [program, *argv]
        with redirect_stdout(buffer), redirect_stderr(buffer):
            try:
                result = main_func()
                code = int(result or 0)
            except SystemExit as exc:
                value = exc.code
                code = int(value if isinstance(value, int) else 1)
    except Exception as exc:
        buffer.write(f"IN_PROCESS_SCRIPT_ERROR:{type(exc).__name__}:{exc}\n")
        code = 1
    finally:
        sys.argv = old_argv
    return code, buffer.getvalue()
