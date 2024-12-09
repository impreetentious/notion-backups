from __future__ import annotations

from typing import Any

def page_title(page: dict[str, Any]) -> str:
    properties = page.get("properties", {})
    for prop in properties.values():
        if prop.get("type") == "title":
            text = rich_text_to_markdown(prop.get("title", []))
            return text or "Untitled"
    return "Untitled"


def rich_text_to_markdown(items: list[dict[str, Any]]) -> str:
    rendered: list[str] = []
    for item in items:
        text = item.get("plain_text", "")
        href = item.get("href")
        annotations = item.get("annotations", {})
        if annotations.get("code"):
            text = f"`{text}`"
        if annotations.get("bold"):
            text = f"**{text}**"
        if annotations.get("italic"):
            text = f"*{text}*"
        if annotations.get("strikethrough"):
            text = f"~~{text}~~"
        if href:
            text = f"[{text}]({href})"
        rendered.append(text)
    return "".join(rendered)


def blocks_to_markdown(blocks: list[dict[str, Any]], depth: int = 0) -> str:
    lines: list[str] = []
    for index, block in enumerate(blocks, start=1):
        lines.extend(_block_to_lines(block, depth, index))
        children = block.get("children", [])
        if children:
            child_markdown = blocks_to_markdown(children, depth + 1)
            if child_markdown:
                lines.append(child_markdown.rstrip())
        if block.get("type") == "toggle":
            lines.append(f"{'  ' * depth}</details>")
        if lines and lines[-1] != "":
            lines.append("")
    return "\n".join(lines).rstrip() + ("\n" if lines else "")


def _block_to_lines(block: dict[str, Any], depth: int, index: int) -> list[str]:
    block_type = block.get("type")
    data = block.get(block_type, {}) if block_type else {}
    indent = "  " * depth

    if block_type == "paragraph":
        return _paragraph_lines(rich_text_to_markdown(data.get("rich_text", [])), indent)
    if block_type == "heading_1":
        return [f"# {rich_text_to_markdown(data.get('rich_text', []))}"]
    if block_type == "heading_2":
        return [f"## {rich_text_to_markdown(data.get('rich_text', []))}"]
    if block_type == "heading_3":
        return [f"### {rich_text_to_markdown(data.get('rich_text', []))}"]
    if block_type == "bulleted_list_item":
        return [f"{indent}- {rich_text_to_markdown(data.get('rich_text', []))}"]
    if block_type == "numbered_list_item":
        return [f"{indent}{index}. {rich_text_to_markdown(data.get('rich_text', []))}"]
    if block_type == "to_do":
        checked = "x" if data.get("checked") else " "
        return [f"{indent}- [{checked}] {rich_text_to_markdown(data.get('rich_text', []))}"]
    if block_type == "toggle":
        return [f"{indent}<details>", f"{indent}<summary>{rich_text_to_markdown(data.get('rich_text', []))}</summary>", ""]
    if block_type == "quote":
        text = rich_text_to_markdown(data.get("rich_text", []))
        return [f"{indent}> {line}" for line in text.splitlines()] if text else [f"{indent}>"]
    if block_type == "callout":
        icon = data.get("icon", {})
        icon_text = icon.get("emoji", "") if icon.get("type") == "emoji" else ""
        text = rich_text_to_markdown(data.get("rich_text", []))
        return [f"{indent}> {icon_text} {text}".rstrip()]
    if block_type == "code":
        language = data.get("language") or ""
        code = rich_text_to_markdown(data.get("rich_text", []))
        return [f"```{language}", code, "```"]
    if block_type == "divider":
        return ["---"]
    if block_type == "child_page":
        title = data.get("title", "Untitled child page")
        return [f"{indent}- Child page: `{title}` (`{block.get('id')}`)"]
    if block_type == "child_database":
        title = data.get("title", "Untitled child database")
        return [f"{indent}- Child database: `{title}` (`{block.get('id')}`)"]
    if block_type in {"image", "file", "pdf", "video", "audio"}:
        url = _file_url(data)
        caption = rich_text_to_markdown(data.get("caption", []))
        label = caption or block_type
        return [f"{indent}[{label}]({url})" if url else f"{indent}[{label}]"]
    if block_type == "bookmark":
        return [f"{indent}{data.get('url', '')}"]
    if block_type == "equation":
        return [f"{indent}$$", data.get("expression", ""), f"{indent}$$"]
    if block_type == "table":
        return [f"{indent}[Table block preserved in JSON: `{block.get('id')}`]"]
    if block_type == "table_row":
        cells = [" ".join(rich_text_to_markdown(cell) for cell in data.get("cells", []))]
        return [f"{indent}| {' | '.join(cells)} |"]

    return [f"{indent}[Unsupported Notion block `{block_type}` preserved in JSON: `{block.get('id')}`]"]


def _paragraph_lines(text: str, indent: str) -> list[str]:
    if not text:
        return []
    return [f"{indent}{line}" if line else "" for line in text.splitlines()]


def _file_url(data: dict[str, Any]) -> str | None:
    if data.get("type") == "external":
        return data.get("external", {}).get("url")
    if data.get("type") == "file":
        return data.get("file", {}).get("url")
    return None