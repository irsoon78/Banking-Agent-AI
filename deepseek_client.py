"""DeepSeek HTTP 兼容配置；Agent 和本地参数校验保持原样。"""

import json

import httpx


def compatible_schema(value):
    """DeepSeek strict schema 不接受字符串长度关键字；本地仍完整校验。"""
    if isinstance(value, list):
        return [compatible_schema(item) for item in value]
    if isinstance(value, dict):
        return {
            key: compatible_schema(item)
            for key, item in value.items()
            if key not in {"minLength", "maxLength"}
        }
    return value


class DeepSeekHTTPClient(httpx.AsyncClient):
    """通过 OpenAI SDK 支持的 http_client 扩展点适配官方接口。"""

    async def send(self, request: httpx.Request, **kwargs) -> httpx.Response:
        if request.method == "POST" and request.url.path.endswith("/chat/completions"):
            payload = json.loads(await request.aread())
            # 使用非思考模式，保持原来的消息上下文和工具调用流程。
            payload["thinking"] = {"type": "disabled"}
            for message in payload.get("messages", []):
                if message.get("role") == "assistant":
                    message.setdefault("content", None)
            # 官方接口未定义该 OpenAI 参数；Agent 本地仍拒绝多个工具调用。
            payload.pop("parallel_tool_calls", None)
            for tool in payload.get("tools", []):
                function = tool.get("function", {})
                if "parameters" in function:
                    function["parameters"] = compatible_schema(function["parameters"])
            headers = httpx.Headers(request.headers)
            # 请求 JSON 重新编码后，由 HTTPX 重新计算 body 长度。
            headers.pop("Content-Length", None)
            request = httpx.Request(
                method=request.method,
                url=request.url,
                headers=headers,
                json=payload,
                extensions=request.extensions,
            )
        return await super().send(request, **kwargs)
