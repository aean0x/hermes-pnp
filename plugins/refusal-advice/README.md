# refusal-advice

Appends the gate-clean alternative to an unattended approvals refusal.

## Why

Every refusal the approvals gate renders in an unattended context (cron, `-q`, webhook)
comes from one template, `_Unattended.block_message` in hermes-agent `tools/approval.py`,
and its advice is one sentence: *"Find an alternative approach that avoids this command."*
The blocked command is dropped, nothing names the shape that would have run, and the job's
next turn emits the same banned shape. Measured on this fleet: 21.0 cron refusals/day,
unchanged by three rounds of carriers on skills and prompts — partly because three
offenders preload no skill at all, and partly because a rule on a loaded carrier is still
only a prohibition.

The refusal itself is the one surface guaranteed to be in the model's context at the moment
of the mistake. This plugin makes it name the alternative.

## What it does

`transform_tool_result` (documented as "plugins may replace the final result string"):

| Detector description | Appended advice |
|---|---|
| `script execution via heredoc`, `script execution via -e/-c flag`, `shell command via -c/-lc flag` | write the logic to a script FILE and run `python3 <path>` (skill `cron-output-guide`, section Headless tooling) |
| `stop/restart system service` and other raw-string matches | the detector matches quoted text too; reword the payload (name the unit, say it needs a restart) |
| anything else the detector flags | the script-file shape, or a plain tool (`read_file`, `search_files`, `jq`) for inspection |

A result without the dangerous-command prefix is returned untouched, and a refusal that
already carries the advice is not rewritten twice.

Message-only: it does not change what the gate blocks, does not rewrite a command, and
runs no tool. No IO, no subprocess, no locks — required, because
`plugins.hook_callback_timeout: 0` runs this hook on the caller thread.

## Tests

```
PYTHONPATH=. python3 -m unittest discover -s tests -v
```
