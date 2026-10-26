# Runbook: ow_gerichte dates, Regesten and entscheidsuche stubs (2026-10-26)

**What is wrong.** Report 2026-10-26 (Adrian König's AI):

* `Gericht OW AbR 2000/01 Nr. 4` is served "vom 8. Februar 1999", the day of a
  Gemeinderat resolution its Sachverhalt recounts. The ruling is of 18 July 2001: the
  entry's fourth line reads "Entscheid der Obergerichtskommisson vom 18. Juli 2001".
  Cause: the portal dates every entry imported in 2015 with 26 November 2015 (VVGE:
  1 July 2016), and `scripts/repair_decision_dates.py --all` on 2026-03-12 replaced that
  with the first "Beschluss/Entscheid vom <date>" of the text, because "Entscheid der
  Obergerichtskommission vom" does not match its "Entscheid vom".
* Its Regeste stops after 239 characters at "vom 18. Juli 2". The scraper stores the
  portal's abstract, which is cut at ~240 characters, as the Regeste; 2,180 of the 2,182
  directly scraped rows hold such a prefix of their own text.
* The docket also has an entscheidsuche stub (`ow_gerichte_AbR_2000_01_Nr._4`, in
  `es_ow_gerichte.jsonl`): a header and the same cut Regeste, dated 26 November 2015.
  On 2026-10-26 `cite("AbR 2000/01 Nr. 4")` resolved to the stub. 1,264 of the 1,288
  stubs have a direct twin.

**The fix** (branch `claude/ow-ruling-dates`).

* `scrapers/cantonal/ow_gerichte.py`: `ruling_date()` reads the entry's own line between
  Regeste and reasons ("Entscheid des Regierungsrates vom …", "Urteil der … vom …",
  "Präsidialverfügung vom …", "Entscheid vom 17.12.1968 …"); `full_regeste()` takes the
  Regeste block up to the line dating the entry. New rows use both (portal date ->
  `publication_date`).
* `scripts/restore_ow_rulings.py` repairs `ow_gerichte.jsonl` with `date_restore` /
  `regeste_restore` stamps, and with `--drop-es-twins` removes the stubs that have a
  direct twin, giving each twin the stub's id as `previous_decision_id` (the build
  records it in `decision_id_aliases`: old links resolve, and the per-court swap gate
  counts the stub as answered, so no `OCL_SWAP_GATE_EXEMPT` is needed). The dropped
  stubs go to `es_ow_gerichte.jsonl.dropped-<date>` beside the shard (not `*.jsonl`).
* `scripts/repair_decision_dates.py` refuses `ow_gerichte` / `es_ow_gerichte`.

**Measured 2026-10-26** (dry run on the HF mirror's ow_gerichte parquet, i.e. the
served rows; 2,182 direct + 1,288 stubs):

| | rows |
|---|---|
| date corrected | 938 (of them ~164 still on the import day) |
| date already agrees | 1,090 |
| no ruling line (left alone) | 138 |
| split date "25. November/22. Dezember 2003" (left alone) | 12 |
| year outside the yearbook, a misprint such as "1887" (left alone) | 4 |
| Regeste completed | 1,714 |
| rows changed | 1,765 |
| stubs dropped / kept (no direct twin) | 1,264 / 24 |

Spot checks: the old dates are the lower instance's or the facts' ("Mit Verfügung vom
6. März 1990 verbot der Kantonsgerichtspräsident", "Mit Entscheid vom 18. August 2017
wies der Kantonsgerichtspräsident"). A second run changes nothing. About 466 Regesten
stay cut where the text gives no line closing the block (the older VVGE run Regeste and
facts together). The 24 kept stubs are nine Anhänge/Generalregister and fifteen VVGE/OGVE
entries the direct scraper never found; a backfill of those is separate.

## Apply

Window: no writer on `ow_gerichte.jsonl` (01:00 scrape) and no build: after the full
build exits and before the next 01:00 scrape. The same window serves
`runbooks/closing_dates_2026-10-25.md` (not yet applied on 2026-10-26). Check
immediately before:

```bash
ssh -i ~/.ssh/caselaw root@46.225.212.40 'date -u; systemctl show -p ActiveState --value opencaselaw-publish.service opencaselaw-publish-incremental.service opencaselaw-scrape.service opencaselaw-late-scrapers.service; pgrep -af "build_fts5|run_scraper|publish.py" | grep -v pgrep || echo idle'
```

All `inactive` and `idle`. Then make sure the VPS has the script, back up, dry run:

```bash
ssh -i ~/.ssh/caselaw root@46.225.212.40 'set -e; cd /opt/caselaw/repo; test -f scripts/restore_ow_rulings.py; D=/mnt/HC_Volume_104655575/output/decisions; B=/mnt/HC_Volume_104655575/quarantine/ow-rulings-$(date -u +%Y%m%d); mkdir -p $B; cp -a $D/ow_gerichte.jsonl $D/es_ow_gerichte.jsonl $B/; ls -la $B; python3 scripts/restore_ow_rulings.py $D/ow_gerichte.jsonl --examples 0 2>/dev/null; python3 scripts/restore_ow_rulings.py $D/ow_gerichte.jsonl --drop-es-twins $D/es_ow_gerichte.jsonl 2>/dev/null'
```

Expect `rows_changed` about 1,765 (`date_corrected` about 938) and `es_dropped_twin`
about 1,264 (a few more on each if the scrape added rows since the mirror); stop if
either is off by more than a few dozen. Then, repair first, stubs second:

```bash
ssh -i ~/.ssh/caselaw root@46.225.212.40 'set -e; cd /opt/caselaw/repo; D=/mnt/HC_Volume_104655575/output/decisions; python3 scripts/restore_ow_rulings.py $D/ow_gerichte.jsonl --apply --examples 0 2>/dev/null; python3 scripts/restore_ow_rulings.py $D/ow_gerichte.jsonl --drop-es-twins $D/es_ow_gerichte.jsonl --apply 2>/dev/null; ls -la $D/ | grep ow_gerichte'
```

Rollback: copy the two quarantine backups back (and delete
`es_ow_gerichte.jsonl.dropped-*`); or per row, `date_restore.from`.

## Verify after the next full build

The build log shows the per-court gate passing through the aliases:
"pre-swap per-court gate: ow_gerichte … answered through an alias …".

```bash
python3 - <<'PY'
import json, urllib.request
def call(name, args):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                       "params": {"name": name, "arguments": args}}).encode()
    req = urllib.request.Request("https://mcp.opencaselaw.ch/mcp", data=body, headers={
        "Content-Type": "application/json", "Accept": "application/json, text/event-stream"})
    raw = urllib.request.urlopen(req, timeout=60).read().decode()
    raw = [z[5:] for z in raw.splitlines() if z.startswith("data:")][-1] if "data:" in raw else raw
    return json.loads(raw)["result"]["structuredContent"]
for ref in ("AbR 2000/01 Nr. 4", "ow_gerichte_AbR_2000_01_Nr._4", "ow_gerichte_AbR 1990_91 Nr. 21"):
    c = call("cite", {"reference": ref})
    print(ref, "|", c["citation_string_de"], "|", c.get("docket_ambiguous"), "|", c["rule_statement"][-40:])
PY
```

Expected: "Gericht OW AbR 2000/01 Nr. 4 vom 18. Juli 2001" for the first two (the stub id
resolves to the direct row), not docket-ambiguous, rule statement ending "vom 18. Juli
2001"; "Gericht OW AbR 1990/91 Nr. 21 vom 12. Juni 1990".
