# Nap Card

Turns a nap's start and end times into a pastel 1080×1350 JPEG poster.

```bash
pip install pillow numpy
python nap_card.py 12:45 2:10                  # -> nap.jpg
python nap_card.py 1:30 4:45 --name Leo -o leo.jpg
python nap_card.py 11:40am 3:05 --date 2026-11-21
```

- Times without am/pm are treated as afternoon times (`2:10` means 2:10 PM).
- The arc covers 12 PM to 5 PM and stretches automatically if a nap starts earlier or ends later.
- Fonts: Fredoka (SIL Open Font License, see `fonts/OFL-LICENSE.txt`). If the fonts are missing, the script uses a system font instead.

See `samples/` for example output.
