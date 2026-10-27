# Banned words

Marketing for idlescan Desk stays factual. The following words and phrases are
rejected by `scripts/check_copy.py` in `release/listing/` and in the buyer-facing
documents.

| avoid | why |
| --- | --- |
| guaranteed, guaranteed returns | The tool cannot promise eligibility, a claim, a price, or an outcome. |
| free money, easy money, passive income, get rich | This is a research tool, not an income scheme. |
| profit, profits, profitable | Avoid outcome claims entirely; describe net value or cost instead. |
| risk-free, cannot lose | Claiming is an on-chain transaction with real costs and risks. |
| bypass | Implies circumventing a project's rules or controls. |
| sybil, farm, farming | Describes conduct idlescan does not support or advise. |
| moon, no-brainer, secret sauce | Hype. |

## Exemptions

- `LICENSE` and `NOTICE` are legal text and keep standard liability wording such
  as "lost profits".
- `THIRD_PARTY.md` may name third-party projects.
- The single PowerShell line containing `ExecutionPolicy Bypass` is a command
  parameter, not a claim. The checker allows lines containing
  `Set-ExecutionPolicy`.

## Run it

```bash
python scripts/check_copy.py
```

The script exits 0 only when every scanned file is clean.
