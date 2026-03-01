# Mini Spec 001: Rename Home Screen Title

## Summary

Change the home screen title from "My App" to "Pictionary App" and update the subtitle to match the app's purpose.

## Current State

- **File:** `app/index.tsx` (line 50–51)
- **Title:** `My App`
- **Subtitle:** `Minimal reusable mobile scaffold`

## Desired State

- **Title:** `Pictionary App`
- **Subtitle:** `A party word-guessing game`

## Changes Required

1. In `app/index.tsx`, update the title `Text` component from `"My App"` to `"Pictionary App"`.
2. In `app/index.tsx`, update the subtitle `Text` component from `"Minimal reusable mobile scaffold"` to `"A party word-guessing game"`.

## Acceptance Criteria

- Home screen displays "Pictionary App" as the title.
- Home screen displays "A party word-guessing game" as the subtitle.
- No other screens or components are affected.
