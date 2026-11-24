# Manual QA checklist (Windows 10/11)

The Qt layer (tray, hotkey, snipping overlay, result window) and the Win32 foreground
detection need a real desktop, so they are verified by hand. Tick each box on your own
machine **before** you publish the repository or quote a feature on a CV.

Start with the diagnosis, which reports missing packages, the Tesseract path and OCR packs:

```bash
python main.py --check
```

## Setup
- [ ] `pip install -r requirements-dev.txt` finishes without errors
- [ ] `pytest -v` -> every test `PASSED` (GUI-free, runs anywhere)
- [ ] `python scripts/benchmark.py` prints the CER / WER / latency tables

## Application
- [ ] `python main.py` -> tray icon appears; tooltip shows the hotkey
- [ ] Press the hotkey, drag over a **Dark Mode** UI (VS Code, Discord) -> text is recognised and the
      card shows `Context: VS Code (IDE)` (or `Context: General` for unknown apps)
- [ ] Snip a 3-4 line paragraph -> lines are merged into one card before translation
- [ ] Snip a menu with shortcuts (Chrome 3-dot menu) -> one card per item, shortcut kept (`... (Ctrl+T)`)
- [ ] Click `+ Save` on a Google card -> entry appears in `database.json`; scanning it again shows `Local DB`
- [ ] Change the target language to Japanese/Korean, quit from the tray, restart -> language is remembered
- [ ] Type slowly in the language box (`J`, `Ja`, `Jap`) -> **nothing** is translated until you pick/confirm a language
- [ ] Open History, go back -> no duplicate history entry and no re-translation
- [ ] Disconnect the network, scan a word that is not in the glossary -> UI stays responsive, card shows `Offline / Error`
- [ ] Glossary hits still work offline

## Multi-monitor / High-DPI
- [ ] Second monitor: snip text there -> result window opens on that monitor
- [ ] Display scale 100 % / 125 % / 150 %: recognised text is as sharp as at 100 %
- [ ] Mixed scales (e.g. 100 % laptop + 150 % external): selection lands on the right pixels

## Robustness
- [ ] Rename/uninstall Tesseract temporarily -> tray balloon + overlay explain how to fix it (no crash)
- [ ] Set a hotkey already used by another tool -> app still starts; change it in Settings
- [ ] Start a second instance -> the old one is replaced (only one tray icon)
- [ ] `git status` is clean of `config.json`, `history.json`, `capture.png`, `app.log` before `git push`
