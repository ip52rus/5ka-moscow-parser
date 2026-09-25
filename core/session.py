from __future__ import annotations

import asyncio
import json
from typing import Any

from camoufox import AsyncCamoufox
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from config import (
    CATALOG_WARMUP_URL,
    HEADER_KEYS,
    MAIN_URL,
    STORAGE_STATE_PATH,
)


class FiveKaSession:
    """Human browser session + direct fetch through the real 5ka page."""

    def __init__(self) -> None:
        self.browser = None
        self.context = None
        self.page = None
        self.headers: dict[str, str] = {}

    async def __aenter__(self) -> "FiveKaSession":
        self.browser = await AsyncCamoufox(
            locale="ru-RU",
            headless=False,
            block_images=False,
        ).start()

        context_kwargs: dict[str, Any] = {
            "geolocation": {"longitude": 37.6173, "latitude": 55.7558},
            "permissions": ["geolocation"],
        }
        if STORAGE_STATE_PATH.exists():
            context_kwargs["storage_state"] = str(STORAGE_STATE_PATH)

        self.context = await self.browser.new_context(**context_kwargs)
        self.page = await self.context.new_page()
        self.page.on("request", self._capture_headers)

        await self._warmup()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self.context is not None:
            try:
                await self.context.storage_state(path=str(STORAGE_STATE_PATH))
            except Exception:
                pass

        if self.browser is not None:
            try:
                await self.browser.close()
            except Exception:
                pass

    def _capture_headers(self, request) -> None:
        try:
            if not request.url.startswith("https://5d.5ka.ru/"):
                return
            for key in HEADER_KEYS:
                value = request.headers.get(key)
                if value:
                    self.headers[key] = value
        except Exception:
            pass

    async def _goto(self, url: str) -> None:
        try:
            await self.page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=60_000,
            )
        except PlaywrightTimeoutError:
            pass

    async def _looks_like_challenge(self) -> bool:
        try:
            url = self.page.url.lower()
            html = (await self.page.content()).lower()
            markers = (
                "/xpvnsulc/",
                "captcha-control",
                "rotated_captcha",
                "js-challenge-loader",
                "servicepipe.tech",
            )
            return any(marker in url or marker in html for marker in markers)
        except Exception:
            return False

    async def _warmup(self) -> None:
        print("Открываю 5ka.ru...")
        await self._goto(MAIN_URL)
        await asyncio.sleep(6)

        if await self._looks_like_challenge():
            print()
            print("=" * 72)
            print("5ka показала CAPTCHA/проверку.")
            print("Пройди её вручную в окне Camoufox.")
            print("Дождись обычной страницы 5ka.ru и нажми Enter в Terminal.")
            print("=" * 72)
            input()
            await asyncio.sleep(3)

        if not self.headers:
            print("Получаю служебные заголовки через каталог...")
            await self._goto(CATALOG_WARMUP_URL)

            for _ in range(30):
                required = {"x-app-version", "x-device-id", "x-platform"}
                if required.issubset(self.headers):
                    break
                await asyncio.sleep(0.5)

        required = {"x-app-version", "x-device-id", "x-platform"}
        missing = required - set(self.headers)
        if missing:
            raise RuntimeError(
                "Не удалось получить обязательные заголовки 5ka: "
                + ", ".join(sorted(missing))
            )

        print("Сессия готова:", ", ".join(sorted(self.headers)))

        try:
            await self.context.storage_state(path=str(STORAGE_STATE_PATH))
        except Exception:
            pass

    async def fetch_json(
        self,
        url: str,
        *,
        use_5d_headers: bool = True,
        credentials: bool = True,
    ) -> Any:
        headers = {"Accept": "application/json, text/plain, */*"}
        if use_5d_headers:
            headers.update(self.headers)

        result = await self.page.evaluate(
            """
            async ({url, headers, credentials}) => {
                try {
                    const response = await fetch(url, {
                        method: "GET",
                        headers,
                        credentials: credentials ? "include" : "omit",
                        cache: "no-store",
                        mode: "cors",
                        referrer: "https://5ka.ru/"
                    });
                    const text = await response.text();
                    return {
                        ok: response.ok,
                        status: response.status,
                        statusText: response.statusText,
                        url: response.url,
                        text
                    };
                } catch (e) {
                    return {
                        ok: false,
                        status: -1,
                        statusText: String(e),
                        url,
                        text: "",
                        error: e && e.stack ? e.stack : String(e)
                    };
                }
            }
            """,
            {"url": url, "headers": headers, "credentials": credentials},
        )

        if not result.get("ok"):
            raise RuntimeError(
                f"HTTP {result.get('status')} {result.get('statusText')} "
                f"{url}; body={result.get('text', '')[:600]}"
            )

        try:
            return json.loads(result["text"])
        except Exception as exc:
            raise RuntimeError(
                f"Ответ не JSON: {url}; body={result.get('text', '')[:600]}"
            ) from exc
