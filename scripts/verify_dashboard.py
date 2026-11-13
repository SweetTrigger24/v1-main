"""Kiểm tra UI tùy chọn: pip install playwright; chạy monitor demo + dashboard trước."""
import asyncio
import os
from pathlib import Path

from playwright.async_api import async_playwright
from qa_helpers import login_dashboard


async def main():
    output = Path(__file__).resolve().parents[1] / "artifacts"
    output.mkdir(exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel="msedge", headless=True)
        page = await browser.new_page(viewport={"width": 1440, "height": 1100}, device_scale_factor=1)
        errors = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        await page.goto(os.getenv("IOT_QA_URL", "http://127.0.0.1:3000"), wait_until="networkidle")
        await login_dashboard(page)
        await page.get_by_text("MÔ PHỎNG · không phải số đo thật", exact=True).wait_for(timeout=30000)
        await page.wait_for_selector(".recharts-line-curve")
        assert await page.get_by_text("Chu kỳ ngày (dự phòng)", exact=True).count() == 0
        assert await page.get_by_text("LSTM nhiều bước · 12 giờ", exact=True).count() == 0
        await page.get_by_text("Thấp nhất – cao nhất trong 12 giờ tới", exact=True).first.wait_for()
        humidity_axis = page.locator(".recharts-wrapper").nth(1).locator('svg text[orientation="left"]')
        assert [float(t) for t in await humidity_axis.all_text_contents()] == [0, 20, 40, 60, 80, 100]
        await page.screenshot(path=str(output / "dashboard-desktop.png"), full_page=True)
        await page.get_by_role("combobox").click()
        await page.get_by_role("option", name="1", exact=True).click()
        await page.wait_for_timeout(5500)
        assert await page.get_by_role("combobox").inner_text() == "1"
        await page.get_by_text("Thấp nhất – cao nhất trong 1 giờ tới", exact=True).first.wait_for()
        await page.get_by_role("tab", name="Lịch sử", exact=True).click()
        dates = page.locator('input[type="datetime-local"]')
        await dates.nth(0).fill("2026-10-24T00:00")
        await dates.nth(1).fill("2026-10-27T23:59")
        await page.get_by_role("combobox").click()
        await page.get_by_role("option", name="Theo ngày", exact=True).click()
        await page.wait_for_timeout(5500)
        assert await page.locator(".recharts-line-curve").count() >= 2
        history_humidity_axis = page.locator(".recharts-wrapper").nth(1).locator('svg text[orientation="left"]')
        assert [float(t) for t in await history_humidity_axis.all_text_contents()] == [0, 20, 40, 60, 80, 100]
        async with page.expect_download() as download_info:
            await page.get_by_role("button", name="Tải CSV").click()
        download = await download_info.value
        target = output / "lich_su_qa.csv"
        await download.save_as(str(target))
        lines = target.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 5, f"Mong đợi 4 ngày lịch sử, nhận {len(lines) - 1}"
        assert "temp_min" in lines[0]
        await page.screenshot(path=str(output / "dashboard-history.png"), full_page=True)
        await page.get_by_role("tab", name="Dự báo", exact=True).click()
        await page.set_viewport_size({"width": 390, "height": 844})
        await page.wait_for_timeout(1000)
        assert await page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
        await page.screenshot(path=str(output / "dashboard-mobile.png"), full_page=True)
        assert not errors, errors
        print("PASS: realtime demo, dự báo 1h, lịch sử 4 ngày, xuất CSV, mobile, không có lỗi JavaScript")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
