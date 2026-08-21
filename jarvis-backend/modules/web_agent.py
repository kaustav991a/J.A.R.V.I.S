
# A log character must not be able to abort an operation:
# the web agent has its own __main__. See modules/utf8_stdout.py.
try:                            # imported as `modules.web_agent`
    from . import utf8_stdout   # noqa: F401
except ImportError:             # run as `python modules/web_agent.py`
    import utf8_stdout          # noqa: F401,E402
import re
import asyncio
from bs4 import BeautifulSoup
import markdownify
from playwright.async_api import async_playwright


#: Review finding R10, 2026-08-16. `click` and `type_text` interpolate the id
#: straight into a CSS attribute selector:
#:
#:     selector = f"[data-jarvis-id='{element_id}']"
#:
#: An id of `1'], a[href^='http` closes the quote and appends a second selector,
#: so Playwright acts on the first element matching EITHER — and both tools are
#: AUTO tier, with `element_id` declared as a free string in the schema.
#:
#: That matters more than an ordinary injection would, because the numbered map
#: from `_mark_and_extract_dom` is both the contract for what the agent may act
#: on AND the audit record of what it did. A page whose text steers the model can
#: escape the map and click something that never appeared in it.
#:
#: The ids are integers assigned by `_mark_and_extract_dom`, so the check is
#: exact rather than an escaping scheme — there is no legitimate id this refuses.
_ELEMENT_ID_RE = re.compile(r"^\d+$")


def _element_id_problem(element_id) -> str | None:
    """Refuse anything that is not one of the integer ids we handed out."""
    if not _ELEMENT_ID_RE.match(str(element_id or "").strip()):
        return (f"'{str(element_id)[:40]}' is not an element id. Use one of the "
                "numbers from the element list in the most recent page output — "
                "they are plain integers.")
    return None


class WebAgent:
    def __init__(self):
        self.user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None

    async def _init_browser(self):
        if not self._playwright:
            try:
                self._playwright = await async_playwright().start()
                self._browser = await self._playwright.chromium.launch(headless=True)
                self._context = await self._browser.new_context(user_agent=self.user_agent)
                self._page = await self._context.new_page()

                # Route interceptor to block images, media, and fonts
                async def intercept_route(route):
                    if route.request.resource_type in ["image", "media", "font"]:
                        await route.abort()
                    else:
                        await route.continue_()
                await self._page.route("**/*", intercept_route)
            except Exception as e:
                # Partial init (e.g. launch succeeded but new_context failed) would
                # otherwise leave a live Chromium with self._playwright set, so the
                # next _init_browser() short-circuits and we operate on a None page.
                # Tear down whatever came up and re-raise so the caller reports cleanly.
                print(f"[WEB AGENT] Init failed ({e}); tearing down partial browser.", flush=True)
                await self.close()
                raise

    async def close(self):
        """
        Fully tears down the Playwright stack. Each step is independently guarded
        so that a failure closing the context can never prevent the browser and
        the Playwright node subprocess from being stopped — which is exactly how
        zombie Chromium processes were being leaked on a mid-session error.
        """
        for label, closer in (
            ("page", getattr(self._page, "close", None)),
            ("context", getattr(self._context, "close", None)),
            ("browser", getattr(self._browser, "close", None)),
            ("playwright", getattr(self._playwright, "stop", None)),
        ):
            if closer is None:
                continue
            try:
                await closer()
            except Exception as e:
                print(f"[WEB AGENT] Error closing {label} (continuing teardown): {e}", flush=True)
        # Always null out references, even if a step above failed, so the next
        # browse() call re-initialises a clean stack instead of reusing dead handles.
        self._page = None
        self._context = None
        self._browser = None
        self._playwright = None
        return "Web browser closed, memory freed."

    async def _mark_and_extract_dom(self):
        js_code = """
        () => {
            let id_counter = 1;
            const elements = document.querySelectorAll('button, a, input, select, textarea, [role="button"], [role="link"], [tabindex]');
            
            // Clear old marks
            document.querySelectorAll('[data-jarvis-id]').forEach(el => el.removeAttribute('data-jarvis-id'));

            let interactive_map = [];
            elements.forEach(el => {
                const style = window.getComputedStyle(el);
                if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return;
                const rect = el.getBoundingClientRect();
                if (rect.width === 0 || rect.height === 0) return;
                
                const myId = id_counter++;
                el.setAttribute('data-jarvis-id', myId.toString());
                
                let tag = el.tagName.toLowerCase();
                let text = el.innerText || el.value || el.getAttribute('aria-label') || el.getAttribute('placeholder') || el.title || "";
                text = text.trim().substring(0, 60).replace(/\\n/g, ' ');
                
                if (text || tag === 'input' || tag === 'textarea' || tag === 'select') {
                    let info = `[ID: ${myId}] <${tag}> ${text}`;
                    if (tag === 'input') {
                        info += ` (type: ${el.type || 'text'})`;
                    }
                    interactive_map.push(info);
                }
            });
            return interactive_map.join('\\n');
        }
        """
        return await self._page.evaluate(js_code)

    async def _get_page_state(self) -> str:
        # Wait briefly for dynamic JS rendering
        await self._page.wait_for_timeout(1000)
        
        interactive_map_str = await self._mark_and_extract_dom()
        
        html_content = await self._page.content()
        soup = BeautifulSoup(html_content, "html.parser")
        
        # Remove unwanted tags
        for tag in soup(["script", "style", "noscript", "svg", "nav", "footer", "aside", "header"]):
            tag.decompose()
            
        main_content = soup.find("main") or soup.find("article") or soup.find("div", {"id": "content"}) or soup.body
        if not main_content:
            return "Error: Could not parse page content."
            
        text = markdownify.markdownify(str(main_content), heading_style="ATX").strip()
        text = re.sub(r'\n{3,}', '\n\n', text)
        
        if len(text) > 8000:
            text = text[:8000] + "\n\n... [Content Truncated]"
            
        url = self._page.url
        
        result = f"--- CURRENT URL ---\n{url}\n\n"
        result += f"--- INTERACTIVE ELEMENTS ---\nUse web_click or web_type with the ID to interact.\n"
        result += (interactive_map_str[:2000] + "\n...[Truncated]" if len(interactive_map_str) > 2000 else interactive_map_str)
        result += f"\n\n--- PAGE CONTENT ---\n{text}"
        
        return result

    async def browse(self, url: str) -> str:
        if not url.startswith("http"):
            url = f"https://{url}"
            
        print(f"[WEB AGENT] Navigating to: {url}")
        
        try:
            await self._init_browser()
            await self._page.goto(url, timeout=30000, wait_until="domcontentloaded")
            return await self._get_page_state()
        except Exception as e:
            print(f"[WEB AGENT] Playwright error (browse): {e}")
            return f"Error navigating to {url}: {e}"

    async def click(self, element_id: str) -> str:
        print(f"[WEB AGENT] Clicking element {element_id}")
        problem = _element_id_problem(element_id)
        if problem:
            return problem
        try:
            await self._init_browser()
            selector = f"[data-jarvis-id='{element_id}']"
            # Wait for element to be VISIBLE, not just attached — prevents clicking
            # hidden or overlaid elements that exist in the DOM but can't be interacted with.
            try:
                await self._page.wait_for_selector(selector, state="visible", timeout=5000)
            except Exception:
                # Stale-ID recovery: the DOM likely re-rendered since the IDs were handed
                # out. Re-marking renumbers every data-jarvis-id from 1, so the OLD id can
                # now point at a different element (or nothing) — blindly retrying the same
                # selector would risk clicking the wrong thing. Instead, hand back a fresh
                # element map and require the agent to re-issue with a current ID.
                print(f"[WEB AGENT] Element {element_id} not visible — DOM changed; returning refreshed elements.", flush=True)
                fresh_state = await self._get_page_state()
                return (f"Element ID {element_id} is no longer valid — the page changed since "
                        f"the IDs were listed. The element IDs below are current; re-issue the "
                        f"click with a matching ID.\n\n{fresh_state}")
            await self._page.click(selector, timeout=5000)
            return await self._get_page_state()
        except Exception as e:
            return f"Error clicking element ID {element_id}: {e}"

    async def type_text(self, element_id: str, text: str) -> str:
        print(f"[WEB AGENT] Typing into {element_id}: '{text}'")
        problem = _element_id_problem(element_id)
        if problem:
            return problem
        try:
            await self._init_browser()
            selector = f"[data-jarvis-id='{element_id}']"
            # Wait for element to be VISIBLE (not just attached).
            try:
                await self._page.wait_for_selector(selector, state="visible", timeout=5000)
            except Exception:
                # Stale-ID recovery: re-marking renumbers every data-jarvis-id from 1, so
                # the old id can now point at a different element — don't blind-retry the
                # dead selector (risks typing into the wrong field). Return a fresh element
                # map and require the agent to re-issue with a current ID.
                print(f"[WEB AGENT] Element {element_id} not visible — DOM changed; returning refreshed elements.", flush=True)
                fresh_state = await self._get_page_state()
                return (f"Element ID {element_id} is no longer valid — the page changed since "
                        f"the IDs were listed. The element IDs below are current; re-issue the "
                        f"type with a matching ID.\n\n{fresh_state}")
            await self._page.fill(selector, text, timeout=5000)
            # Only press Enter for search-type inputs — unconditional Enter caused
            # unintended form submissions when the user just wanted to fill a field.
            try:
                el_type = await self._page.get_attribute(selector, "type") or ""
                el_role = await self._page.get_attribute(selector, "role") or ""
                if el_type.lower() == "search" or el_role.lower() == "searchbox":
                    await self._page.keyboard.press("Enter")
            except Exception:
                pass  # non-critical; skip Enter if attribute read fails
            return await self._get_page_state()
        except Exception as e:
            return f"Error typing into element ID {element_id}: {e}"

    async def scroll(self, direction: str) -> str:
        try:
            await self._init_browser()
            if "up" in direction.lower():
                await self._page.evaluate("window.scrollBy(0, -window.innerHeight * 0.8)")
            else:
                await self._page.evaluate("window.scrollBy(0, window.innerHeight * 0.8)")
            return await self._get_page_state()
        except Exception as e:
            return f"Error scrolling: {e}"

    async def go_back(self) -> str:
        try:
            await self._init_browser()
            await self._page.go_back(timeout=10000, wait_until="domcontentloaded")
            return await self._get_page_state()
        except Exception as e:
            return f"Error navigating back: {e}"

if __name__ == "__main__":
    async def main():
        agent = WebAgent()
        print("Testing Playwright WebAgent...")
        res = await agent.browse("https://news.ycombinator.com")
        print(res[:1000])
        await agent.close()
    asyncio.run(main())
