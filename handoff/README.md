# Push the three histories to Origin and GitHub

This folder is a **one-time transfer**. It is not part of the workflow engine. Delete it after the three remotes have `main`.

The cloud agent can push only to *this* Cursor session’s git remote. It cannot push to:

- `https://github.com/Ashishkosana/...` (`gh` is not logged in on the agent VM)
- `ashishkosanagmailcom/platform-forge` (and the two sibling Origin repos) — this session’s Origin token is not scoped for those names

Your WSL user **is** logged into GitHub as `Ashishkosana`. Origin remotes for all three names already exist. Run the script below **on WSL** (`ashish_kosana@DESKTOP-VTMH2S5`).

## What you already created

| Remote | Status from your paste |
| --- | --- |
| GitHub `Ashishkosana/ai-reliability-control-plane` | Created, empty |
| GitHub `Ashishkosana/realtime-event-platform` | Created, empty |
| GitHub `Ashishkosana/platform-forge` | **Not** in the success output — the script creates it if missing |
| Origin `…/platform-forge`, `…/ai-reliability-control-plane`, `…/realtime-event-platform` | Already exist |

## WSL

Copy this `handoff/` directory onto the machine (download the three `.bundle` files plus `push_to_remotes.sh` into one folder), then:

```bash
cd /mnt/c/Users/Ashish\ Kosana/Downloads/handoff   # or wherever you put the files
chmod +x push_to_remotes.sh
./push_to_remotes.sh
```

The script:

1. Creates `Ashishkosana/platform-forge` on GitHub if it is missing (private, wiki/issues off).
2. Clones each `.bundle` (full existing history — not a squash).
3. Pushes `main` to GitHub (`github` remote) and Origin (`origin` remote).

Do **not** pass `--force` unless a remote already has a README commit you intend to replace.

## Manual equivalent

```bash
git clone ./ai-reliability-control-plane.bundle ~/repos/ai-reliability-control-plane
cd ~/repos/ai-reliability-control-plane
git remote remove origin
git remote add origin https://origin.cursor.com/ashishkosanagmailcom/ai-reliability-control-plane.git
git remote add github https://github.com/Ashishkosana/ai-reliability-control-plane.git
git push -u github main
git push -u origin main
```

Repeat with `realtime-event-platform` and `platform-forge`.

## After a successful push

```bash
git -C ~/repos/platform-forge remote -v
git -C ~/repos/ai-reliability-control-plane log -1 --oneline   # 3cf05a0
git -C ~/repos/realtime-event-platform log -1 --oneline        # 65f3b22
```

Then delete `handoff/` from this workflow-engine repo so the bundles are not part of the product tree.
