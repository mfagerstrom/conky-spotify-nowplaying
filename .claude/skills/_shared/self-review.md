# Self review

Shared by any pull request a session opens, inside a skill or outside one.
Ported from PlaywrightTesting's `self-review.md`. Before a session hands a pull
request to the user, it reviews the pull request itself, fixes everything the
review finds, and reviews it again, round after round, until a pass finds
nothing. Only then does it send the ready signal from
[ready-signal.md](ready-signal.md). The user's review starts from a pull
request the session has already read as a reviewer, not as its author, and
found clean.

## When it starts

The self review starts once the pull request is open, the static checks in
[static-checks.md](static-checks.md) pass on the branch head, and the pull
request is mergeable, per [ready-signal.md](ready-signal.md). The review ends in the signal.

Move the session to the `Self Review` sidebar group as the review starts, per
[sidebar-groups.md](sidebar-groups.md).

## One pass

A pass always reads the whole pull request as it stands at the branch head,
not just the commits since the last pass. A fix can break something the fix
commit alone does not show.

1. Run the `code-review` skill on the pull request at `high`, with neither
   `--comment` nor `--fix`:

   ```
   Skill  skill: "code-review"  args: "high <pr-number>"
   ```

   Reviewing the pull request by number reads the diff as GitHub has it, which
   is the diff the user will read.
2. Read the diff once more against this repository's own rules, which a
   general review does not know:

   - `CLAUDE.md`, and the user's rule that software is installed with apt
     only: no `pip install`, snap, flatpak, AppImage or `curl | sh` in the
     README, a skill, or a script;
   - a new file the installed program needs at runtime that is missing from
     `debian/install`, or a new runtime dependency missing from
     `debian/control`;
   - a user-visible change the README does not describe, or a README step that
     no longer matches the code;
   - comments and log strings that describe history rather than how the code
     works now;
   - the pull request body: what changed, why, and a test plan that says what
     was actually run and what was not.
3. Check every finding before acting on it. Read the code the finding names;
   when running the program can settle it (the `run` skill, or the installed
   smoke test in `/release` step 4), the run settles it. A finding holds when
   it names a concrete way the change goes wrong (a case that breaks, a path
   that only exists under `/usr`, a step skipped), or a rule it breaks that
   can be quoted from `CLAUDE.md` or a skill, or a cost that can be pointed at
   (a duplicated helper, dead code). A finding that does not hold up,
   including a preference with no such cost behind it, is not a finding: note
   it, with the reason, as a false positive. `code-review` at `high` is tuned
   to surface too much rather than too little, so this check is what lets the
   loop end.

## The loop

1. Run a pass.
2. No finding holds: the loop is over. Go to [When it ends](#when-it-ends).
3. Otherwise fix every finding that holds, on the branch, rerun the static
   checks from [static-checks.md](static-checks.md),
   and push. Each finding is fixed, none is set aside for the user to pick up
   at review, and none is filed away for later. The session stays in
   `Self Review` while it fixes.
4. Go back to 1.

There is no cap on the number of passes. The loop ends on a clean pass and on
nothing else.

A false positive a later pass raises again, against code that has not changed
since it was checked, does not count against a clean pass. It is already
settled. Raised again against code that has changed, it is checked again from
scratch.

A finding that holds but whose fix is the user's call, such as a scope line
the issue does not draw or two behaviours the README does not choose between,
is put to the user with `AskUserQuestion` rather than guessed at. The session
sits in `Needs Review` while the question is open, per
[sidebar-groups.md](sidebar-groups.md), goes back to `Self Review` with the
answer, applies it, and carries on with the loop. The question is not the
ready signal: the pull request is still not handed over.

The findings never go up as review comments on the pull request. Comments are
the user's review, and a thread the session opens on its own pull request only
pads the list the user has to read.

## The `Self review` section

The pull request body carries a `Self review` heading, updated after each
pass. It says how many passes ran, and lists each finding by pass: what it
was, and the commit that fixed it, or the reason it was a false positive. The
last pass is listed as clean. A first pass that found nothing reads "one pass,
no findings" rather than dropping the heading.

Patch the body through the API from a scratchpad file, which sidesteps
`gh pr edit` failing on some `gh` releases and keeps the text out of shell
quoting:

```bash
gh api -X PATCH repos/{owner}/{repo}/pulls/<pr-number> -F body=@<scratchpad>/pr-body.md
```

## Commits after the handoff

Commits pushed after the ready signal, for the user's review comments or
anything else, send the pull request back through the loop, starting with a
full pass once the static checks pass, in `Self Review`. The signal goes out
again only on a clean pass.

## When it ends

The review ends on a pass that finds nothing, with the static checks passing
on the branch head and the pull request still reading as mergeable, per
[ready-signal.md](ready-signal.md#the-mergeability-check-comes-first). A
sibling branch can merge while the review runs, so read it again rather than
trusting the read from before. A conflict resolved at that point is a new
commit, so it goes back through the loop. Then the session sends the ready
signal and moves to `Needs Review`, per [ready-signal.md](ready-signal.md).

The report above the signal says in a line or two how many passes ran and what
they found and fixed.
