"""Kiểm tra đăng nhập/đổi mật khẩu trên DB QA riêng; không dùng DB vận hành."""
import asyncio
import json
import os
import sqlite3
from pathlib import Path

from playwright.async_api import async_playwright, expect
from qa_helpers import login_dashboard


async def main():
    url = os.environ["IOT_QA_URL"]
    assert ":3001" in url
    root = Path(__file__).resolve().parents[1]
    database = Path(os.environ["IOT_DB_FILE"]).resolve()
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as db:
        assert json.loads(db.execute("SELECT value FROM kv WHERE key='database_role'").fetchone()[0]) == "qa"
        record = json.loads(db.execute("SELECT value FROM kv WHERE key='dashboard_auth'").fetchone()[0])
    output = root / "artifacts"
    output.mkdir(exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel="msedge", headless=True)
        context = await browser.new_context(viewport={"width": 1440, "height": 1000})
        page = await context.new_page()
        errors, received = [], []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.on("websocket", lambda ws: ws.on("framereceived", lambda frame: received.append(str(frame))))
        await page.goto(url, wait_until="networkidle")
        await page.get_by_role("button", name="Đăng nhập", exact=True).wait_for()
        assert await page.get_by_role("tab").count() == 0
        await expect(page.locator("#login-password")).to_have_attribute("type", "password")
        await page.wait_for_timeout(1000)
        before_login = "\n".join(received)
        assert "settings-qa@example.invalid" not in before_login
        assert "receiver@example.com" not in before_login
        assert record["digest"] not in before_login and record["version"] not in before_login
        await page.screenshot(path=str(output / "dashboard-login.png"), full_page=True)
        await page.set_viewport_size({"width": 390, "height": 844})
        assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
        await page.screenshot(path=str(output / "dashboard-login-mobile.png"), full_page=True)
        await page.set_viewport_size({"width": 1440, "height": 1000})
        await page.locator("#login-password").fill("wrong123")
        await page.get_by_role("button", name="Đăng nhập", exact=True).click()
        await page.get_by_text("Mật khẩu không đúng.", exact=True).wait_for()
        assert await page.get_by_role("tab").count() == 0
        await login_dashboard(page, "123456")

        other_context = await browser.new_context()
        other = await other_context.new_page()
        await other.goto(url, wait_until="networkidle")
        await login_dashboard(other, "123456")
        await page.get_by_role("tab", name="Cài đặt", exact=True).click()

        async def change(current, new, confirmation):
            await page.locator("#password-current").fill(current)
            await page.locator("#password-new").fill(new)
            await page.locator("#password-confirm").fill(confirmation)
            await page.get_by_role("button", name="Đổi mật khẩu", exact=True).click()

        await change("wrong123", "qa-new-pass-2026", "qa-new-pass-2026")
        await page.get_by_text("Mật khẩu hiện tại không đúng.", exact=True).wait_for()
        await change("123456", "qa-new-pass-2026", "not-matching")
        await page.get_by_text("Mật khẩu xác nhận chưa khớp.", exact=True).wait_for()
        await change("123456", "qa-new-pass-2026", "qa-new-pass-2026")
        await page.get_by_text("Đã đổi mật khẩu.", exact=True).wait_for()
        await expect(page.locator("#password-current")).to_have_value("")
        await expect(page.locator("#password-new")).to_have_value("")
        await page.screenshot(path=str(output / "dashboard-password-settings.png"), full_page=True)
        # Các phiên dùng mật khẩu cũ bị thu hồi trong vòng làm mới.
        await other.locator("#login-password").wait_for(timeout=15000)
        assert await other.get_by_role("tab").count() == 0
        await page.get_by_role("button", name="Đăng xuất", exact=True).click()
        await page.locator("#login-password").wait_for()
        assert await page.get_by_role("tab").count() == 0
        await page.locator("#login-password").fill("123456")
        await page.get_by_role("button", name="Đăng nhập", exact=True).click()
        await page.get_by_text("Mật khẩu không đúng.", exact=True).wait_for()
        await login_dashboard(page, "qa-new-pass-2026")
        await page.reload(wait_until="networkidle")
        await page.get_by_role("tab", name="Dự báo", exact=True).wait_for(timeout=15000)
        new_context = await browser.new_context()
        fresh = await new_context.new_page()
        await fresh.goto(url, wait_until="networkidle")
        await fresh.locator("#login-password").wait_for()
        await login_dashboard(fresh, "qa-new-pass-2026")
        assert not errors, errors
        print("PASS: không lộ dữ liệu trước đăng nhập, sai/đúng mật khẩu, đổi mật khẩu, thu hồi phiên cũ, đăng xuất, tải lại trang, phiên mới, mobile")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
