"""网络工具 - Web 搜索与页面抓取。"""

from __future__ import annotations

import os
from typing import Any

from emoticorebot.tools.base import Tool


class WebSearchTool(Tool):
    """通过 Brave Search API 搜索网页"""

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.environ.get("BRAVE_API_KEY", "")

    @property
    def name(self) -> str:
        return "web_search"

    @property
    def description(self) -> str:
        return "Search the web using Brave Search API. Returns top results with title, URL, and snippet."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "count": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 20,
                    "description": "Number of results (default 5)",
                },
            },
            "required": ["query"],
        }

    async def execute(self, query: str, count: int = 5, **kwargs: Any) -> str:
        if not self.api_key:
            return "Error: BRAVE_API_KEY not configured"
        try:
            import httpx

            url = "https://api.search.brave.com/res/v1/web/search"
            headers = {
                "Accept": "application/json",
                "Accept-Encoding": "gzip",
                "X-Subscription-Token": self.api_key,
            }
            params = {"q": query, "count": min(count, 20)}
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(url, headers=headers, params=params)
                resp.raise_for_status()
                data = resp.json()

            results = data.get("web", {}).get("results", [])
            if not results:
                return "No results found"

            lines = []
            for i, r in enumerate(results, 1):
                title = r.get("title", "")
                link = r.get("url", "")
                snippet = r.get("description", "")
                lines.append(f"{i}. {title}\n   {link}\n   {snippet}")
            return "\n\n".join(lines)
        except Exception as e:
            return f"Error searching web: {e}"


class WebFetchTool(Tool):
    """获取网页内容并提取纯文本"""

    @property
    def name(self) -> str:
        return "web_fetch"

    @property
    def description(self) -> str:
        return "Fetch a web page and return its text content."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL to fetch"},
            },
            "required": ["url"],
        }

    async def execute(self, url: str, **kwargs: Any) -> str:
        try:
            import httpx

            headers = {"User-Agent": "Mozilla/5.0 (compatible; NanoBot/1.0)"}
            async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
                resp = await client.get(url, headers=headers)
                resp.raise_for_status()
                content_type = resp.headers.get("content-type", "")
                text = resp.text

            if "html" in content_type:
                try:
                    from html.parser import HTMLParser

                    class TextExtractor(HTMLParser):
                        def __init__(self):
                            super().__init__()
                            self._skip = False
                            self.parts: list[str] = []

                        def handle_starttag(self, tag, attrs):
                            if tag in ("script", "style", "noscript"):
                                self._skip = True

                        def handle_endtag(self, tag):
                            if tag in ("script", "style", "noscript"):
                                self._skip = False

                        def handle_data(self, data):
                            if not self._skip:
                                stripped = data.strip()
                                if stripped:
                                    self.parts.append(stripped)

                    extractor = TextExtractor()
                    extractor.feed(text)
                    text = "\n".join(extractor.parts)
                except Exception:
                    pass

            max_chars = 20000
            if len(text) > max_chars:
                text = text[:max_chars] + f"\n\n[...truncated at {max_chars} chars]"
            return text or "(empty page)"
        except Exception as e:
            return f"Error fetching {url}: {e}"


__all__ = ["WebSearchTool", "WebFetchTool"]
