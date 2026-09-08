# -*- coding: utf-8 -*-
"""视觉 DeepSeek 的 OpenAI Chat Completions 兼容格式化器。"""
from __future__ import annotations

from copy import deepcopy

from agentscope.formatter import DeepSeekChatFormatter
from agentscope.message import Base64Source, DataBlock, HintBlock, Msg, TextBlock, URLSource


class HuiyanDeepSeekVisionFormatter(DeepSeekChatFormatter):
    """保留 DeepSeek 原生思考/工具结构，并补充 ``image_url`` 内容块。"""

    input_types: list[str] = ["text/plain", "image/*"]

    @staticmethod
    def _image_part(block: DataBlock) -> dict:
        source = block.source
        if isinstance(source, Base64Source):
            url = f"data:{source.media_type};base64,{source.data}"
        elif isinstance(source, URLSource) and str(source.url).startswith("https://"):
            url = str(source.url)
        else:
            raise ValueError("DeepSeek 视觉输入只接受临时 Base64 或公网 HTTPS 图片")
        return {"type": "image_url", "image_url": {"url": url}}

    async def format(self, msgs: list[Msg]) -> list[dict]:
        """用不可碰撞的文本占位复用原生格式化，再恢复为多模态内容块。"""
        copied = deepcopy(msgs)
        image_parts: dict[str, dict] = {}
        sequence = 0

        def replace_block(block):
            nonlocal sequence
            if not isinstance(block, DataBlock):
                return block
            marker = f"<huiyan-image-{sequence}-{block.id}>"
            sequence += 1
            image_parts[marker] = self._image_part(block)
            return TextBlock(text=marker)

        for msg in copied:
            msg.content = [replace_block(block) for block in msg.content]
            for block in msg.content:
                if isinstance(block, HintBlock) and isinstance(block.hint, list):
                    block.hint = [replace_block(child) for child in block.hint]

        formatted = await super().format(copied)
        for message in formatted:
            content = message.get("content")
            if not isinstance(content, str) or not any(marker in content for marker in image_parts):
                continue
            parts: list[dict] = []
            cursor = 0
            while cursor < len(content):
                matches = [(content.find(marker, cursor), marker) for marker in image_parts]
                matches = [(index, marker) for index, marker in matches if index >= 0]
                if not matches:
                    if content[cursor:]:
                        parts.append({"type": "text", "text": content[cursor:]})
                    break
                index, marker = min(matches, key=lambda item: item[0])
                if content[cursor:index]:
                    parts.append({"type": "text", "text": content[cursor:index]})
                parts.append(image_parts[marker])
                cursor = index + len(marker)
            message["content"] = parts
        return formatted


__all__ = ["HuiyanDeepSeekVisionFormatter"]
