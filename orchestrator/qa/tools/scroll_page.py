"""
scroll_page.py — the tool Hercules does not ship, loaded through the extension
point it does ship (`ADDITIONAL_TOOL_DIRS`). Nothing is forked for this.

**Why it exists.** Hercules has no scroll tool. Its whole browser toolset is
open/click/hover/entertext/select/slider/upload/get_*/press_key_combination —
so the only way it can scroll is to press PageDown or End, and a key-press
scroll only works when focus is already on a scrollable element. On
`project-three`, 2026-08-19, it pressed PageDown **30 times** and End
**18 times** and moved the page **zero pixels**. That site gates its entire
interactive surface behind 99% scroll progress, so all ten failures in an
eleven-scenario run were downstream of one scroll that never happened, and the
run effectively tested the header.

The page was never the problem: a single `window.scrollTo(0, scrollHeight)`
through `agent-browser` took it 0% → 100% and revealed everything.

**Two details that are not incidental.**

*It steps rather than jumping.* A scroll-driven page computes its state from
scroll position on a frame loop; landing at the bottom in one assignment can
skip the intermediate states an animation needs to run through. Stepping costs
milliseconds and is the difference between a reveal that completes and one that
snaps.

*It scrolls the right thing.* Plenty of apps scroll an inner element rather than
the document. If the document has no room to move, this finds the tallest
element that does and scrolls that instead, and says which it used — so a
scroll that did nothing is visible as a fact rather than inferred from a later
assertion failing.
"""
import time
from typing import Annotated

from testzeus_hercules.core.playwright_manager import PlaywrightManager
from testzeus_hercules.core.tools.tool_registry import tool
from testzeus_hercules.telemetry import EventData, EventType, add_event
from testzeus_hercules.utils.logger import logger

_MEASURE = """() => {
  const de = document.documentElement;
  const docRoom = de.scrollHeight - window.innerHeight;
  if (docRoom > 8) {
    return {target: 'document', room: docRoom, pos: window.scrollY};
  }
  let best = null, bestRoom = 0;
  for (const el of document.querySelectorAll('*')) {
    const room = el.scrollHeight - el.clientHeight;
    if (room > bestRoom) {
      const s = getComputedStyle(el);
      if (/(auto|scroll)/.test(s.overflowY)) { best = el; bestRoom = room; }
    }
  }
  if (!best) return {target: 'none', room: docRoom, pos: window.scrollY};
  best.setAttribute('data-hercules-scroll-target', '1');
  return {target: 'element', room: bestRoom, pos: best.scrollTop};
}"""


def _step(target: str, to: float) -> str:
    if target == "element":
        return ("(y) => { const el = document.querySelector('[data-hercules-scroll-target]');"
                " if (el) el.scrollTop = y; }")
    return "(y) => window.scrollTo(0, y)"


@tool(
    agent_names=["browser_nav_agent"],
    description=(
        "Scroll the page. Use this INSTEAD OF pressing PageDown or End — a key press "
        "only scrolls when focus is already on a scrollable element, and on many pages "
        "it moves nothing at all. Pass to='bottom', 'top', or a percentage 0-100. "
        "Scrolls in steps so scroll-driven animations complete, and reports where it "
        "actually ended up so you can tell a scroll that worked from one that did not."
    ),
    name="scroll_page",
)
async def scroll_page(
    to: Annotated[str, "'bottom', 'top', or a percentage such as '50'"] = "bottom",
) -> Annotated[str, "What was scrolled, from where to where, and whether it moved"]:
    add_event(EventType.INTERACTION, EventData(detail="scroll_page"))
    started = time.time()

    browser_manager = PlaywrightManager()
    page = await browser_manager.get_current_page()
    if page is None:
        raise ValueError("No active page found. openurl opens a new page.")
    await browser_manager.wait_for_load_state_if_enabled(page=page)

    info = await page.evaluate(_MEASURE)
    target, room, start_pos = info["target"], float(info["room"]), float(info["pos"])

    if target == "none" or room <= 8:
        return (f"Nothing to scroll: the page is {room:.0f}px taller than the viewport, "
                f"so it already shows everything. Position unchanged at {start_pos:.0f}.")

    key = (to or "bottom").strip().lower()
    if key == "bottom":
        end = room
    elif key == "top":
        end = 0.0
    else:
        try:
            end = max(0.0, min(100.0, float(key.rstrip("%")))) / 100.0 * room
        except ValueError:
            return (f"'{to}' is not a scroll position. Use 'bottom', 'top', "
                    f"or a percentage such as '50'.")

    setter = _step(target, end)
    steps = 12
    for i in range(1, steps + 1):
        await page.evaluate(setter, start_pos + (end - start_pos) * i / steps)
        await page.wait_for_timeout(90)          # let the page's frame loop run
    await page.wait_for_timeout(350)

    after = await page.evaluate(_MEASURE)
    end_pos = float(after["pos"])
    moved = abs(end_pos - start_pos)
    logger.info(f"scroll_page: {target} {start_pos:.0f} -> {end_pos:.0f} of {room:.0f} "
                f"in {time.time() - started:.1f}s")

    if moved < 2:
        return (f"SCROLL DID NOT MOVE. Tried to scroll the {target} from {start_pos:.0f} "
                f"to {end:.0f} of {room:.0f} available, and it stayed at {end_pos:.0f}. "
                f"Report this rather than concluding that content further down is absent.")
    return (f"Scrolled the {target} from {start_pos:.0f} to {end_pos:.0f} "
            f"of {room:.0f} available ({end_pos / room * 100:.0f}%).")
