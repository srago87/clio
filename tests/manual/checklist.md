# Clio Manual Test Checklist

Smoke test for core voice behaviors. Run after any significant change. Takes ~15 minutes.

---

## Setup

1. `./tests/manual/reset.sh` — restore fixture files to known state
2. `./start.sh` — start the server
3. Open URL on phone, tap mic to begin
4. **Orient Clio** — say this first. Use the relative path (`tests/manual/workspace`) rather than the absolute path — Whisper capitalizes proper-noun-sounding words like "Sean", "Claude", and "Clio", which breaks case-sensitive Linux paths.

   > "Please make a scratchpad note: the test workspace for this session
   > is at tests slash manual slash workspace"

   Expect: Clio confirms the note. No `Working…` badge — `update_scratchpad` is handled inline and skips the status entirely.

   **Verify in Terminal 2** that the workspace exists at the right path:
   ```bash
   ls tests/manual/workspace/
   ```
   You should see: `config.json  hello.py  notes.txt  subdir`

---

## 1. Basic voice pipeline

*Tests: STT → Claude API → TTS with no tool use*

- [ ] **Say:** "What's two plus two?"
  - **Expect:** A spoken answer within a few seconds. No tool calls made.
  - **Verify:** Status badge shows `Transcribing…` → `Thinking…` → clears when audio starts. No `Working…` badge appears.

---

## 2. Read a file

*Tests: `read_file` (auto-approved)*

- [ ] **Say:** "Read the file at tests slash manual slash workspace slash hello dot py"
  - **Expect:** Clio reads it and describes two functions: `greet` and `farewell`.
  - **Note:** `Working…` fires but may flash too briefly to see on a small file — that's expected. Test 15 uses a slower operation to verify the badge properly.

---

## 3. List a directory

*Tests: `list_directory` (auto-approved)*

- [ ] **Say:** "List the files in tests slash manual slash workspace"
  - **Expect:** Clio lists `hello.py`, `notes.txt`, `config.json`, and `subdir`.
  - **Verify:** All four entries mentioned in the response.

---

## 4. Search code

*Tests: `search_code` (auto-approved)*

- [ ] **Say:** "Find all function definitions in tests slash manual slash workspace slash hello dot py"
  - **Expect:** Clio finds and names both `greet` and `farewell`.
  - **Verify:** Both function names are spoken.

---

## 5. Find files by pattern

*Tests: `find_files` (auto-approved)*

- [ ] **Say:** "Find all Python files in tests slash manual slash workspace"
  - **Expect:** Clio returns `hello.py` only.
  - **Verify:** One result, correct filename.

---

## 6. Current time

*Tests: `get_current_time` (auto-approved)*

- [ ] **Say:** "What time is it?"
  - **Expect:** Clio tells the current time.
  - **Verify:** Sounds approximately right.

---

## 7. Write a file — approve

*Tests: `write_file` permission flow (approve path)*

- [ ] **Say:** "Create a file at tests slash manual slash workspace slash output dot txt with the text 'test passed'"
  - **Expect:** Permission overlay appears on phone: "Create output.txt"
  - **Tap Approve**
  - **Verify:** `tests/manual/workspace/output.txt` exists and contains `test passed`

    ```bash
    cat tests/manual/workspace/output.txt
    ```

---

## 8. Edit a file — approve

*Tests: `edit_file` permission flow (approve path)*

- [ ] **Say:** "In tests slash manual slash workspace slash notes dot txt, replace 'Project Alpha' with 'Project Beta'"
  - **Expect:** Permission overlay appears
  - **Tap Approve**
  - **Verify:** First line of `notes.txt` now reads `Meeting notes - Project Beta`

    ```bash
    head -1 tests/manual/workspace/notes.txt
    ```

---

## 9. Edit a file — deny

*Tests: `edit_file` permission flow (deny path)*

- [ ] **Say:** "In tests slash manual slash workspace slash config dot json, change debug to true"
  - **Expect:** Permission overlay appears
  - **Tap Deny**
  - **Expect:** Clio confirms the action was cancelled
  - **Verify:** `config.json` still has `"debug": false`

    ```bash
    grep debug tests/manual/workspace/config.json
    ```

---

## 10. Run a bash command — approve

*Tests: `bash_command` permission flow*

- [ ] **Say:** "Run the command: echo test passed"
  - **Expect:** Permission overlay appears
  - **Tap Approve**
  - **Expect:** Clio reads back the output: "test passed"

---

## 11. Delete a file — approve

*Tests: `delete_file` permission flow*

- [ ] **Say:** "Delete tests slash manual slash workspace slash output dot txt"
  - **Expect:** Permission overlay appears
  - **Tap Approve**
  - **Verify:** `output.txt` is gone

    ```bash
    ls tests/manual/workspace/
    ```

---

## 12. Multi-step task

*Tests: sequential tool use — read then edit*

- [ ] **Say:** "Read tests slash manual slash workspace slash notes dot txt, then add a new line at the end that says 'Action items reviewed'"
  - **Expect:** Clio reads the file first (auto-approved, `Working…` may flash too briefly to see), then requests approval to edit
  - **Tap Approve**
  - **Verify:** Last line of `notes.txt` is `Action items reviewed`

    ```bash
    tail -1 tests/manual/workspace/notes.txt
    ```

---

## 13. Memory update

*Tests: `update_memory` (auto-approved)*

- [ ] **Say:** "Remember that my favorite editor is Vim"
  - **Expect:** Clio confirms it will remember this
  - **Verify:** `memory.md` contains a reference to Vim or the favorite editor

    ```bash
    grep -i vim memory.md
    ```

---

## 14. Path sandboxing

*Tests: `_is_safe_path` blocking sensitive directories*

- [ ] **Say:** "Read the file at home slash sean slash dot s s h slash id underscore rsa"
  - **Expect:** Clio refuses or reports it cannot access that path
  - **Verify:** No SSH key content is spoken. Clio either declines or reports an error.

---

## 15. Status badge — working state

*Tests: the `working` status state (renamed from `executing` in d3dda68)*

- [ ] **Say:** "Search the web for Python asyncio documentation"
  - **Expect:** `Thinking…` while Claude decides to search, then `Working…` with a label like "web_search" while the request is in flight (several seconds — long enough to read the badge clearly)
  - **Verify:** Badge reads **"Working…"** (not "Executing…") with a label and elapsed timer

---

## 16. Mic mute / unmute

*Tests: mic teardown and restart*

- [ ] While mic is active (session started), **tap the mic button**
  - **Expect:** Mic button shows muted state; OS mic indicator light goes off; session glow clears
- [ ] **Tap mic again to unmute**
  - **Expect:** Mic restarts; glow returns; Clio is ready to listen
- [ ] **Say something after unmuting**
  - **Verify:** Clio responds normally

---

## 17. Silence detection auto-send

*Tests: VAD auto-send after ~1s of quiet*

- [ ] Start speaking, then **stop mid-sentence** and stay quiet
  - **Expect:** After ~1 second of silence, the clip sends automatically — no button tap needed
  - **Verify:** Clio responds to what was said

---

## Cleanup

```bash
./tests/manual/reset.sh
```

Restores the workspace to its original state for the next run.
