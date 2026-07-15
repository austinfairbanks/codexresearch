from __future__ import annotations

import re
from collections.abc import Iterable, Iterator


def protected_markdown_lines(lines: Iterable[str]) -> Iterator[tuple[str, bool]]:
    """Yield whether each line is protected code/raw-HTML content.

    Fences follow the relevant CommonMark rules: up to three leading spaces,
    same delimiter character, and a closing run at least as long as its opener.
    """
    fence_char: str | None = None
    fence_length = 0
    html_tag: str | None = None
    for line in lines:
        if fence_char is not None:
            closing = re.match(r"^ {0,3}([`~]+)[ \t]*$", line)
            is_close = (
                closing is not None
                and set(closing.group(1)) == {fence_char}
                and len(closing.group(1)) >= fence_length
            )
            yield line, True
            if is_close:
                fence_char = None
                fence_length = 0
            continue
        if html_tag is not None:
            yield line, True
            if re.search(rf"</{html_tag}\s*>", line, flags=re.IGNORECASE):
                html_tag = None
            continue
        opening = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line)
        if opening is not None:
            run, info = opening.groups()
            # CommonMark forbids backticks in a backtick-fence info string.
            if run[0] == "~" or "`" not in info:
                fence_char = run[0]
                fence_length = len(run)
                yield line, True
                continue
        html_start = re.search(r"<(pre|code|script|style)(?:\s|>)", line, flags=re.IGNORECASE)
        if html_start:
            tag = html_start.group(1).lower()
            yield line, True
            if re.search(rf"</{tag}\s*>", line, flags=re.IGNORECASE) is None:
                html_tag = tag
            continue
        yield line, line.startswith("    ") or line.startswith("\t")
