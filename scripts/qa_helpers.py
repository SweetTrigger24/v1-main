"""Hỗ trợ đăng nhập cho các bài kiểm tra giao diện local."""
import os


async def login_dashboard(page, password=None):
    await page.locator("#login-password").fill(password or os.getenv("IOT_QA_PASSWORD", "123456"))
    await page.get_by_role("button", name="Đăng nhập", exact=True).click()
    await page.get_by_role("tab", name="Dự báo", exact=True).wait_for(timeout=20000)
