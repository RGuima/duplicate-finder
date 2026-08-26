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
