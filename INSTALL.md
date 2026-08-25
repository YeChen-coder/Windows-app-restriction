# Installation

## Requirements

- Windows 10 or Windows 11
- Python 3.11 or newer
- Tkinter, normally included with the standard Windows Python installer
- `psutil`

Install `psutil` if needed:

```powershell
py -3 -m pip install psutil
```

## Run

From this folder:

```powershell
.\run_app_limiter.bat
```

If some target applications cannot be closed because of permissions, run:

```powershell
.\run_app_limiter_as_admin.bat
```

## Configure Rules

The app creates local runtime files in this folder:

- `rules.json`: monitored application rules
- `state.json`: current sessions and cooldown state
- `schedule.json`: optional daily schedule settings
- `error.log`: crash/error diagnostics

These files are intentionally ignored by Git because they are machine-specific.

To start from the example rules:

```powershell
Copy-Item .\rules.example.json .\rules.json
```

You can also create and edit rules through the GUI:

- `Add from running`: choose a running process.
- `Add EXE`: choose an executable file.
- `One-shot timer`: create a temporary one-time limit.
- `Pick window`: select a visible app window when the process name is unclear.

## Daily Schedule

The `Daily schedule` section controls only daily rules.

- Leave `Enable schedule` unchecked for manual control.
- Use `Start` and `End` in `HH:MM` 24-hour format.
- A normal window such as `08:00 -> 22:00` runs during the same day.
- A cross-day window such as `16:00 -> 08:00` runs from afternoon through the next morning.

One-time rules remain active even when daily rules are paused by schedule.
