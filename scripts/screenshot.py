"""Dev helper: launch headless Chromium against the running app and screenshot it.

Usage: uv run python scripts/screenshot.py <path> <output.png> [--click SELECTOR]
Requires the app to already be running (see README "Run Locally").
"""

import sys

from playwright.sync_api import sync_playwright

BASE_URL = "http://localhost:5000"


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "/"
    output = sys.argv[2] if len(sys.argv) > 2 else "screenshot.png"
    click_selector = None
    if "--click" in sys.argv:
        click_selector = sys.argv[sys.argv.index("--click") + 1]

    console_errors = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
        page.goto(f"{BASE_URL}{path}", wait_until="networkidle")
        if click_selector:
            page.click(click_selector)
            page.wait_for_timeout(300)
        page.screenshot(path=output, full_page=True)
        browser.close()

    print(f"Saved screenshot to {output}")
    if console_errors:
        print(f"Console errors ({len(console_errors)}):")
        for err in console_errors:
            print(f"  - {err}")
    else:
        print("No console errors.")


if __name__ == "__main__":
    main()
