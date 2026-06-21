# Diff Viewer Stress Test — Large File

This is the large file replacement test. Almost every line is new.

## Part 1: Introduction

The diff viewer should handle large writes cleanly. And it should also handle extremely long lines without breaking the layout or overflowing the container in a way that makes the rest of the diff unreadable. This sentence is intentionally very long to test horizontal overflow behavior in the diff viewer. If the viewer wraps it gracefully or shows a scrollbar, that is a pass. If it breaks the card layout entirely, that is something to fix. Lorem ipsum dolor sit amet consectetur adipiscing elit sed do eiusmod tempor incididunt ut labore et dolore magna aliqua ut enim ad minim veniam quis nostrud exercitation ullamco laboris nisi ut aliquip ex ea commodo consequat.
This section introduces the test scenario.
We are replacing the entire previous file with much more content.
The old file had about 30 lines. This one has over 60.

## Part 2: Numbers

Line one of the numbers section.
Line two of the numbers section.
Line three of the numbers section.
Line four of the numbers section.
Line five of the numbers section.
Line six of the numbers section.
Line seven of the numbers section.
Line eight of the numbers section.
Line nine of the numbers section.
Line ten of the numbers section.

## Part 3: Code Block

```python
class DiffViewer:
    def __init__(self, websocket_url):
        self.url = websocket_url
        self.diffs = []
        self.connected = False

    def connect(self):
        self.connected = True
        print(f"Connected to {self.url}")

    def handle_event(self, event):
        if event["type"] == "diff":
            self.diffs.append(event)
            self.render(event)

    def render(self, diff):
        for hunk in diff["hunks"]:
            for line in hunk["lines"]:
                if line.startswith("+"):
                    print(f"\033[32m{line}\033[0m")
                elif line.startswith("-"):
                    print(f"\033[31m{line}\033[0m")
                else:
                    print(line)
```

## Part 4: Prose

The quick brown fox jumps over the lazy dog.
Sphinx of black quartz, judge my vow.
The five boxing wizards jump quickly.
How valiantly did Beowulf slay the dragon.
Pack my box with five dozen liquor jugs.

## Part 5: End of Test (Rapid Edit)

This is the final section of the large file test. Edited rapidly after notes.md.
If you can read this in the diff viewer, the large write test passed.
All lines above this in Parts 2 through 5 should appear green.
Lines from the old file that don't exist here should appear red.
