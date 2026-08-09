import asyncio
from playwright.async_api import async_playwright
import os
from dotenv import load_dotenv
import logging
from typing import Optional
from pathlib import Path
import csv
import time
import sys

if sys.platform == 'win32':
    import msvcrt
    def _lock_shared(f):
        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
    def _lock_exclusive(f):
        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
    def _unlock(f):
        try:
            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
else:
    import fcntl
    def _lock_shared(f):
        fcntl.flock(f.fileno(), fcntl.LOCK_SH)
    def _lock_exclusive(f):
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
    def _unlock(f):
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)

# set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# loading env variables
load_dotenv()

DIAG_DIR = Path(__file__).parent / "diagnostics"


class WebAutomationBot:
    def __init__(self, headless: bool = False):
        self.headless = headless
        self.browser = None
        self.context = None
        self.page = None
        self.playwright = None
        self._content_frame = None  # frame that has item entry (may be iframe)

    async def _capture_diagnostic(self, label: str):
        """Capture screenshot + page state for self-diagnosis.
        Pushes to in-memory log server (viewable at http://127.0.0.1:5051 Diagnostics tab).
        Also saves to diagnostics/ folder as backup."""
        try:
            # Take screenshot as bytes
            screenshot_bytes = await self.page.screenshot(full_page=True)
            logger.info(f"[diag] Screenshot captured for '{label}'")

            # Page HTML
            html = await self.page.content()

            # Scan all contexts for inputs/buttons
            report = {"label": label, "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"), "url": self.page.url, "contexts": {}}
            contexts = [("main_page", self.page)]
            if self._content_frame:
                contexts.insert(0, ("content_frame", self._content_frame))
            for i, fr in enumerate(self.page.frames):
                if fr != self.page and fr != self._content_frame:
                    contexts.append((f"frame_{i}", fr))

            for ctx_name, ctx in contexts:
                try:
                    inputs = await ctx.evaluate("""() => {
                        return Array.from(document.querySelectorAll('input, select, button, [role="button"]')).map(el => ({
                            tag: el.tagName, id: el.id, name: el.name || '',
                            type: el.type || '', cls: (el.className || '').substring(0, 60),
                            text: (el.textContent || '').substring(0, 40).trim(),
                            vis: !!el.offsetParent, w: el.offsetWidth, h: el.offsetHeight
                        })).filter(e => e.id || e.name || e.type === 'password' || e.tag === 'BUTTON');
                    }""")
                    report["contexts"][ctx_name] = inputs
                    logger.info(f"[diag] {ctx_name}: {len(inputs)} elements found")
                except Exception:
                    report["contexts"][ctx_name] = "evaluate failed"

            # Push to log server in-memory (auto-viewable in browser)
            try:
                from log_server import store_diagnostic
                store_diagnostic(label, screenshot_bytes=screenshot_bytes, report=report, html_snippet=html[:30000])
                logger.info(f"[diag] Pushed to log server — view at http://127.0.0.1:5051 (Diagnostics tab)")
            except Exception as e:
                logger.warning(f"[diag] Could not push to log server: {e}")

            # Also save to disk as backup
            DIAG_DIR.mkdir(exist_ok=True)
            ts = time.strftime("%Y%m%d-%H%M%S")
            prefix = DIAG_DIR / f"{ts}_{label}"
            with open(f"{prefix}.png", "wb") as f:
                f.write(screenshot_bytes)
            import json as _json
            with open(f"{prefix}.json", "w") as f:
                _json.dump(report, f, indent=2)
            logger.info(f"[diag] Backup saved: {prefix}.png + .json")

        except Exception as e:
            logger.error(f"[diag] Failed to capture diagnostic: {e}")
    
    async def setup(self, use_saved_auth: bool = True):
        """Initialize browser and login if needed"""
        # starts playwright
        self.playwright = await async_playwright().start()
        
        # launch browser maximized (full screen)
        self.browser = await self.playwright.chromium.launch(
            headless=self.headless,
            slow_mo=0,
            args=['--start-maximized']
        )
        
        # no_viewport=True lets the page fill the maximized window naturally
        auth_file = "auth_state.json"
        if use_saved_auth and Path(auth_file).exists():
            logger.info("Loading saved authentication state...")
            self.context = await self.browser.new_context(
                storage_state=auth_file,
                no_viewport=True
            )
        else:
            logger.info("Creating new browser context...")
            self.context = await self.browser.new_context(no_viewport=True)
        
        self.context.set_default_timeout(30000)  # 30s default timeout
        
        self.page = await self.context.new_page()
        
        # navigates to website
        site_url = os.getenv('SITE_URL')
        logger.info(f"Navigating to {site_url}")
        await self.page.goto(site_url)
        
        # wait for page to load
        await self.page.wait_for_load_state('networkidle')
        
        # check for session message - ensure only ONE tab (if new tab opens, use it and close old)
        try:
            start_over = await self.page.wait_for_selector('a:has-text("Click Here to Start Over")', timeout=3000)
            if start_over:
                logger.info("Session message found, clicking Start Over...")
                try:
                    async with self.context.expect_page(timeout=5000) as page_info:
                        await start_over.click()
                    new_page = await page_info.value
                    await self.page.close()
                    self.page = new_page
                    logger.info("Using single tab (closed old)")
                except Exception:
                    await self.page.wait_for_load_state('networkidle')
                await self.page.wait_for_load_state('networkidle')
                logger.info("Session cleared, proceeding...")
        except Exception:
            logger.info("No session message, proceeding...")
        
        # check if we need to login
        login_needed = False
        try:
            # check if username field exists (indicates login page)
            await self.page.wait_for_selector('input[aria-label="Username"]', timeout=3000)
            login_needed = True
            logger.info("Login required, entering credentials...")
            
            # fills username and password
            username = os.getenv('SITE_USERNAME')
            password = os.getenv('SITE_PASSWORD')
            
            await self.page.fill('input[aria-label="Username"]', username)
            await self.page.fill('input[aria-label="Password"]', password)
            
            # login
            await self.page.click('button:has-text("Log in")')
            
            # wait for login to complete
            await self.page.wait_for_load_state('networkidle')
            logger.info("Login submitted...")
            
            # wait longer for page to fully load after login
            await asyncio.sleep(3)
            
            # check if 2FA is needed or if we're logged in
            logged_in = False
            
            # Use the exact selector for the Add/View Retail Orders element
            main_selector = 'span.IconCaptionText:has-text("Add/View Retail Orders")'
            
            try:
                # First try with short timeout
                await self.page.wait_for_selector(main_selector, timeout=5000)
                logger.info("Login successful! Found Add/View Retail Orders button")
                logged_in = True
            except:
                # 2FA might be required or page is still loading
                logger.info("Waiting for page to load or 2FA completion...")
                logger.info("Please complete any 2FA in the browser if prompted.")
                
                # Wait with periodic checks and better feedback
                for i in range(60):  # 60 seconds total
                    try:
                        # Check if element exists
                        element = await self.page.query_selector(main_selector)
                        if element:
                            logger.info("✓ Login/2FA completed successfully!")
                            logged_in = True
                            break
                        
                        # Also check for any error messages
                        error_element = await self.page.query_selector('text=/error|invalid|incorrect/i')
                        if error_element:
                            logger.warning("Possible login error detected. Check credentials.")
                    except:
                        pass
                    
                    await asyncio.sleep(1)
                    if i % 10 == 0 and i > 0:
                        logger.info(f"Still waiting for login completion... ({60-i} seconds remaining)")
                
                if not logged_in:
                    # Take screenshot for debugging
                    logger.error("Could not find main page after login")
                    await self.page.screenshot(path="login_timeout.png")
                    logger.info("Screenshot saved as login_timeout.png for debugging")
                    
                    # Log current URL for debugging
                    current_url = self.page.url
                    logger.info(f"Current URL: {current_url}")
                    
                    # Try to log page content for debugging
                    try:
                        page_text = await self.page.text_content('body')
                        if page_text:
                            logger.info(f"Page contains text (first 200 chars): {page_text[:200]}...")
                    except:
                        pass
                    
                    raise Exception("Login failed or page structure unexpected. Check login_timeout.png")
            
            # save authentication state after successful login
            if logged_in:
                await self.save_auth_state()
            
        except Exception as e:
            if not login_needed:
                logger.info("Already logged in with saved authentication")
            else:
                logger.error(f"Login process error: {e}")
                raise
        
        # verify we're on the main page using the correct selector
        try:
            main_selector = 'span.IconCaptionText:has-text("Add/View Retail Orders")'
            await self.page.wait_for_selector(main_selector, timeout=10000)
            logger.info("✓ Bot is ready to process orders")
        except:
            logger.error("Could not verify main page loaded correctly")
            logger.info("Looking for debugging information...")
            
            # Try to find what's on the page
            try:
                all_spans = await self.page.query_selector_all('span.IconCaptionText')
                if all_spans:
                    logger.info(f"Found {len(all_spans)} IconCaptionText spans:")
                    for span in all_spans[:5]:  # Log first 5
                        text = await span.text_content()
                        logger.info(f"  - {text}")
            except:
                pass
            
            await self.page.screenshot(path="error_page.png")
            logger.info("Screenshot saved as error_page.png")
            raise Exception("Main page did not load correctly - check error_page.png")
    
    async def navigate_to_home(self):
        """Navigate back to home page to start new order"""
        logger.info("Navigating back to home page...")
        site_url = os.getenv('SITE_URL')
        await self.page.goto(site_url)
        await self.page.wait_for_load_state('networkidle')
        # wait for main page to load with correct selector
        main_selector = 'span.IconCaptionText:has-text("Add/View Retail Orders")'
        await self.page.wait_for_selector(main_selector, timeout=10000)
    
    async def start_order(self):
        """Navigate to the item entry page to start a new order"""
        # make sure we're on home page
        await self.navigate_to_home()
        
        # clicks on add/view retail orders using the correct selector
        logger.info("Starting new order...")
        main_selector = 'span.IconCaptionText:has-text("Add/View Retail Orders")'
        await self.page.click(main_selector)
        await self.page.wait_for_load_state('networkidle')
        
        # clicking add order
        await self.page.click('span:has-text("Add Order")')
        await self.page.wait_for_load_state('networkidle')
        await asyncio.sleep(1)  # wait for modal/form to render
        
        # click on manually enter items - try multiple selectors (IDs can be dynamic)
        manually_clicked = False
        
        # try Playwright's robust locators first
        for locator in [
            self.page.get_by_label("Manually Enter Items"),
            self.page.get_by_label("Manually enter items"),
            self.page.get_by_role("radio", name="Manually Enter Items"),
            self.page.get_by_role("radio", name="Manually enter items"),
        ]:
            try:
                if await locator.count() > 0:
                    await locator.first.click()
                    manually_clicked = True
                    logger.info("Selected manually enter items (label/role)")
                    break
            except Exception:
                continue
        
        if not manually_clicked:
            for selector in [
                'input[type="radio"][id="Dl-q"]',
                'label:has-text("Manually Enter")',
                'label:has-text("Manually enter")',
                'label:has-text("Manual Entry")',
                'label:has-text("Manually")',
                'span:has-text("Manually Enter")',
                'span:has-text("Manual Entry")',
            ]:
                try:
                    el = await self.page.wait_for_selector(selector, timeout=2000)
                    if el:
                        await el.click()
                        manually_clicked = True
                        logger.info(f"Selected manually enter items via: {selector}")
                        break
                except Exception:
                    continue
        
        if not manually_clicked:
            # fallback: click the first radio after "Add Order" (manual is often first option)
            try:
                radios = await self.page.query_selector_all('input[type="radio"]')
                if radios:
                    await radios[0].click()
                    manually_clicked = True
                    logger.info("Selected first radio option (manually enter)")
            except Exception as e:
                logger.warning(f"Radio fallback failed: {e}")
        
        if not manually_clicked:
            raise Exception("Could not find/click 'Manually Enter Items' option")
        
        await asyncio.sleep(0.5)
        
        # click next button to go to item entry page
        await self._scroll_to_bottom()
        await asyncio.sleep(0.2)
        await self._scroll_to_bottom()
        await self.page.click('span.ActionButtonCaptionText:has-text("Next")')
        await self.page.wait_for_load_state('networkidle')
        logger.info("Ready to search for items")
    
    async def _get_search_input(self):
        """Get the item search input - try main page and iframes (Glide uses iframes)"""
        for frame in [self.page] + list(self.page.frames):
            try:
                for selector in ['input[id="Dm-8"]', 'input[placeholder*="Item"]', 'input[id^="Dm"]', 'input[type="text"]']:
                    try:
                        el = await frame.wait_for_selector(selector, timeout=500)
                        if el and await el.is_visible():
                            self._content_frame = frame
                            return el
                    except Exception:
                        continue
            except Exception:
                continue
        raise Exception("Could not find item search input")
    
    async def _click_add_item(self):
        """Click Add Item - multiple methods, imperative that one works (Glide/ServiceNow structure)."""
        async def try_click_in_frame(frame):
            for sel in ['span:has-text("Add Item")', 'div:has(span:has-text("Add Item"))', 'td:has(span:has-text("Add Item"))']:
                try:
                    loc = frame.locator(sel).first
                    if await loc.count() == 0:
                        continue
                    await loc.scroll_into_view_if_needed()
                    await loc.click(force=True, timeout=2000)
                    return True
                except Exception:
                    continue
            return False
        async def try_click():
            # try content frame first (where search input was found)
            if self._content_frame and await try_click_in_frame(self._content_frame):
                return True
            if await try_click_in_frame(self.page):
                return True
            for fr in self.page.frames:
                if fr != self.page.main_frame:
                    try:
                        if await try_click_in_frame(fr):
                            return True
                    except Exception:
                        pass
            eval_page = self._content_frame if self._content_frame else self.page
            # Method 1: JS - find element with "Add Item" (exact or includes), get clickable parent
            r = await eval_page.evaluate("""() => {
                const all = document.querySelectorAll('span, div, td, button, a');
                for (const e of all) {
                    const t = e.textContent && e.textContent.trim();
                    if ((t === 'Add Item' || t.includes('Add Item')) && e.offsetParent) {
                        const target = e.closest('[data-event]') || e.closest('td') || e.closest('div[role="button"]') || e.parentElement || e;
                        target.scrollIntoView({block: 'center'});
                        const rect = target.getBoundingClientRect();
                        const x = rect.left + rect.width/2, y = rect.top + rect.height/2;
                        ['mousedown','mouseup','click'].forEach(n => target.dispatchEvent(new MouseEvent(n, {bubbles:true,cancelable:true,view:window,clientX:x,clientY:y})));
                        return true;
                    }
                }
                return false;
            }""")
            if r:
                return True
            # Method 2: JS - direct .click() on element containing "Add Item"
            r = await eval_page.evaluate("""() => {
                const all = document.querySelectorAll('span, div, td, button');
                for (const e of all) {
                    const t = e.textContent && e.textContent.trim();
                    if ((t === 'Add Item' || t.includes('Add Item')) && e.offsetParent) {
                        e.scrollIntoView({block: 'center'});
                        e.click();
                        return true;
                    }
                }
                return false;
            }""")
            if r:
                return True
            # Method 3: Playwright - div with data-event containing Add Item, mouse.click at center
            try:
                el = await eval_page.query_selector('div[data-event]:has(span:has-text("Add Item"))')
                if el:
                    await el.scroll_into_view_if_needed()
                    box = await el.bounding_box()
                    if box:
                        await self.page.mouse.click(box['x'] + box['width']/2, box['y'] + box['height']/2)
                        return True
            except Exception:
                pass
            # Method 4: Playwright - td containing Add Item
            try:
                el = await eval_page.query_selector('td:has(span:has-text("Add Item"))')
                if el:
                    await el.scroll_into_view_if_needed()
                    await el.click(force=True)
                    return True
            except Exception:
                pass
            # Method 5: Playwright - span ActionButtonCaptionText
            try:
                el = await eval_page.query_selector('span.ActionButtonCaptionText:has-text("Add Item")')
                if el:
                    await el.scroll_into_view_if_needed()
                    await el.click(force=True)
                    return True
            except Exception:
                pass
            # Method 6: Playwright - any span with Add Item, mouse click at center
            try:
                el = await eval_page.query_selector('span:has-text("Add Item")')
                if el:
                    await el.scroll_into_view_if_needed()
                    box = await el.bounding_box()
                    if box:
                        await self.page.mouse.click(box['x'] + box['width']/2, box['y'] + box['height']/2)
                        return True
            except Exception:
                pass
            # Method 7: Playwright getByText - flexible text match
            try:
                loc = eval_page.locator('text=Add Item')
                if await loc.count() > 0:
                    await loc.first.scroll_into_view_if_needed()
                    await loc.first.click(force=True)
                    return True
            except Exception:
                pass
            # Method 8: JS - any element containing "add" and "item" (case insensitive)
            r = await eval_page.evaluate("""() => {
                const all = document.querySelectorAll('span, div, td, button, a');
                for (const e of all) {
                    const t = (e.textContent || '').toLowerCase();
                    if (t.includes('add') && t.includes('item') && t.length < 20 && e.offsetParent) {
                        const target = e.closest('[data-event]') || e.closest('td') || e.parentElement || e;
                        target.scrollIntoView({block: 'center'});
                        target.click();
                        return true;
                    }
                }
                return false;
            }""")
            if r:
                return True
            return False
        try:
            result = await asyncio.wait_for(try_click(), timeout=15.0)
        except asyncio.TimeoutError:
            logger.warning("    _click_add_item timed out after 15s")
            result = False
        if result:
            await asyncio.sleep(0.6)
        return result
    
    async def check_and_process_items(self, items):
        """Check all unfilled items and add any available ones to cart (one order, min 10 total qty).
        Skips items with order_filled='yes' so ordered items are never re-ordered."""
        items_found = []
        total_qty_added = 0
        self.trace_log = getattr(self, 'trace_log', [])

        for item in items:
            item_number = item['item_number']
            quantity = int(item['quantity'])

            # skip if already ordered (order_filled persisted to CSV on successful submit)
            if item.get('order_filled', '').lower() == 'yes':
                continue

            # skip backorder items (will be picked up in next cycle)
            if item.get('order_filled', '').lower() == 'backorder':
                continue

            t_item_start = time.time()
            trace = {'item': item_number, 'steps': {}, 'result': '', 'total_ms': 0}

            # Human-like delay between searches (1-3s random)
            import random
            delay = random.uniform(1.0, 3.0)
            logger.info(f"Checking item #{item_number} (requested qty: {quantity})... (waiting {delay:.1f}s)")
            await asyncio.sleep(delay)

            # enter item number - click, clear, type (type() triggers proper input events)
            search_input = await self._get_search_input()
            await search_input.click()
            await search_input.fill('')
            await search_input.type(str(item_number), delay=30)
            
            await search_input.press('Enter')
            await self.page.wait_for_load_state('networkidle')
            await asyncio.sleep(0.5)  # wait for search results to render

            # STEP 1: Read available qty FIRST — skip immediately if 0
            try:
                ctx = self._content_frame if self._content_frame else self.page

                logger.info(f"  [STEP 1] Reading available qty for item #{item_number}...")
                t0 = time.time()
                available_quantity = -1
                try:
                    el = await ctx.wait_for_selector('span[id="fgvt_Dm-m-1"]', timeout=3000)
                    available_text = await el.text_content() or '0'
                    available_quantity = int(available_text.replace(',', ''))
                    logger.info(f"  [STEP 1] Available qty: {available_quantity}")
                except Exception:
                    logger.warning(f"  [STEP 1] Could not read qty via fgvt_Dm-m-1")

                trace['steps']['read_qty'] = round((time.time() - t0) * 1000)

                # If qty is 0, skip — don't waste 15s trying to click inactive Add Item
                if available_quantity == 0:
                    delay = random.uniform(1.0, 3.0)
                    logger.info(f"  x Item #{item_number} — available qty is 0, skipping (waiting {delay:.1f}s)")
                    await asyncio.sleep(delay)
                    trace['result'] = 'SKIP (qty 0)'
                    trace['total_ms'] = round((time.time() - t_item_start) * 1000)
                    self.trace_log.append(trace)
                    raise Exception("Available quantity is 0")

                # If qty > 0, adjust order qty if needed
                backorder_qty = 0
                if available_quantity > 0:
                    logger.info(f"  ✓ Item #{item_number} is AVAILABLE! (qty: {available_quantity})")
                    if quantity > available_quantity:
                        backorder_qty = quantity - available_quantity
                        logger.info(f"    Ordering {available_quantity} of {quantity} requested ({backorder_qty} → backorder)")
                        quantity = available_quantity
                    else:
                        logger.info(f"    Using requested quantity: {quantity}")
                else:
                    # Could not read qty via fgvt_Dm-m-1 — try broader selectors
                    logger.info(f"  [STEP 1] Trying broader qty selectors...")
                    for qty_sel in ['span[id^="fgvt_Dm"]', 'span[id^="fgvt_"]']:
                        try:
                            els = await ctx.query_selector_all(qty_sel)
                            for qel in els:
                                txt = (await qel.text_content() or '').replace(',', '').strip()
                                if txt.isdigit():
                                    val = int(txt)
                                    el_id = await qel.get_attribute('id') or '?'
                                    logger.info(f"    Found {el_id}: {val}")
                                    if val > 0 and available_quantity <= 0:
                                        available_quantity = val
                        except Exception:
                            continue

                    if available_quantity > 0:
                        logger.info(f"  ✓ Item #{item_number} is AVAILABLE! (qty: {available_quantity} from broader scan)")
                        if quantity > available_quantity:
                            backorder_qty = quantity - available_quantity
                            logger.info(f"    Ordering {available_quantity} of {quantity} requested ({backorder_qty} → backorder)")
                            quantity = available_quantity
                        else:
                            logger.info(f"    Using requested quantity: {quantity}")
                    else:
                        # Still unknown — check if Add Item button is active as last resort
                        logger.info(f"  [STEP 1] Qty still unknown, checking Add Item button state...")
                        add_item_visible = None
                        for sel in ['span:has-text("Add Item")', 'button:has-text("Add Item")']:
                            try:
                                add_item_visible = await ctx.wait_for_selector(sel, timeout=1000)
                                if add_item_visible:
                                    break
                            except Exception:
                                continue
                        if not add_item_visible:
                            delay = random.uniform(1.0, 3.0)
                            logger.info(f"  x Item #{item_number} — not available, skipping (waiting {delay:.1f}s)")
                            await asyncio.sleep(delay)
                            raise Exception("Add Item button not found and qty unknown")
                        logger.warning(f"  ⚠ Item #{item_number} — qty unknown but Add Item active, using requested qty: {quantity}")

                # click Add Item button
                logger.info(f"  [STEP 2] Clicking Add Item button...")
                add_clicked = await self._click_add_item()
                if add_clicked:
                    logger.info("  [STEP 2] ✓ Add Item clicked successfully, waiting for quantity modal...")
                if not add_clicked:
                    await self.page.screenshot(path="add_item_fail.png")
                    html_snippet = await ctx.evaluate("""() => {
                        const spans = document.querySelectorAll('span');
                        return Array.from(spans).filter(s => s.textContent && s.textContent.includes('Add')).slice(0,10).map(s => ({text: s.textContent.trim().substring(0,50), tag: s.tagName})).join('|');
                    }""")
                    logger.error(f"  [STEP 2] FAILED - Add Item elements on page: {html_snippet}")
                    raise Exception("Failed to click Add Item - see add_item_fail.png")
                await self.page.wait_for_load_state('networkidle')
                await asyncio.sleep(0.4)  # wait for quantity modal to open

                # Type quantity into modal (use adjusted qty if we capped by available), then click Add Item
                qty_str = str(int(quantity))
                logger.info(f"  [STEP 3] Entering quantity {qty_str} for item #{item_number}...")
                done = False
                all_frames = [self.page] + list(self.page.frames)
                for frame in all_frames:
                    try:
                        # Find quantity input - id Ds_1-81, class DocControlQuantity
                        result = await frame.evaluate(f"""() => {{
                            const qtyInput = document.querySelector('input[id="Ds_1-81"]') || document.querySelector('input[id^="Ds_1"]') || document.querySelector('input.DocControlQuantity') || document.querySelector('input[name="Ds_1-81"]');
                            if (!qtyInput || !qtyInput.offsetParent) return false;
                            qtyInput.focus();
                            qtyInput.select();
                            qtyInput.value = '';
                            qtyInput.value = '{qty_str}';
                            qtyInput.dispatchEvent(new Event('input', {{ bubbles: true }}));
                            qtyInput.dispatchEvent(new Event('change', {{ bubbles: true }}));
                            qtyInput.dispatchEvent(new Event('blur', {{ bubbles: true }}));
                            const dialog = document.querySelector('[role="dialog"]') || document.querySelector('[class*="modal"]') || document;
                            const btns = dialog.querySelectorAll('button, span, div[role="button"], a');
                            for (const b of btns) {{
                                const t = (b.textContent || '').trim();
                                if (t === 'Add Item' || t === 'OK') {{
                                    b.click();
                                    return true;
                                }}
                            }}
                            return false;
                        }}""")
                        if result:
                            done = True
                            logger.info(f"  [STEP 3] ✓ Entered quantity {qty_str} and clicked Add Item in modal")
                            break
                    except Exception:
                        continue
                if not done:
                    logger.info(f"  [STEP 3] JS method failed, trying Playwright method...")
                    # Playwright: find quantity input by exact id/class
                    for frame in all_frames:
                        try:
                            qty_input = await frame.query_selector('input[id="Ds_1-81"]') or await frame.query_selector('input[id^="Ds_1"]') or await frame.query_selector('input.DocControlQuantity')
                            if qty_input and await qty_input.is_visible():
                                await qty_input.click()
                                await qty_input.fill('')
                                await qty_input.type(qty_str, delay=20)
                                await asyncio.sleep(0.1)
                                add_btn = await frame.query_selector('[role="dialog"] button:has-text("Add Item"), [role="dialog"] span:has-text("Add Item")') or await frame.query_selector('[role="dialog"] button:has-text("OK"), [role="dialog"] span:has-text("OK")')
                                if add_btn:
                                    await add_btn.click()
                                    done = True
                                    logger.info(f"  [STEP 3] ✓ Entered quantity {qty_str} via Playwright method")
                                    break
                        except Exception:
                            continue
                if not done:
                    await self.page.screenshot(path="qty_modal_fail.png")
                    raise Exception("Could not enter quantity — see qty_modal_fail.png")
                await self.page.wait_for_load_state('networkidle')
                await asyncio.sleep(0.2)  # wait for modal to close

                # mark item as processed
                item['order_filled'] = 'yes'
                item['quantity'] = quantity  # update to actual ordered qty
                items_found.append(item)
                total_qty_added += quantity

                # create backorder entry for remaining qty
                if backorder_qty > 0:
                    backorder_item = {
                        'item_number': item_number,
                        'quantity': backorder_qty,
                        'order_filled': 'backorder'
                    }
                    items.append(backorder_item)
                    logger.info(f"    + Backorder created: {backorder_qty} units for item #{item_number}")

                trace['result'] = f"ADDED {quantity} units" + (f" (+{backorder_qty} backorder)" if backorder_qty > 0 else "")
                trace['total_ms'] = round((time.time() - t_item_start) * 1000)
                self.trace_log.append(trace)
                logger.info(f"  [STEP 4] ✓ Added to cart: {quantity} units (total: {total_qty_added}) [{trace['total_ms']}ms]")

            except Exception as e:
                logger.error(f"  ✗ Item #{item_number} FAILED at: {e}")
                if 'SKIP' not in trace.get('result', ''):
                    trace['result'] = f"FAILED: {e}"
                    trace['total_ms'] = round((time.time() - t_item_start) * 1000)
                    self.trace_log.append(trace)
                try:
                    await self.page.screenshot(path=f"item_{item_number}_fail.png")
                    logger.error(f"    Screenshot saved: item_{item_number}_fail.png")
                except Exception:
                    pass
                try:
                    search_input = await self._get_search_input()
                    await search_input.click()
                    await search_input.press('Control+a')
                    await search_input.press('Backspace')
                    await asyncio.sleep(0.1)
                except Exception:
                    pass
        
        return items_found, total_qty_added
    
    async def _scroll_to_bottom(self):
        """Scroll page and all frames to bottom - call before every Next/Submit."""
        async def do_scroll(ctx):
            try:
                await ctx.evaluate('window.scrollTo(0, document.body.scrollHeight)')
                await ctx.evaluate('() => { const d = document.scrollingElement || document.documentElement; if(d) d.scrollTop = d.scrollHeight; }')
                await ctx.evaluate('''() => {
                    document.querySelectorAll('[style*="overflow"], .Overflown, [class*="Scroll"]').forEach(el => {
                        if (el.scrollHeight > el.clientHeight) el.scrollTop = el.scrollHeight;
                    });
                }''')
            except Exception:
                pass
        await do_scroll(self.page)
        if self._content_frame:
            await do_scroll(self._content_frame)
        for fr in self.page.frames:
            if fr != self.page.main_frame:
                try:
                    await do_scroll(fr)
                except Exception:
                    pass
    
    async def _click_next_or_submit(self, text: str, timeout_ms: int = 3000):
        """Click Next or Submit - span.ActionButtonCaptionText structure.
        Scrolls to bottom so buttons are visible, then clicks.
        Fast path: query_selector (instant) before wait_for_selector (slow)."""
        await self._scroll_to_bottom()
        await asyncio.sleep(0.1)
        await self._scroll_to_bottom()

        selectors = [
            f'span.ActionButtonCaptionText:has-text("{text}")',
            f'span:has-text("{text}")',
            f'button:has-text("{text}")',
        ]
        contexts = ([self._content_frame] if self._content_frame else []) + [self.page] + list(self.page.frames)

        # Fast path: instant query_selector across all contexts
        for ctx in contexts:
            for sel in selectors:
                try:
                    btn = await ctx.query_selector(sel)
                    if btn and await btn.is_visible():
                        await btn.scroll_into_view_if_needed()
                        await btn.click()
                        logger.info(f"Clicked '{text}' via fast query_selector")
                        return True
                except Exception:
                    continue

        # JS click via ActionButtonCaptionText parent (fast, no wait)
        for ctx in contexts:
            try:
                clicked = await ctx.evaluate(f"""() => {{
                    const spans = document.querySelectorAll('span.ActionButtonCaptionText');
                    for (const s of spans) {{
                        if (s.textContent.trim().includes('{text}')) {{
                            const p = s.closest('button, div[role="button"], [class*="ActionButton"], [class*="Button"]') || s.parentElement;
                            if (p && p.offsetParent) {{ p.click(); return true; }}
                            s.click(); return true;
                        }}
                    }}
                    return false;
                }}""")
                if clicked:
                    logger.info(f"Clicked '{text}' via JS parent click")
                    return True
            except Exception:
                continue

        # Slow fallback: wait_for_selector with short timeout (only if fast paths failed)
        logger.info(f"Fast paths failed for '{text}', trying wait_for_selector...")
        for ctx in contexts:
            for sel in selectors:
                try:
                    btn = await ctx.wait_for_selector(sel, timeout=timeout_ms)
                    if btn and await btn.is_visible():
                        await btn.scroll_into_view_if_needed()
                        await btn.click()
                        logger.info(f"Clicked '{text}' via wait_for_selector fallback")
                        return True
                except Exception:
                    continue
        return False
    
    async def _fill_password_field(self, password: str) -> bool:
        """Find password input and type using real keyboard events.
        ServiceNow renders in iframe — search content frame FIRST.
        Glide IDs are dynamic so we use type='password' as primary selector."""
        import time
        start = time.time()

        pw_selectors = [
            'input[type="password"]',
            'input.DocControlPassword',
            'input[id^="Dn"]',
            'input#Dn-k',
            'input[name="Dn-k"]',
        ]

        # Build context list: content frame FIRST, then page, then all frames
        all_contexts = []
        if self._content_frame:
            all_contexts.append(('content_frame', self._content_frame))
        all_contexts.append(('main_page', self.page))
        for i, fr in enumerate(self.page.frames):
            if fr != self.page and fr != self._content_frame:
                all_contexts.append((f'frame_{i}', fr))

        logger.info(f"[password] Attempting entry across {len(all_contexts)} contexts...")

        # Method 1: JS focus + keyboard.type — try each context
        for ctx_name, ctx in all_contexts:
            try:
                focused = await ctx.evaluate("""() => {
                    const sels = ['input[type="password"]', 'input.DocControlPassword', 'input[id^="Dn"]'];
                    for (const sel of sels) {
                        const el = document.querySelector(sel);
                        if (el) {
                            el.scrollIntoView({block: 'center'});
                            el.focus();
                            el.click();
                            el.value = '';
                            el.dispatchEvent(new Event('focus', {bubbles: true}));
                            return sel + ' (id=' + el.id + ', type=' + el.type + ')';
                        }
                    }
                    return null;
                }""")
                if focused:
                    await asyncio.sleep(0.05)
                    await self.page.keyboard.type(password, delay=10)
                    logger.info(f"[password] Entered via JS focus in {ctx_name}: {focused} ({time.time()-start:.1f}s)")
                    return True
                else:
                    logger.info(f"[password] JS focus: no password input found in {ctx_name}")
            except Exception as e:
                logger.warning(f"[password] JS focus failed in {ctx_name}: {e}")

        # Method 2: Playwright query_selector + click — try each context
        for ctx_name, ctx in all_contexts:
            for sel in pw_selectors:
                try:
                    el = await ctx.query_selector(sel)
                    if el:
                        is_vis = await el.is_visible()
                        logger.info(f"[password] Found {sel} in {ctx_name}, visible={is_vis}")
                        if is_vis:
                            await el.scroll_into_view_if_needed()
                            await el.click(force=True)
                            await asyncio.sleep(0.05)
                            await self.page.keyboard.press('Control+a')
                            await self.page.keyboard.press('Delete')
                            await self.page.keyboard.type(password, delay=10)
                            logger.info(f"[password] Entered via click {sel} in {ctx_name} ({time.time()-start:.1f}s)")
                            return True
                        else:
                            # Try clicking even if not "visible" — Glide hides things oddly
                            await el.click(force=True)
                            await asyncio.sleep(0.05)
                            await self.page.keyboard.type(password, delay=10)
                            logger.info(f"[password] Entered via force-click {sel} in {ctx_name} ({time.time()-start:.1f}s)")
                            return True
                except Exception:
                    pass

        # Method 3: wait_for_selector with timeout — password may appear after delay
        logger.info(f"[password] Fast methods failed ({time.time()-start:.1f}s), trying wait_for_selector...")
        for ctx_name, ctx in all_contexts:
            for sel in pw_selectors[:3]:
                try:
                    el = await ctx.wait_for_selector(sel, timeout=3000)
                    if el:
                        logger.info(f"[password] wait_for_selector found {sel} in {ctx_name}")
                        await el.scroll_into_view_if_needed()
                        await el.click(force=True)
                        await asyncio.sleep(0.05)
                        await self.page.keyboard.press('Control+a')
                        await self.page.keyboard.press('Delete')
                        await self.page.keyboard.type(password, delay=10)
                        logger.info(f"[password] Entered via wait {sel} in {ctx_name} ({time.time()-start:.1f}s)")
                        return True
                except Exception:
                    pass

        # Method 4: Tab into field
        try:
            logger.info("[password] Trying Tab method...")
            await self.page.keyboard.press('Tab')
            await asyncio.sleep(0.05)
            await self.page.keyboard.type(password, delay=10)
            logger.info(f"[password] Entered via Tab ({time.time()-start:.1f}s)")
            return True
        except Exception as e:
            logger.warning(f"[password] Tab method failed: {e}")

        # Self-diagnosis: capture screenshot + full page state
        logger.error(f"[password] ALL METHODS FAILED after {time.time()-start:.1f}s — capturing diagnostic...")
        await self._capture_diagnostic("password_failed")
        
        return False
    
    async def _wait_for_wizard_transition(self, timeout_s: float = 3.0):
        """Wait for ServiceNow wizard page transition.
        Uses domcontentloaded (fast) instead of networkidle (Glide never settles)."""
        try:
            await self.page.wait_for_load_state('domcontentloaded', timeout=timeout_s * 1000)
        except Exception:
            pass
        await asyncio.sleep(0.8)  # let Glide JS render the new step

    async def _click_wizard_button(self, text: str, step_label: str, max_retries: int = 3) -> bool:
        """Click a wizard button with retry. Waits for button to appear after page transition."""
        import time
        start = time.time()
        logger.info(f"[wizard] {step_label}: looking for '{text}' button...")

        for attempt in range(max_retries):
            if attempt > 0:
                logger.info(f"[wizard] {step_label}: retry {attempt}, waiting for button...")
                await asyncio.sleep(1.0)

            clicked = await self._click_next_or_submit(text, timeout_ms=2000)
            if clicked:
                elapsed = time.time() - start
                logger.info(f"[wizard] {step_label}: clicked '{text}' in {elapsed:.1f}s")
                return True

        elapsed = time.time() - start
        logger.error(f"[wizard] {step_label}: FAILED to find '{text}' after {elapsed:.1f}s")
        return False

    async def _find_and_click_ach(self) -> bool:
        """Select ACH Debit Bank payment — fast query_selector first."""
        import time
        start = time.time()
        logger.info("[wizard] Looking for ACH Debit Bank...")

        contexts = ([self._content_frame] if self._content_frame else []) + [self.page] + list(self.page.frames)

        # Fast: query_selector (instant)
        for ctx in contexts:
            for sel in ['text=ACH Debit Bank', 'span:has-text("ACH Debit")', 'label:has-text("ACH")']:
                try:
                    el = await ctx.query_selector(sel)
                    if el and await el.is_visible():
                        await el.click()
                        logger.info(f"[wizard] Selected ACH in {time.time()-start:.1f}s (fast)")
                        return True
                except Exception:
                    continue

        # JS fallback: click by text content
        for ctx in contexts:
            try:
                clicked = await ctx.evaluate("""() => {
                    const els = document.querySelectorAll('span, label, div, td');
                    for (const el of els) {
                        if (el.textContent.trim().includes('ACH Debit Bank') && el.offsetParent) {
                            el.click(); return true;
                        }
                    }
                    return false;
                }""")
                if clicked:
                    logger.info(f"[wizard] Selected ACH in {time.time()-start:.1f}s (JS)")
                    return True
            except Exception:
                continue

        # Slow fallback: wait_for_selector
        for ctx in contexts:
            try:
                el = await ctx.wait_for_selector('text=ACH Debit Bank', timeout=3000)
                if el and await el.is_visible():
                    await el.click()
                    logger.info(f"[wizard] Selected ACH in {time.time()-start:.1f}s (wait)")
                    return True
            except Exception:
                continue

        logger.warning(f"[wizard] ACH not found after {time.time()-start:.1f}s - continuing anyway")
        return False

    async def submit_order(self):
        """Complete checkout and submit the full order (all items in cart).
        Wizard: Next×2 → ACH → Next×3 → Submit → Password → final Submit"""
        import time
        overall_start = time.time()
        logger.info("=" * 50)
        logger.info("Proceeding to checkout - placing full order...")

        try:
            # Step 1-2: item entry → click Next twice
            for step in range(2):
                if not await self._click_wizard_button("Next", f"item-entry-{step+1}"):
                    raise Exception(f"Could not find Next button (item entry step {step+1})")
                await self._wait_for_wizard_transition()

            # Step 3: select ACH Debit Bank payment
            await self._find_and_click_ach()
            await asyncio.sleep(0.5)

            # Step 4-6: checkout Next buttons
            for step in range(3):
                if not await self._click_wizard_button("Next", f"checkout-{step+1}"):
                    raise Exception(f"Could not find Next button at checkout step {step+1}")
                await self._wait_for_wizard_transition()

            # Step 7: Submit button
            if not await self._click_wizard_button("Submit", "submit"):
                raise Exception("Could not find Submit button")
            await self._wait_for_wizard_transition()

            # Step 8: password confirmation
            logger.info("[wizard] Entering confirmation password...")
            password = os.getenv('SITE_PASSWORD')
            if not password:
                raise Exception("SITE_PASSWORD not set - required for order confirmation")

            # ALWAYS capture diagnostic before password attempt so we can see the page
            await self._capture_diagnostic("before_password")
            logger.info("[wizard] Diagnostic captured — now attempting password entry...")

            # Scroll to reveal password field
            await self._scroll_to_bottom()
            await asyncio.sleep(0.5)
            await self._scroll_to_bottom()
            try:
                scroll_more = await self.page.query_selector('a.ScrollForMoreLink, a[data-event="ScrollForMore"]')
                if scroll_more and await scroll_more.is_visible():
                    await scroll_more.click()
                    await asyncio.sleep(0.5)
                    logger.info("[wizard] Clicked 'Scroll for More' link")
            except Exception:
                pass

            # Wait a moment for any lazy-loaded content
            await asyncio.sleep(1.0)

            pw_filled = await self._fill_password_field(password)
            if not pw_filled:
                await self._capture_diagnostic("password_failed")
                raise Exception("Could not enter password - all methods failed")

            await asyncio.sleep(0.3)

            # Step 9: final Submit after password
            logger.info("[wizard] Clicking final Submit after password...")
            if not await self._click_wizard_button("Submit", "final-submit"):
                raise Exception("Could not find final Submit button after password")

            await self._wait_for_wizard_transition()

            elapsed = time.time() - overall_start
            logger.info(f"✓ Order submitted successfully! Total checkout time: {elapsed:.1f}s")
            await asyncio.sleep(1)
            return True

        except Exception as e:
            elapsed = time.time() - overall_start
            logger.error(f"Error during checkout after {elapsed:.1f}s: {e}")
            await self._capture_diagnostic("checkout_failed")
            raise

    async def process_multiple_items(self, items):
        """Process unfilled items: start order, add available ones to cart, submit if any found.
        Returns dict with success, items_ordered, message for reporting."""
        unfilled_items = [i for i in items if i.get('order_filled', '').lower() != 'yes']
        if not unfilled_items:
            return {'success': False, 'items_ordered': [], 'message': 'No unfilled items'}

        # Start new order if not already on item entry page
        current_url = self.page.url
        if 'itemEntry' not in current_url:
            await self.start_order()

        items_found, total_qty_added = await self.check_and_process_items(items)

        if not items_found:
            logger.info("No items available at this time")
            return {'success': False, 'items_ordered': [], 'message': 'No items were available'}

        if total_qty_added < 10:
            logger.warning(f"Order requires minimum 10 quantity total (have {total_qty_added}). Not submitting - add more items.")
            for item in items_found:
                item['order_filled'] = ''
            return {'success': False, 'items_ordered': [], 'message': f'Need min 10 qty (have {total_qty_added})'}

        item_numbers = [str(i['item_number']) for i in items_found]
        logger.info(f"✓ Found {len(items_found)} items, {total_qty_added} total qty: {', '.join(item_numbers)}")

        try:
            await self.submit_order()
            logger.info(f"ORDER SUCCESS: Placed order for items {', '.join(item_numbers)}")
            return {
                'success': True,
                'items_ordered': item_numbers,
                'message': f'Order placed for {len(items_found)} item(s): {", ".join(item_numbers)}'
            }
        except Exception as e:
            logger.error(f"ORDER FAILED: {e}")
            for item in items_found:
                item['order_filled'] = ''
            return {
                'success': False,
                'items_ordered': item_numbers,
                'message': f'Checkout failed: {e}'
            }
    
    async def save_auth_state(self):
        """Save authentication state for future runs"""
        logger.info("Saving authentication state...")
        await self.context.storage_state(path="auth_state.json")
        logger.info("Authentication state saved")
    
    async def cleanup(self):
        """Clean up browser resources"""
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()

def read_csv_file(filename):
    """Read CSV file and return list of items"""
    items = []
    try:
        with open(filename, 'r', newline='') as file:
            reader = csv.DictReader(file)
            for row in reader:
                items.append({
                    'item_number': int(row['item_number']),
                    'quantity': int(row['quantity']),
                    'order_filled': row.get('order_filled', '').strip()
                })
        return items
    except Exception as e:
        logger.error(f"Error reading CSV file: {e}")
        return []

def update_csv_file(filename, items):
    """Update CSV file with order_filled status"""
    try:
        with open(filename, 'w', newline='') as file:
            fieldnames = ['item_number', 'quantity', 'order_filled']
            writer = csv.DictWriter(file, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(items)
        logger.info("CSV file updated")
    except Exception as e:
        logger.error(f"Error updating CSV file: {e}")

def create_sample_csv():
    """Create a sample CSV template"""
    sample_filename = 'orders_template.csv'
    with open(sample_filename, 'w', newline='') as file:
        fieldnames = ['item_number', 'quantity', 'order_filled']
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows([
            {'item_number': '12345', 'quantity': '10', 'order_filled': ''},
            {'item_number': '67890', 'quantity': '5', 'order_filled': ''},
            {'item_number': '11111', 'quantity': '20', 'order_filled': ''}
        ])
    logger.info(f"Created sample CSV template: {sample_filename}")

async def main():
    """Main bot loop"""
    csv_filename = 'orders.csv'
    
    # check if CSV exists
    if not Path(csv_filename).exists():
        logger.warning(f"{csv_filename} not found. Creating sample template...")
        create_sample_csv()
        logger.info(f"Please fill in orders_template.csv and rename it to {csv_filename}")
        return
    
    # create bot instance
    bot = WebAutomationBot(headless=False)
    
    try:
        # setup browser and login
        logger.info("Initializing bot...")
        await bot.setup(use_saved_auth=True)
        
        logger.info("\n" + "="*60)
        logger.info("BOT STARTED - Continuously checking for items")
        logger.info("="*60 + "\n")
        
        while True:
            try:
                # reload CSV each iteration to get latest data
                items = read_csv_file(csv_filename)
                
                # get unfilled items
                unfilled_items = [item for item in items if item.get('order_filled', '').lower() != 'yes']
                
                if not unfilled_items:
                    logger.info("All items completed! Checking for new items in 5 seconds...")
                    await asyncio.sleep(5)
                    continue
                
                logger.info(f"\n--- Checking {len(unfilled_items)} items for availability ---")
                
                # Start a new order only if we are not already on the order page
                current_url = bot.page.url
                if not "itemEntry" in current_url:
                    await bot.start_order()
                
                # check all items and add available ones to cart (one order, min 10 qty)
                items_found, total_qty_added = await bot.check_and_process_items(items)
                
                if items_found and total_qty_added >= 10:
                    item_numbers = [str(item['item_number']) for item in items_found]
                    logger.info(f"\n✓ Found {len(items_found)} items, {total_qty_added} total qty: {', '.join(item_numbers)}")
                    await bot.submit_order()
                    
                    update_csv_file(csv_filename, items)
                    logger.info("Immediately checking for remaining items...")
                elif items_found and total_qty_added < 10:
                    logger.warning(f"Need min 10 qty total (have {total_qty_added}). Reverting - will retry.")
                    for item in items_found:
                        item['order_filled'] = ''
                else:
                    # no items found - wait 1 second before checking again
                    logger.info("No items available. Checking again in 1 second...")
                    await asyncio.sleep(1)
            
            except Exception as e:
                logger.error(f"An error occurred during bot operation: {e}")
                import traceback
                traceback.print_exc()
                
                # Try to re-initialize the bot to recover from a potential crash
                logger.info("Attempting to recover by re-initializing the bot...")
                await bot.cleanup()
                bot = WebAutomationBot(headless=False)
                await bot.setup(use_saved_auth=True)
                
    except KeyboardInterrupt:
        logger.info("\nBot stopped by user")
    except Exception as e:
        logger.error(f"\nFATAL BOT ERROR: {e}")
        import traceback
        traceback.print_exc()
    finally:
        logger.info("Cleaning up...")
        await bot.cleanup()
        logger.info("Bot shutdown complete")

if __name__ == "__main__":
    print("\n" + "="*60)
    print("Mississippi DOR Order Bot")
    print("="*60)
    print("\nPress Ctrl+C to stop the bot\n")
    
    asyncio.run(main())