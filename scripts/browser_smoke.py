"""Real-browser acceptance check using synthetic credentials only."""

import os
from pathlib import Path

from playwright.sync_api import sync_playwright


def main():
    base = os.environ.get("BANKING_API_URL", "http://127.0.0.1:8200")
    artifacts = Path(".local/screenshots")
    artifacts.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1050}, color_scheme="dark")
        page.goto(base + "/assistant", wait_until="domcontentloaded")
        page.get_by_label("Password", exact=True).fill("SyntheticDemo!42")
        page.get_by_role("button", name="Sign in securely", exact=True).click()
        page.get_by_text("Welcome, Demo1", exact=False).wait_for(timeout=30000)
        assert "securebank_ui" not in page.evaluate("document.cookie")
        # OS dark mode must not introduce pale text on the app's light cards.
        for scheme in ("light", "dark"):
            page.emulate_media(color_scheme=scheme)
            page.get_by_text("Quick actions", exact=True).click()
            button = page.get_by_role("button", name="Check my balance", exact=True)
            assert button.evaluate("el => getComputedStyle(el).color") != "rgb(255, 255, 255)"
            assert page.locator("#customer-profile h3").evaluate("el => getComputedStyle(el).color") not in (
                "rgb(255, 255, 255)",
                "rgb(241, 245, 249)",
            )
            page.get_by_text("Quick actions", exact=True).click()
        assert not page.locator("#action-card").is_visible()
        assert not page.locator("#transaction-panel").is_visible()
        page.set_viewport_size({"width": 390, "height": 844})
        page.evaluate("window.scrollTo(0, 0)")
        assert page.locator("#chat-window").bounding_box()["y"] < 650
        page.set_viewport_size({"width": 1440, "height": 1050})
        page.get_by_label("Select an account", exact=True).click()
        page.get_by_role("option").first.click()
        message = page.get_by_label("Message", exact=True)
        message.fill("Show my balance and last five transactions")
        page.get_by_role("button", name="Send message", exact=True).click()
        page.get_by_text("Your available balance", exact=False).first.wait_for(timeout=40000)
        page.screenshot(path=str(artifacts / "desktop.png"), full_page=True)
        message.fill("Request a checkbook")
        page.get_by_role("button", name="Send message", exact=True).click()
        page.get_by_text("Deliver to your registered address", exact=False).first.wait_for(timeout=30000)
        page.reload(wait_until="domcontentloaded")
        page.get_by_text("Welcome, Demo1", exact=False).wait_for(timeout=30000)
        page.get_by_text("Deliver to your registered address", exact=False).first.wait_for(timeout=30000)
        page.get_by_role("button", name="Confirm proposal", exact=True).click()
        otp = page.get_by_label("Development verification code", exact=True)
        otp.wait_for(timeout=20000)
        page.reload(wait_until="domcontentloaded")
        otp.wait_for(timeout=30000)
        assert page.get_by_role("button", name="Confirm proposal", exact=True).is_disabled()
        page.get_by_role("button", name="Send message", exact=True).click()
        assert otp.is_visible()
        otp.fill("654321")
        page.get_by_role("button", name="Verify and submit", exact=True).click()
        page.get_by_text("Request submitted successfully", exact=True).wait_for(timeout=30000)
        assert not page.locator("#action-card").is_visible()
        assert otp.count() == 0 or otp.input_value() == ""
        assert "654321" not in page.locator("#chat-window").inner_text()
        page.set_viewport_size({"width": 768, "height": 1024})
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 5")
        page.set_viewport_size({"width": 390, "height": 844})
        page.screenshot(path=str(artifacts / "mobile.png"), full_page=True)
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 5")
        page.get_by_role("button", name="Clear conversation", exact=True).click()
        page.get_by_role("button", name="Sign out", exact=True).click()
        page.get_by_role("button", name="Sign in securely", exact=True).wait_for(timeout=30000)
        browser.close()
    print(
        "Desktop/tablet/mobile browser flow passed with proposal restoration: login, combined query, confirmed checkbook, hidden session cookie, cleared OTP, logout."
    )


if __name__ == "__main__":
    main()
