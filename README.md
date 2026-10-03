# stopcoder

Label why your coding agent stopped, from your own Claude Code, Codex and agentknit history.

Every time an agent ends its turn and you reply, your reply carries something the agent lacked:
nothing at all ("yes, go on"), a choice between options it offered, a goal it could not know,
a correction. `stopcoder` samples 50 of those moments from your transcripts and asks you, one
key per moment, which it was. It takes about 10 minutes.

It is part of a study on long-horizon autonomy for coding agents: which stops a supervisor
agent could answer for you, and which ones only you can.

## Run

```bash
uvx --from git+https://github.com/monperrus/stopcoder stopcoder
```

or `pipx run --spec git+https://github.com/monperrus/stopcoder stopcoder`. Python 3.10+, no dependencies.

Quit any time with `q`; running it again resumes where you stopped.

## Keys

- `1`–`7`: label the stop (`?` shows the codebook), `0`: unclear
- `↑` `↓` `PgUp` `PgDn`: scroll the agent's message
- `←` / `→`: previous / next stop
- `n`: attach a note
- `q`: save and quit

## The codebook

- **NUDGE**: no new information: yes, continue, proceed, do next.
- **POLL**: asked for status or progress.
- **PICK**: chose among options the agent offered, or endorsed its recommendation.
- **GOAL**: gave goals, thresholds, priorities, scope or taste the agent could not know.
- **CORRECT**: corrected a misunderstanding, a mistake or a wrong direction.
- **EXTERNAL**: gave facts only you had, or approved money, push, publish or contacting people.
- **NEWTASK**: started an unrelated task or question.

## Privacy

- Transcripts are read locally from `~/.claude/projects`, `~/.codex/sessions` and
  `~/.local/share/agent_probe`.
- Progress is kept in `~/.local/state/stopcoder/annotation.json` (mode 600). It holds the text
  you see, and it never leaves your machine by itself.
- At the end you choose what to send:
  - **labels and numbers only** (default): label, harness, model, month, turn index, message
    lengths, timings, salted hashes in place of session ids. No text, paths or project names.
  - **with text**: also the three messages you saw and your notes. Only if you are fine sharing them.
- `stopcoder export` prints exactly what would be sent.
- It is sent over HTTPS to `https://api.monperrus.com/stopcoder`, which runs
  [`collector/stopcoder.py`](collector/stopcoder.py): it stores the JSON as one file and
  nothing else (no IP address). If sending fails, the export stays in
  `~/.local/state/stopcoder/` to send by hand.

## Other commands

```bash
stopcoder status [--json]          # progress and label counts
stopcoder export [-o FILE] [--include-text]
stopcoder send [--include-text]    # send again, or later
stopcoder --restart                # draw a new sample
stopcoder reset                    # delete progress
stopcoder -n 100                   # sample size
```

## License

MIT
