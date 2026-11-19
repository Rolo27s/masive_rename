# masive_rename

This project hides player names in poker hand history files.

It is made for PT4 exports, so you can share/review hands without exposing real nicknames.

## Demo data included in this repo

This repository keeps only a tiny example in:

- `data\bronze\sample`
- `data\gold\sample`

It is only to make the functionality intuitive for third persons.

Before real use, remove/ignore demo files and copy your own PT4 exports into `data\bronze`.

## Quick start (copy/paste)

```bash
python -m venv .venv
.\.venv\Scripts\activate
python -m pip install .
python data\anonymize_bronze_to_gold.py
```

## Simple idea

- **Bronze** = original `.txt` files (your PT4 export)
- **Gold** = same files, but names are changed to anonymous names
- **Silver** = not used

Very important: if the same player appears again in another file, they keep the same anonymous name.  
Example: `frLameche001` will always become the same alias (like `randomname000012`).

## What you do (step by step)

### 1) Export hands from PT4

In PokerTracker 4, export your hand histories as `.txt` files (or folders with `.txt` files).

### 2) Copy those exported folders manually into Bronze

Put your exported `.txt` files inside:

`data\bronze`

You can keep subfolders (room/site/date). The script reads all `.txt` files recursively.

### 3) Run the script

From the project root:

```bash
python data\anonymize_bronze_to_gold.py
```

### 4) Get the anonymized files in Gold

Your processed files will be created in:

`data\gold`

Same folder structure, same hand histories, but with anonymized player names.

## First-time setup (only once per machine)

Create virtual environment:

```bash
python -m venv .venv
```

Activate it (Windows PowerShell):

```powershell
.\.venv\Scripts\activate
```

Install dependencies:

```bash
python -m pip install .
```

## Important note

The file `data\gold\anonymization.sqlite` is your identity map.

If you delete it, aliases are generated again from zero, so old and new aliases may not match.

## If you want to start over from zero

This deletes the name mapping database and rebuilds everything:

```powershell
$db='data\gold\anonymization.sqlite'
Remove-Item -LiteralPath $db -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath "$db-wal" -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath "$db-shm" -Force -ErrorAction SilentlyContinue
python data\anonymize_bronze_to_gold.py
```

## Troubleshooting

- **"Processed: 0" or no new files in gold**
  - Check that your PT4 `.txt` files are inside `data\bronze`.
  - If files changed but were skipped, run with:
    - `python data\anonymize_bronze_to_gold.py --force`

- **"python is not recognized"**
  - Install Python 3.9+ and restart terminal.

- **Virtual environment does not activate**
  - Use PowerShell command:
    - `.\.venv\Scripts\activate`
