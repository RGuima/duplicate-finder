# Duplicate Finder

A desktop app (PySide6) that scans a folder and all its subfolders for
duplicate and near-duplicate media files and documents.

## What it finds

- **Exact duplicates**: files with identical content, detected via size
  pre-filtering followed by hashing (blake2b), regardless of filename.
- **Similar files**: files that aren't byte-identical but share the
  attributes that matter for that file type — for photos/videos, the same
  "date taken" and camera make/model; for documents, the same
  author/creation date — even though the filename, and possibly the exact
  bytes, differ.

Groups can contain more than 2 files (e.g. 5 copies of the same photo).

## Performance / scale

Built to handle 100,000+ files:

- A SQLite cache (`%LOCALAPPDATA%\DuplicateFinder\cache.db`) stores one row
  per scanned file. Duplicate grouping is done with indexed SQL `GROUP BY`
  queries instead of building large structures in Python.
- Files are only hashed if they share a size with at least one other file
  (cheap `os.stat` pass first), and only fully hashed if a cheap partial
  hash (first+last 64KB) also matches.
- Hashing and metadata/EXIF extraction run in a process pool across all
  CPU cores.
- Rescanning the same folder skips files whose size and modified time
  haven't changed — their previous hash/metadata is reused.

## Setup

```
pip install -r requirements.txt
python app.py
```

Pass `--exact-only` to restrict the app to exact-duplicate matches only
(similar/near-duplicate groups are not shown or scanned into results) and
to enable the **Suggest Delete** bulk-cleanup button:

```
python app.py --exact-only
```

## Usage

1. Browse to (or type) the folder to scan.
2. Click **Start Scan**. Progress is shown per stage (walking, hashing,
   reading attributes).
3. Results appear as expandable groups — "Exact duplicates" or "Similar
   (image/video/doc)" — each listing every file in that group with its
   folder.
4. Double-click a file, or select it and click **Show Details**, to see
   its full path, size, timestamps, hash, and extracted attributes
   (EXIF date/camera, document author/created date, etc.).
5. Use the **Show** dropdown and **Search** box to filter the results list.
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

## Suggest Delete (`--exact-only` mode)

When launched with `--exact-only`, a **Suggest Delete** button appears.
It targets exact-duplicate groups whose files all live in the same folder,
and for each one picks a single file to remove: the one with the longest
name, or — if names are the same length — the one that sorts last in
natural (alphabetic + numeric) order. Groups spanning more than one folder,
or where files live in different folders, are left alone.

It's a two-step action:

1. **First press** computes the suggestions and highlights every file that
   would be deleted in orange, without touching disk. Review the
   highlighted files in the list.
2. **Second press** asks for confirmation (listing the files and total
   size) and, only if you confirm, moves exactly those files to the
   Recycle Bin. Declining the confirmation clears the highlight and
   suggests nothing further until you press the button again.

This is a separate action from the single-file **Delete File...** button
and follows the same safety model: deletes go to the Recycle Bin via
`send2trash`, and only one file per qualifying group is ever selected for
removal, however many copies that group contains.
