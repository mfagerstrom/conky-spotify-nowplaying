---
name: create-issue
description: >-
  File a GitHub issue in this repo under the title convention
  `[<area>] <description>`, with the standard body sections, the matching area
  label, and a kind label. Use when asked to file, open, create, or write up an
  issue - "/create-issue", "file an issue for this", "open an issue about the
  lyrics skipping" - and when another skill needs an issue filed. Also retitles
  existing issues that predate the convention.
---

# Create Issue

Every issue in this repo carries the same title shape, so the tracker reads by
area at a glance and `/implement` finds the same sections in every body.

```
[<area>] <description>
```

One bracketed area in lower case, one space, then the description. No trailing
period. Whole title 100 characters or fewer.

Ported from PlaywrightTesting's `create-issue`, less its spec-name slot and
suite acronyms: this repository has no test suites, and its areas are few
enough that the area alone places an issue.

Good:

```
[lyrics] synced lines drift behind the track after a seek
[likes] heart stays grey after the rate limit lifts
[controls] dragging the widget onto a second monitor snaps it back
[layout] background falls back to grey on mostly grayscale covers
[tray] menu keeps the previous track after Spotify quits
[packaging] autostart entry is left behind after the package is removed
[tooling] share the static checks between skills
```

Bad, with the reason:

```
fix: lyrics are broken                 -> no area, verb prefix, not specific
[lyrics][layout] lyric font too small  -> two areas in one issue
[Lyrics] Synced lines drift.           -> capitalised area, trailing period
[lyrics] - drift                       -> dash instead of a description
```

## 1. Area

The area is where the user meets the problem, not the file the fix lands in:

- `lyrics`: fetching lyrics from LRCLIB, matching them to the track, syncing
  and scrolling them.
- `likes`: the heart, Spotify login, the Liked Songs index and the rate-limit
  backoff (`src/spotify_api.py`).
- `controls`: clicks, the seek bar, the playback buttons, dragging and the
  saved position (`src/conky-mouse.py`).
- `layout`: what the widget draws and where: text, fonts, cover art,
  background colour, scaling (`src/conky.conf`, `src/draw.lua`, the render in
  `src/nowplaying.py`).
- `tray`: the top-bar icon and its menu (`src/tray.py`).
- `packaging`: `debian/`, `packaging/`, `build-deb.sh`, `release-ppa.sh`, the
  PPA, and the launcher's install, start, stop and autostart (`bin/`).
- `tooling`: the skills under `.claude/`, `scripts/`, `CLAUDE.md`, and tests;
  anything about working on the repo rather than the widget.

A missed click on the heart is `likes`, not `controls`: the area follows the
feature. Drawing the heart in the wrong place is `layout`.

One area per issue. Work that is really two things, say a lyric fetch bug and a
lyric font change, is two issues, each saying which sibling it was split from.
Each area also exists as a repository label of the same name, and the issue
carries both the prefix and the label: the prefix is what a person reads, the
label is what `gh issue list --label lyrics` filters on. See section 5.

## 2. Description

State the observed behaviour or the requested change. No conventional-commit
verb prefix: `fix`, `feat` and `chore` belong on the branch name and the
commit, and the kind label says the rest.

Lower case after the area except for names (Spotify, LRCLIB, Conky), no
trailing period, present tense, specific enough to recognise without opening
the issue. `lyrics are broken` is not a
description; `synced lines drift behind the track after a seek` is.

## 3. Body

Required sections, in this order. A section with nothing to put in it says so
in one line rather than being dropped; `/implement` reads them in this shape.
The body is not hand-wrapped.

- **What happens** - what actually happened, or for an enhancement, how it
  behaves today. Report only what was observed. Never invent error text, line
  numbers or a root cause; an issue filed from a report rather than a run says
  so.
- **What should happen** - the behaviour wanted, and where it comes from when
  it is written down (the README, an earlier issue, Spotify's own app).
- **How to reproduce** - what the widget was showing when it happened:
  - the track, as title and artist, with its link when known
    (`playerctl -p spotify metadata xesam:url`);
  - lyrics availability: synced, plain only, or none on LRCLIB;
  - the monitor setup: how many, their scaling, and which one the widget sits
    on;
  - anything else in play: liked or not, logged in or not, rate-limited,
    installed package or a checkout.

  For an issue this does not fit (most `tooling` and `packaging` issues),
  give the command that shows it, or write that it does not apply and why.
- **Logs** - relevant lines from `~/.cache/conky-spotify-nowplaying/`:
  `nowplaying.log` (lyrics, likes, rendering), `mouse.log` (clicks, drag,
  position) and `run.log` (launcher, Conky, Lua errors). Quote them in a code
  block, trimmed to the lines around the problem, and only lines actually read
  from those files. None read says so.
- **Scope** - the files a fix is expected to touch, and anything deliberately
  left out.
- **Acceptance criteria** - what done means, as checks `/implement` can follow
  literally. A change to the widget ends with it running from the checkout and
  showing the fixed behaviour, per `/implement` step 5; a visual one with a
  capture from `scripts/capture-widget.py`.

## 4. Kind

Every issue carries one kind label on top of its area label: `bug`,
`enhancement` or `documentation`, all of which exist here. A README-only change
is `documentation`; a skill or script change is `enhancement` unless something
in it is broken.

Never apply `In Progress` here. `/implement` applies it when work starts, and
applying it at filing time makes every later session stand down from an issue
nobody works.

## 5. Labels

The area labels are made on first use, like `In Progress` in `/implement`, and
never deleted. Create the one this issue needs before filing, with these
colours and descriptions so every session makes the same label:

```bash
gh label create lyrics --color 1D76DB \
  --description "Area: fetching, syncing and scrolling lyrics" 2>/dev/null || true
gh label create likes --color D93F0B \
  --description "Area: the heart, login and the Liked Songs index" 2>/dev/null || true
gh label create controls --color 0E8A16 \
  --description "Area: clicks, seeking, playback buttons and dragging" 2>/dev/null || true
gh label create layout --color 5319E7 \
  --description "Area: what the widget draws and where" 2>/dev/null || true
gh label create tray --color C5DEF5 \
  --description "Area: the top-bar icon and its menu" 2>/dev/null || true
gh label create packaging --color BFD4F2 \
  --description "Area: Debian package, PPA and launcher" 2>/dev/null || true
gh label create tooling --color EDEDED \
  --description "Area: skills, scripts and tests" 2>/dev/null || true
```

Any other label that does not already exist is never created here.

## 6. Before creating

1. Look for an open issue that already covers it:

   ```bash
   gh issue list --state open --label "<area>" --json number,title,url
   gh issue list --state open --search "<key words> in:title,body" --json number,title,url
   ```

   The second read catches issues filed before the area labels existed. A hit
   means this finding goes onto that issue, and its URL is what gets
   reported; returning an existing URL is a successful run. Add it as a
   comment when it is new evidence (another track, another log line), or edit
   the body when it widens the work, so the work list stays in one place for
   `/implement`:

   ```bash
   gh issue view <n> --json body --jq .body > <scratchpad>/issue-body.md
   test -s <scratchpad>/issue-body.md || { echo "empty fetch, not editing"; exit 1; }
   # append the item, then:
   test -s <scratchpad>/issue-body.md && \
     gh issue edit <n> --body-file <scratchpad>/issue-body.md
   gh issue view <n> --json body --jq .body | head -5
   ```

   Run the fetch from inside the repository: `gh` finds the repo from the
   working directory, and a fetch from anywhere else writes an empty file that
   `--body-file` then writes over the issue, reporting success. The read-back
   after the edit is what shows the old body survived the append.

2. Confirm the title with the user when the description was inferred rather
   than given. A title the user supplied is used as it is once it fits the
   convention. A session filing on the side of `/implement`, which works on
   its own, files without asking and names the issue in its report.

Then write the body to `<scratchpad>/issue-body.md` with the Write tool and
file it, the area and kind as two `--label` flags:

```bash
gh issue create --title "[lyrics] synced lines drift behind the track after a seek" \
  --label lyrics --label bug --body-file <scratchpad>/issue-body.md
gh issue view <n> --json labels --jq '[.labels[].name] | join(", ")'
```

Both labels are read back off the issue before its URL is reported. Then stop:
no branch, no `In Progress`, no code. `/implement` owns everything after the
URL exists.

## 7. Retitling existing issues

Issues filed before this convention are retitled in place, never closed and
refiled, since their numbers are referenced from branches, pull requests and
other issues. Retitle only when asked to clean up the tracker: `/implement`
works an issue under whatever title it has.

1. List candidates:
   `gh issue list --state open --limit 100 --json number,title,labels`.
2. A title already matching
   `^\[(lyrics|likes|controls|layout|tray|packaging|tooling)\] \S` is left
   alone, but still gets its area label if it is missing one. Any other
   bracketed prefix is an old title like the rest.
3. Take the area from the body and from the files it names. When neither says,
   ask with `AskUserQuestion` rather than guess: a wrong area hides the issue
   from the view it belongs in.
4. Keep the description's meaning: drop the verb prefix and keep the words that
   carry information.
5. Show the user the whole old-to-new list before applying it. Then create
   each area label the list uses, per section 5, and apply one issue at a
   time, title and label together, reading the labels back:

   ```bash
   gh issue edit <n> --title "<new title>" --add-label "<area>"
   gh issue view <n> --json title,labels --jq '.title, [.labels[].name]'
   ```

   ```
   Background colour: prefer a small accent colour over gray/black on mostly-grayscale covers
     -> [layout] background prefers a small accent colour over grey on mostly grayscale covers
   Add unit tests
     -> [tooling] add unit tests
   ```

6. Report how many were retitled, how many labelled, and which were skipped.

A retitle changes the title and the area label, never the body.
