#!/usr/bin/env python3
"""
Fetch LA Times sudoku puzzles and compose a PDF:
  Page 1: Easy + Medium (side by side)
  Page 2: Expert + Impossible (side by side)

Usage: python3 latimes_sudoku.py [output_dir]
"""

import os
import sys
from datetime import date
from pathlib import Path
from playwright.sync_api import sync_playwright, Page
from fpdf import FPDF
from PIL import Image


OUTPUT_DIR = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.home()
TODAY = date.today()
TODAY_STR = TODAY.strftime("%Y-%m-%d")
TODAY_LONG = TODAY.strftime("%B %d, %Y")
BASE_URL = "https://www.latimes.com/games/sudoku"

# In CI, run headless with xvfb providing a virtual display
IS_CI = os.environ.get("CI") == "true"


def setup_browser(p):
    browser = p.chromium.launch(
        headless=False,  # always headed — xvfb provides display in CI
        args=["--disable-blink-features=AutomationControlled"],
    )
    context = browser.new_context(
        viewport={"width": 1280, "height": 900},
        user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
    )
    page = context.new_page()
    page.add_init_script(
        "Object.defineProperty(navigator, 'webdriver', { get: () => undefined });"
    )
    return browser, page


def navigate_to_sudoku_page(page: Page):
    """Load the LA Times sudoku page, dismiss terms, wait for ad."""
    page.goto(BASE_URL, wait_until="domcontentloaded", timeout=60_000)
    page.wait_for_timeout(3_000)

    # Dismiss terms modal
    try:
        btn = page.get_by_role("button", name="Continue")
        btn.wait_for(state="visible", timeout=5_000)
        btn.click()
        page.wait_for_timeout(2_000)
        print("  Dismissed terms modal")
    except Exception:
        pass

    # Wait for ad to clear
    print("  Waiting for ad to clear...")
    try:
        page.locator("button", has_text="Easy").wait_for(
            state="visible", timeout=120_000
        )
        print("  Ad cleared")
    except Exception:
        page.wait_for_timeout(45_000)
        print("  Ad wait timed out")


def find_game_frame(page: Page):
    """Find the Amuse Labs game iframe."""
    for frame in page.frames:
        if "amuselabs" in frame.url:
            return frame
    return None


def close_menus(frame):
    """Close any open hamburger menus."""
    try:
        frame.page.keyboard.press("Escape")
        frame.wait_for_timeout(500)
        menu_btn = frame.locator(
            ".hamburger, .menu-btn, .nav-toggle, [class*='hamburger']"
        ).first
        if menu_btn.is_visible():
            menu_btn.click()
            frame.wait_for_timeout(500)
    except Exception:
        pass


def screenshot_grid(frame, path: Path) -> bool:
    """Screenshot the sudoku grid from inside the game iframe."""
    close_menus(frame)

    for sel in [".crossword.sudoku", ".crossword", ".grid-area"]:
        try:
            el = frame.locator(sel).first
            if el.is_visible():
                el.screenshot(path=str(path))
                box = el.bounding_box()
                if box:
                    print(
                        f"  Captured grid via {sel} ({box['width']:.0f}x{box['height']:.0f})"
                    )
                return True
        except Exception:
            continue
    return False


def reveal_and_screenshot(frame, path: Path) -> bool:
    """Reveal all answers in the puzzle and screenshot the solved grid."""
    close_menus(frame)

    # Click the "Reveal" button in the toolbar, then "Puzzle" to reveal all
    try:
        # Try clicking Reveal button
        reveal_btn = frame.locator("text=Reveal").first
        if reveal_btn.is_visible():
            reveal_btn.click()
            frame.wait_for_timeout(500)
            # Click "Puzzle" to reveal the whole puzzle
            puzzle_btn = frame.locator("text=Puzzle").first
            if puzzle_btn.is_visible():
                puzzle_btn.click()
                frame.wait_for_timeout(500)
            # Confirm if there's a confirmation dialog
            try:
                confirm = frame.locator("text=OK, text=Yes, text=Confirm, button:has-text('Reveal')").first
                if confirm.is_visible():
                    confirm.click()
                    frame.wait_for_timeout(500)
            except Exception:
                pass
            frame.wait_for_timeout(1_000)
            print("  Revealed answers")
        else:
            # Try via the hamburger menu
            menu_btn = frame.locator("[class*='hamburger'], .menu-btn").first
            if menu_btn.is_visible():
                menu_btn.click()
                frame.wait_for_timeout(500)
                reveal_link = frame.locator("text=Reveal").first
                if reveal_link.is_visible():
                    reveal_link.click()
                    frame.wait_for_timeout(500)
                    puzzle_btn = frame.locator("text=Puzzle").first
                    if puzzle_btn.is_visible():
                        puzzle_btn.click()
                        frame.wait_for_timeout(500)
                    try:
                        confirm = frame.locator("text=OK, text=Yes, text=Confirm").first
                        if confirm.is_visible():
                            confirm.click()
                            frame.wait_for_timeout(500)
                    except Exception:
                        pass
                    frame.wait_for_timeout(1_000)
                    print("  Revealed answers via menu")

        close_menus(frame)
        return screenshot_grid(frame, path)
    except Exception as e:
        print(f"  Could not reveal answers: {e}")
        return False


def wait_for_puzzle_frame(page: Page, timeout_loops=24):
    """Wait for the amuselabs iframe to load."""
    for _ in range(timeout_loops):
        game_frame = find_game_frame(page)
        if game_frame:
            return game_frame
        page.wait_for_timeout(5_000)
    return None


def handle_date_picker(page: Page, game_frame):
    """If we hit a date picker, click the first entry and re-find the puzzle frame."""
    if "date-picker" not in game_frame.url:
        return game_frame

    print("  Date picker detected, clicking first entry...")
    try:
        first_entry = game_frame.locator("a, .date-item, li").first
        first_entry.wait_for(state="visible", timeout=15_000)
        first_entry.click()
        print("  Clicked first date")
    except Exception:
        iframe_el = page.locator("#amuselabs-module-container iframe").first
        iframe_box = iframe_el.bounding_box()
        if iframe_box:
            page.mouse.click(
                iframe_box["x"] + iframe_box["width"] / 2,
                iframe_box["y"] + 50,
            )

    page.wait_for_timeout(10_000)

    # Re-find the game frame (should now be the puzzle, not date-picker)
    for _ in range(24):
        for frame in page.frames:
            if "amuselabs" in frame.url and "date-picker" not in frame.url:
                return frame
        page.wait_for_timeout(5_000)

    # Fallback: return whatever amuselabs frame exists
    for frame in page.frames:
        if "amuselabs" in frame.url:
            return frame
    return None


def wait_for_grid_and_screenshot(page, game_frame, screenshot_path):
    """Wait for grid to render, screenshot puzzle, then reveal and screenshot answers."""
    try:
        game_frame.locator(".crossword.sudoku, .crossword").first.wait_for(
            state="visible", timeout=30_000
        )
    except Exception:
        game_frame.wait_for_load_state("networkidle")
        page.wait_for_timeout(5_000)

    # Screenshot the unsolved puzzle
    if not screenshot_grid(game_frame, screenshot_path):
        iframe_el = page.locator("#amuselabs-module-container iframe").first
        if iframe_el.is_visible():
            iframe_el.screenshot(path=str(screenshot_path))
            print("  Captured iframe element (fallback)")
        else:
            page.screenshot(path=str(screenshot_path))
            print("  Captured full page (fallback)")

    # Now reveal answers and screenshot the solved puzzle
    answer_path = screenshot_path.parent / screenshot_path.name.replace("sudoku_", "answer_")
    if reveal_and_screenshot(game_frame, answer_path):
        return screenshot_path, answer_path
    else:
        print("  Could not capture answer grid")
        return screenshot_path, None


def capture_standard(page: Page, difficulty: str) -> Path | None:
    """Capture a standard difficulty puzzle."""
    screenshot_path = OUTPUT_DIR / f"sudoku_{difficulty}_{TODAY_STR}.png"
    print(f"\n--- {difficulty.upper()} ---")

    navigate_to_sudoku_page(page)

    page.locator("button", has_text=difficulty.capitalize()).click()
    page.wait_for_timeout(2_000)
    print(f"  Selected {difficulty}")

    # Click the first date entry via coordinates (web component, not in DOM)
    amuse_box = page.locator("ps-amuse-labs").bounding_box()
    if amuse_box:
        click_x = amuse_box["x"] + amuse_box["width"] / 2
        click_y = amuse_box["y"] + 50
        page.mouse.click(click_x, click_y)
        print("  Clicked first date entry")
    else:
        page.mouse.click(400, 300)

    print("  Waiting for puzzle to load...")
    page.wait_for_timeout(10_000)

    game_frame = wait_for_puzzle_frame(page)
    if not game_frame:
        print("  ERROR: Game iframe not found")
        page.screenshot(path=str(screenshot_path))
        return screenshot_path, None

    print(f"  Game iframe found: {game_frame.url[:80]}")
    game_frame = handle_date_picker(page, game_frame)

    if not game_frame:
        print("  ERROR: Could not load puzzle")
        page.screenshot(path=str(screenshot_path))
        return screenshot_path, None

    return wait_for_grid_and_screenshot(page, game_frame, screenshot_path)


def capture_impossible(page: Page) -> Path | None:
    """Capture the Impossible Sudoku."""
    screenshot_path = OUTPUT_DIR / f"sudoku_impossible_{TODAY_STR}.png"
    print(f"\n--- IMPOSSIBLE ---")

    navigate_to_sudoku_page(page)

    page.locator("a", has_text="Impossible Sudoku").first.click()
    print("  Clicked Impossible Sudoku link")

    page.wait_for_timeout(10_000)

    game_frame = wait_for_puzzle_frame(page)
    if not game_frame:
        print("  ERROR: Game iframe not found")
        page.screenshot(path=str(screenshot_path))
        return screenshot_path, None

    print(f"  Game iframe found: {game_frame.url[:80]}")
    game_frame = handle_date_picker(page, game_frame)

    if not game_frame:
        print("  ERROR: Could not load puzzle")
        page.screenshot(path=str(screenshot_path))
        return screenshot_path, None

    print(f"  Puzzle frame: {game_frame.url[:80]}")
    return wait_for_grid_and_screenshot(page, game_frame, screenshot_path)


def build_pdf(screenshots: dict[str, Path], output_path: Path):
    """
    2-page landscape PDF:
      Page 1: Easy (left) + Medium (right)
      Page 2: Expert (left) + Impossible (right)
    """
    page_w, page_h = 792, 612
    margin = 40
    gap = 30
    label_h = 20
    half_w = (page_w - 2 * margin - gap) / 2
    available_h = page_h - 2 * margin - label_h - 10

    pdf = FPDF(orientation="L", unit="pt", format="letter")
    pdf.set_auto_page_break(auto=False)

    page_configs = [
        [("easy", f"{TODAY_LONG} - Easy"), ("medium", f"{TODAY_LONG} - Medium")],
        [
            ("expert", f"{TODAY_LONG} - Expert"),
            ("impossible", f"{TODAY_LONG} - Impossible"),
        ],
    ]

    for page_puzzles in page_configs:
        pdf.add_page()

        for i, (diff, label) in enumerate(page_puzzles):
            x = margin + i * (half_w + gap)
            img_path = screenshots.get(diff)

            if img_path and img_path.exists():
                img = Image.open(img_path)
                iw, ih = img.size
                scale = min(half_w / iw, available_h / ih)
                draw_w = iw * scale
                draw_h = ih * scale

                img_x = x + (half_w - draw_w) / 2
                img_y = margin

                pdf.image(str(img_path), img_x, img_y, draw_w, draw_h)

                pdf.set_font("Helvetica", "", 10)
                pdf.set_xy(x, img_y + draw_h + 5)
                pdf.cell(half_w, label_h, label, align="C")
            else:
                pdf.set_font("Helvetica", "I", 12)
                pdf.text(x + 10, margin + 30, f"({diff} - not captured)")

    pdf.output(str(output_path))
    print(f"\nPDF saved: {output_path}")


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    pdf_path = OUTPUT_DIR / f"sudoku_{TODAY_STR}.pdf"
    answers_path = OUTPUT_DIR / f"sudoku_answers_{TODAY_STR}.pdf"

    with sync_playwright() as p:
        browser, page = setup_browser(p)

        screenshots = {}
        answer_screenshots = {}

        for diff in ["easy", "medium", "expert"]:
            try:
                puzzle, answer = capture_standard(page, diff)
                if puzzle:
                    screenshots[diff] = puzzle
                if answer:
                    answer_screenshots[diff] = answer
            except Exception as e:
                print(f"  ERROR: {e}")

        try:
            puzzle, answer = capture_impossible(page)
            if puzzle:
                screenshots["impossible"] = puzzle
            if answer:
                answer_screenshots["impossible"] = answer
        except Exception as e:
            print(f"  ERROR: {e}")

        browser.close()

    build_pdf(screenshots, pdf_path)
    if answer_screenshots:
        build_pdf(answer_screenshots, answers_path)
    print(f"Done!")


if __name__ == "__main__":
    main()
