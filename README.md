# Duplicate Finder

A desktop app (PySide6) that scans a folder and all its subfolders for
exact-duplicate files: files with identical content, detected via size
pre-filtering followed by hashing (blake2b), regardless of filename.

Groups can contain more than 2 files (e.g. 5 copies of the same photo).

## Performance / scale

Built to handle 100,000+ files:

- A SQLite cache (`%LOCALAPPDATA%\DuplicateFinder\cache.db`) stores one row
  per scanned file. Duplicate grouping is done with indexed SQL `GROUP BY`
  queries instead of building large structures in Python.
- Files are only hashed if they share a size with at least one other file
  (cheap `os.stat` pass first), and only fully hashed if a cheap partial
  hash (first+last 64KB) also matches.
- Hashing runs in a process pool across all CPU cores.
- Rescanning the same folder skips files whose size and modified time
  haven't changed — their previous hash is reused.

## Setup

```
pip install -r requirements.txt
python app.py
```

## Usage

1. Browse to (or type) the folder to scan.
2. Click **Start Scan**. Progress is shown per stage (walking, hashing).
3. Results appear as expandable "Exact duplicates" groups, each listing
   every file in that group with its folder.
4. Double-click a file, or select it and click **Show Details**, to see
   its full path, size, timestamps, and hash.
5. Use the **Search** box to filter the results list by name or path.
6. Right-click a file for **Open Containing Folder** or **Delete File...**.

## Deleting files

You can delete a single file at a time (select it, then **Delete File...**,
or use the right-click menu). Safeguards:

- **Only one file per action.** The results list only allows selecting one
  row at a time, so it's not possible to even select more than one file to
  delete, let alone delete a batch.
- **The last remaining copy in a group can't be deleted** from here — once
  a group is down to one live file, its Delete option is disabled.
- Deletes go to the **Recycle Bin** (via `send2trash`), not permanent
  deletion, so a mistaken delete is recoverable.
- A confirmation dialog names the exact file before anything happens.
- A deleted file stays in the list, shown in red with strikethrough, until
  you rescan the folder — it isn't silently removed from view.

## Suggest Delete

**Suggest Delete** is a bulk-cleanup action driven by a folder priority you
set. It's a two-step action:

1. **First press** opens **Set Folder Priority**, listing every folder that
   holds a file in some duplicate group. Rank folders from most important
   (top) to least important (bottom) using whichever is fastest:
   - **Drag and drop** one folder, or a multi-selection of several
     (Ctrl/Shift-click, or a drag-selection box), to a new spot in one move.
   - **Move to Top / Move Up / Move Down / Move to Bottom** buttons act on
     the whole current selection as a block, so you can bulk-move several
     folders at once instead of one row at a time.
   - Keyboard shortcuts: **Alt+Up** / **Alt+Down** to nudge the selection,
     **Ctrl+Home** / **Ctrl+End** to send it to the top or bottom.
   - A **Find** box highlights and jumps to folders matching what you type,
     useful once the list gets long.
   - Each row is numbered with its current rank so the order is always
     visible at a glance.

   Check **Include subfolders** on a folder to also cover any subfolder
   under it — including ones a later scan discovers that were never
   explicitly ranked themselves. Press **Confirm** to lock in the ranking
   (or **Cancel** to back out with nothing highlighted).

   For each duplicate group, the file(s) sitting in the highest-ranked
   folder are kept; every file in a lower-ranked folder is suggested for
   removal. If two or more files tie for the top rank (typically because
   they're already in the very same folder), only one of them — the one
   with the longest name, or the one that sorts last in natural
   (alphabetic + numeric) order on a length tie — is added to the
   suggestion, so exactly one keeper survives per group. A group whose
   folders were never ranked is treated as one tied group, which reduces to
   picking a single file to remove from it.

   Suggested files are highlighted in **orange** — nothing is deleted yet.
   Review the highlighted files in the list.
2. **Second press** asks for confirmation (listing the files and total
   size) and, only if you confirm, moves exactly those files to the
   Recycle Bin. Declining the confirmation clears the highlight and
   suggests nothing further until you press the button again.

Your folder ranking is cached (by folder path, in the same SQLite cache) so
it's remembered automatically the next time those folders show up in a
scan — you can always re-rank them in the dialog before confirming again.

This is a separate action from the single-file **Delete File...** button
and follows the same safety model: deletes go to the Recycle Bin via
`send2trash`.
