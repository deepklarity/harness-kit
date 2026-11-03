---
name: hk-mobile-app-ui-validation
description: "Drive a native iOS or Android app on a device or simulator through the mobile MCP, screenshot every screen, measure touch targets, and write up mis-taps, clipped text and screens that trap the user as one HTML page with a score out of 10. Triggers on: 'mobile UI audit', 'touch targets', 'test the native app', 'test it on the simulator', or /hk-mobile-app-ui-validation."
allowed-tools: Bash, Read, Grep, Glob, Write, mcp__mobile__*
generated: true
---

# Mobile app UI validation

Runs the app on a real screen and uses it with a thumb: every screen is photographed, every tappable element is measured, and every finding shows the screenshot and the element. It changes no source file.

Scope: native and cross-platform mobile apps through the mobile MCP. A web app viewed on a phone is `hk-web-app-ui-validation`, the Chrome walk at 390 wide; the split is the runtime, not the screen size: an app installed on a device comes here, a URL goes there.

The page is built as `docs/skills/knowledge/hk-writeup/SKILL.md` steps 3 to 5 say, in `docs/skills/knowledge/hk-writeup/SHELL.html`, in the voice of `docs/playbook/tone_and_taste.md`. Read the voice doc before writing a sentence.

## Does this apply?

Look for `react-native` or `expo` in a `package.json`, a `pubspec.yaml`, or an `ios/` or `android/` directory under the project's source folders (skip `node_modules` and any state folder CLAUDE.md names), or an app name or bundle id the user gave. Media queries in web CSS do not count. If nothing matches, say so in one chat line and stop; write no file.

## Workflow

**1. Get the app on a screen.** `mcp__mobile__mobile_list_available_devices`; pick the first booted simulator or connected device, or the one the user named. Never allocate a remote device unless the user named one; none available means stop with one chat line. If the app is not installed, build it the way the project's README says and `mcp__mobile__mobile_install_app`; then `mcp__mobile__mobile_launch_app`. Record `mcp__mobile__mobile_get_screen_size` (points, and the scale) once. Done when the app is in the foreground (`mcp__mobile__mobile_get_foreground_app` says so).

**2. Visit every screen.** From the first screen, `mcp__mobile__mobile_save_screenshot` to `writeup/shots/hk-mobile-app-ui-validation/<screen>.png` and `mcp__mobile__mobile_list_elements_on_screen`; tap each navigation element with `mcp__mobile__mobile_click_on_screen_at_coordinates`, scroll with `mcp__mobile__mobile_swipe_on_screen` to the bottom of each screen, and repeat until no new screen appears or you have 30; say on the page which stopped you. Use `mcp__mobile__mobile_press_button` for back and `mcp__mobile__mobile_type_keys` where a screen needs input. Done when there is a list of screens, each with a PNG on disk, its element list, and the path taken to reach it.

**3. Measure and check.** For each screen, run the checklist (below). Touch targets come from the element list's bounds divided by the scale: anything under 44 by 44 points that a user is meant to tap is a finding. Rotate once per screen with `mcp__mobile__mobile_set_orientation` if the app allows landscape, and screenshot again.

**4. Read the logs.** After the walk, `mcp__mobile__mobile_list_crashes` and `mcp__mobile__mobile_get_device_logs`; a crash during the walk is a severity 5 finding with the screen that caused it.

**5. Point each finding at code** when the source is in reach: grep the app's screens for the element's text or test id and carry the file and lines. Otherwise the screen name and the element's label are the location.

**6. Verify, then cut.** Reopen each cited screenshot and repeat the tap once. Drop anything a maintainer would not change. Up to ten findings; fewer is fine, none means the page says the screens are clean.

**7. Build the page** at `writeup/mobile-app-ui-validation-<subject>.html` at the repo root (`$(git rev-parse --show-toplevel)/writeup/`, gitignored; create the directory if missing), in the order under Page order below. Each finding embeds its PNG from `writeup/shots/hk-mobile-app-ui-validation/` as a base64 data URI; then delete `writeup/shots/hk-mobile-app-ui-validation/` and nothing else.

**8. Report back** in one chat line: where the page went, the score, and the count by severity.

## Page order

1. Kicker "Mobile app UI validation", the app as the title, chips for the device, the OS version, the screen count and the model.
2. **Verdict**: one sentence with the score out of 10 and what set it (the band table in ../README.md), one naming the worst finding, then a `.tbl` of screens visited with the path taken and the smallest tappable element in points.
3. **Findings**, one `section` per band: Mis-taps, traps and crashes (severity 5 and 4), Layout fights the user (3), Nits (2 and 1). Each finding is one `details.row`: `.row-name` is the severity in words ("severity four"), `.row-status` the title as a sentence; the `.dbody` says what a user does and what happens, gives the measurement where there is one, shows the screenshot, and ends with screen, element and `file:lines` in `.mono`.
4. `.foot`: replace the `.said` sentence with one saying which device and OS the app was driven on, how many screens were visited on the named model, and that nothing was changed.

## The checklist

Per screen:

- Every tappable element is at least 44 by 44 points, and no two targets sit closer than 8 points.
- Nothing is clipped or hidden behind the notch, the home indicator, the status bar or the keyboard; a field being typed into stays visible above the keyboard.
- Body text is at least 15 points and the smallest text is readable; contrast passes 4.5:1.
- Content fits the width; no horizontal scroll unless it is a deliberate carousel.
- There is a way back from every screen, and the hardware or gesture back does the same thing as the on-screen one.
- The main action is reachable with one thumb from the bottom half of the screen.
- Lists scroll to their end and the last item is not under a bar.
- Rotation, when allowed, keeps the layout usable and keeps the user's place.
- No crash and no red-box error during the walk.

## Severity and score

Severity is 1 to 5, 5 worst: **5** for a crash or a screen with no way back; **4** when a user mis-taps or cannot read the text; **3** when they can do the task but the layout fights them (cramped targets, text under the keyboard, a list end under a tab bar); **2** for spacing; **1** for alignment.

Score 0 to 10 and the band for each set of findings: [../README.md](../README.md).

## Closing step

Then the closing step in [../README.md](../README.md#closing-step): board rows for severity 4 and 5, a pattern for a recurring cause, one ledger line.

## Example

- Name any state folder to skip.
