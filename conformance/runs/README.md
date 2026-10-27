# Conformance runs

Each file here is the log of one run of `conformance/check.py` against a
released Claude Code, named for its version and operating system, such as
`2.1.310-linux.txt`. A log starts with the output of `claude --version`, the
platform, the date, the model the throwaway sessions used and whether the run
was full or quick. Then comes one line per rule: PASS, FAIL or SKIP, the rule,
and what was seen.

To add a log, run the full check with `--log` from the repository root:

```sh
python3 conformance/check.py --log
```

The run takes about seven minutes and spends a few cents of model use. It
writes `conformance/runs/<version>-<os>.txt`, replacing a log of the same
version, so `git diff conformance/runs/` shows what changed since the last
run. Use `--log FILE` to write somewhere else.
A second run of a version already logged, made with other options, keeps the
first log and adds its date to the name, such as `2.1.310-linux-2026-10-27.txt`.

To compare two versions, leave out the details after `--`. They hold process
ids and message ids that differ on every run:

```sh
diff <(sed 's/  -- .*//' conformance/runs/OLD.txt) <(sed 's/  -- .*//' conformance/runs/NEW.txt)
```

Below the header lines, a line that differs is a rule whose result changed
between the two versions.
Socket paths in the details show the home directory as `~`.
