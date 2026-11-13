"""QA riêng: server và DB thử nghiệm, không dùng với dashboard vận hành."""
import asyncio
import os
from pathlib import Path

from playwright.async_api import async_playwright, expect
from qa_helpers import login_dashboard


async def main():
    url = os.environ["IOT_QA_URL"]
    assert ":3001" in url, "Chỉ chạy trên cổng QA riêng"
    output = Path(__file__).resolve().parents[1] / "artifacts"
    output.mkdir(exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel="msedge", headless=True)
        page = await browser.new_page(viewport={"width": 1440, "height": 1100})
        errors = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        await page.goto(url, wait_until="networkidle")
        await login_dashboard(page)
        await page.get_by_role("tab", name="Cài đặt", exact=True).click()
        recipient = page.locator("#setting-email-to")
        # Xác nhận đúng DB QA trước khi thực hiện bất kỳ thao tác ghi nào.
        await page.wait_for_function("document.querySelector('#setting-email-to')?.value === 'settings-qa@example.invalid'")
        await page.locator("#setting-temp_red_high").fill("37")
        await page.locator("#setting-hum_red_high").fill("91")
        await recipient.fill("receiver@example.com")
        await page.locator("#setting-email-enabled").click()
        await page.wait_for_timeout(6000)
        assert await page.locator("#setting-temp_red_high").input_value() == "37"
        assert await recipient.input_value() == "receiver@example.com"
        await page.get_by_role("button", name="Lưu cài đặt", exact=True).click()
        await page.get_by_text("Đã lưu. Monitor áp dụng từ lần đo tiếp theo.", exact=True).wait_for()
        await page.reload(wait_until="networkidle")
        await page.get_by_role("tab", name="Cài đặt", exact=True).click()
        await page.wait_for_function("document.querySelector('#setting-email-to')?.value === 'receiver@example.com'")
        await expect(page.locator("#setting-temp_red_high")).to_have_value("37.0")
        await expect(page.locator("#setting-hum_red_high")).to_have_value("91.0")
        await expect(page.locator("#setting-email-enabled")).to_have_attribute("aria-checked", "true")

        await page.locator("#setting-hum_yellow_high").fill("95")
        await page.get_by_role("button", name="Lưu cài đặt", exact=True).click()
        await page.get_by_text("Độ ẩm: cần đỏ thấp < vàng thấp < vàng cao < đỏ cao.", exact=True).wait_for()
        await page.get_by_role("button", name="Tải lại", exact=True).click()
        await expect(page.locator("#setting-hum_yellow_high")).to_have_value("75.0")
        await recipient.fill("invalid")
        await page.get_by_role("button", name="Lưu cài đặt", exact=True).click()
        await page.get_by_text("Địa chỉ email nhận không hợp lệ.", exact=True).wait_for()
        await recipient.fill("receiver@example.com")
        await page.locator("#setting-email-enabled").click()
        await page.get_by_role("button", name="Lưu cài đặt", exact=True).click()
        await page.get_by_text("Đã lưu. Monitor áp dụng từ lần đo tiếp theo.", exact=True).wait_for()
        await page.screenshot(path=str(output / "dashboard-settings.png"), full_page=True)

        # Hai phiên sửa đồng thời: giữ bản nháp và báo xung đột thay vì ghi đè.
        other = await browser.new_page()
        await other.goto(url, wait_until="networkidle")
        await login_dashboard(other)
        await other.get_by_role("tab", name="Cài đặt", exact=True).click()
        await other.wait_for_function("document.querySelector('#setting-temp_red_high')?.value === '37.0'")
        await page.locator("#setting-temp_red_high").fill("38")
        await other.locator("#setting-temp_red_high").fill("39")
        await other.get_by_role("button", name="Lưu cài đặt", exact=True).click()
        await other.get_by_text("Đã lưu. Monitor áp dụng từ lần đo tiếp theo.", exact=True).wait_for()
        await page.wait_for_timeout(6000)
        assert await page.locator("#setting-temp_red_high").input_value() == "38"
        await page.get_by_role("button", name="Lưu cài đặt", exact=True).click()
        await page.get_by_text("Cài đặt đã thay đổi ở phiên khác. Bấm Tải lại rồi chỉnh lại.", exact=True).wait_for()
        await page.get_by_role("button", name="Tải lại", exact=True).click()
        await expect(page.locator("#setting-temp_red_high")).to_have_value("39.0")

        await page.get_by_role("tab", name="Lịch sử", exact=True).click()
        body = await page.locator("body").inner_text()
        for text in ("Mỗi mốc gồm", "mẫu phút mô phỏng theo", "bật trong .env", "Làm mới 5 giây", "Ngưỡng đỏ:"):
            assert text not in body
        await page.set_viewport_size({"width": 390, "height": 844})
        await page.get_by_role("tab", name="Cài đặt", exact=True).click()
        await page.wait_for_timeout(1000)
        assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
        await page.screenshot(path=str(output / "dashboard-settings-mobile.png"), full_page=True)
        assert not errors, errors
        print("PASS: sửa/lưu ngưỡng và email, giữ bản nháp qua realtime, reload, validation, chống ghi đè hai phiên, dọn chữ thừa, mobile")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
