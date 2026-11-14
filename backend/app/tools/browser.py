from __future__ import annotations

import os
import time
from urllib.parse import urljoin, urlparse

from playwright.async_api import async_playwright, Browser, Page, Playwright

from ..config import settings

CHROMIUM_PATH = "/opt/pw-browsers/chromium"


class BrowserSession:
    """A persistent browser for one agent task. Records network traffic so the
    agent can discover endpoints and JS files."""

    def __init__(self, task_id: int):
        self.task_id = task_id
        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        self._page: Page | None = None
        self.network: list[dict] = []
        self.js_urls: set[str] = set()
        self.available = False
        self.start_error = ""

    async def start(self) -> None:
        self._pw = await async_playwright().start()
        launch_kwargs = {"headless": settings.headless, "args": ["--no-sandbox", "--disable-dev-shm-usage"]}
        if os.path.exists(CHROMIUM_PATH):
            launch_kwargs["executable_path"] = CHROMIUM_PATH
        self._browser = await self._pw.chromium.launch(**launch_kwargs)
        ctx = await self._browser.new_context(
            user_agent="ReconHawk/0.1 (security research agent)",
            ignore_https_errors=True,
        )
        self._page = await ctx.new_page()
        self._page.on("request", self._on_request)
        self._page.on("response", self._on_response)
        self.available = True

    def _on_request(self, request) -> None:
        self.network.append(
            {"method": request.method, "url": request.url, "resource": request.resource_type}
        )
        if request.resource_type == "script" or request.url.endswith(".js"):
            self.js_urls.add(request.url)

    def _on_response(self, response) -> None:
        ct = response.headers.get("content-type", "")
        if "javascript" in ct:
            self.js_urls.add(response.url)

    @property
    def page(self) -> Page:
        if self._page is None:
            raise RuntimeError("Browser not started")
        return self._page

    async def navigate(self, url: str) -> dict:
        resp = await self.page.goto(url, timeout=settings.nav_timeout_ms, wait_until="domcontentloaded")
        try:
            await self.page.wait_for_load_state("networkidle", timeout=4000)
        except Exception:
            pass
        title = await self.page.title()
        return {
            "final_url": self.page.url,
            "status": resp.status if resp else None,
            "title": title,
        }

    async def content(self, max_chars: int = 6000) -> str:
        html = await self.page.content()
        return html[:max_chars]

    async def text(self, max_chars: int = 6000) -> str:
        txt = await self.page.inner_text("body")
        return txt[:max_chars]

    async def links(self) -> list[str]:
        hrefs = await self.page.eval_on_selector_all(
            "a[href]", "els => els.map(e => e.getAttribute('href'))"
        )
        base = self.page.url
        out = set()
        for h in hrefs:
            if not h:
                continue
            absu = urljoin(base, h)
            if absu.startswith(("http://", "https://")):
                out.add(absu)
        return sorted(out)

    async def forms(self) -> list[dict]:
        return await self.page.eval_on_selector_all(
            "form",
            """forms => forms.map(f => ({
                action: f.getAttribute('action'),
                method: f.getAttribute('method') || 'GET',
                inputs: Array.from(f.querySelectorAll('input,select,textarea'))
                             .map(i => ({name: i.getAttribute('name'), type: i.getAttribute('type')}))
            }))""",
        )

    async def screenshot(self) -> str:
        os.makedirs(settings.artifacts_dir, exist_ok=True)
        fname = f"task{self.task_id}_{int(time.time()*1000)}.png"
        path = os.path.join(settings.artifacts_dir, fname)
        await self.page.screenshot(path=path, full_page=False)
        return fname

    def discovered_js(self) -> list[str]:
        return sorted(self.js_urls)

    def recorded_requests(self) -> list[dict]:
        # de-dup by (method, url)
        seen = set()
        out = []
        for r in self.network:
            key = (r["method"], r["url"])
            if key in seen:
                continue
            seen.add(key)
            out.append(r)
        return out

    async def close(self) -> None:
        try:
            if self._browser:
                await self._browser.close()
        finally:
            if self._pw:
                await self._pw.stop()
