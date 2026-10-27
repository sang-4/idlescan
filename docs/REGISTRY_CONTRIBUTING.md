# idlescan Registry Contribution Guide

The idlescan airdrop registry (`idlescan/data/airdrops.json`) lists the airdrops
the scanner checks. Entries describe a distributor contract, an eligibility
source (merkle dataset, dataset URL, or allocation view), and optional claim
metadata. idlescan never ships fabricated entries: every entry should be
verifiable against on-chain data or a cited primary source.

## Entry format

Each entry is an object under the top-level `airdrops` array:

```json
{
  "airdrops": [
    {
      "id": "uniswap-uni",
      "name": "Uniswap UNI",
      "chain": "ethereum",
      "distributor": "0x090d4613473dee047c3f2706764f49e0821d256e",
      "token": "0x1f9840a85d5a5bf72d6466b9cc3b5a2e3e8e1d9e",
      "token_decimals": 18,
      "claim_method": "claim(uint256,address,uint256,bytes32[])",
      "claimed_method": "isClaimed(uint256)",
      "root_method": "merkleRoot()",
      "claim_deadline": null,
      "notes": "Historical UNI airdrop; claim window may still be open.",
      "enabled": true,
      "links": { "docs": "https://docs.uniswap.org/" },
      "official_source_url": "https://uniswap.org/blog/uni/",
      "announcement_url": null,
      "contract_source_url": null,
      "verified_on": null,
      "claim_payable": false,
      "allow_payable": false,
      "eligibility": {
        "type": "merkle_dataset",
        "dataset": "data/datasets/uniswap-uni.claims.json",
        "leaf_encoding": "abi",
        "double_hash": false,
        "sort_pairs": true
      }
    }
  ]
}
```

### Required fields

| Field | Description |
|-------|-------------|
| `id` | Stable slug: lowercase, digits, `.` `_` `-`, must start with a letter/digit |
| `chain` | Chain key (must exist in `idlescan/data/chains.json`) |
| `distributor` | 0x-prefixed EVM address of the claim distributor contract |

### Optional fields

| Field | Default | Description |
|-------|---------|-------------|
| `name` | `id` | Human-readable name |
| `token` | `null` | 0x-prefixed token address (for price lookups) |
| `token_decimals` | `18` | Token decimals |
| `claim_method` | `claim(uint256,address,uint256,bytes32[])` | ABI signature of the claim function |
| `claimed_method` | `isClaimed(uint256)` or `claimed(address)` | ABI signature of the claim-state check |
| `claim_deadline` | `null` | ISO-8601 deadline, or null if unknown |
| `root_method` | `merkleRoot()` | ABI signature of the root reader, or empty for none |
| `notes` | `null` | Human-readable notes |
| `enabled` | `true` | Whether the scanner checks this drop |
| `links` | `{}` | Arbitrary name → URL map |
| `official_source_url` | `null` | Primary source for the airdrop |
| `announcement_url` | `null` | Announcement/blog post URL |
| `contract_source_url` | `null` | Block explorer contract page |
| `verified_on` | `null` | Date the entry was last verified |
| `claim_payable` | `false` | Whether the claim function is payable |
| `allow_payable` | `false` | Whether payable claims are explicitly allowed |
| `verified_by` | `null` | Who verified the entry |

### Eligibility shapes

Three eligibility types are supported:

**merkle_dataset** — a local merkle claims file:

```json
{
  "eligibility": {
    "type": "merkle_dataset",
    "dataset": "data/datasets/<name>.claims.json",
    "leaf_encoding": "abi",
    "double_hash": false,
    "sort_pairs": true
  }
}
```

**dataset_url** — a remotely hosted claims file (fetched via the SSRF-safe client):

```json
{
  "eligibility": {
    "type": "dataset_url",
    "url": "https://example.org/distribution.json"
  }
}
```

**allocation_view** — a contract-side `allocation(address)` view:

```json
{
  "eligibility": {
    "type": "allocation_view",
    "method": "allocation(address)",
    "returns": "uint256",
    "decimals": 18
  }
}
```

## Validation checklist

Before submitting a registry entry:

- [ ] The distributor address has contract code on the target chain
- [ ] `merkleRoot()` (or the configured root method) returns a value
- [ ] The claim-state method (`isClaimed` / `claimed`) is callable
- [ ] If a token is listed, `decimals()` matches `token_decimals`
- [ ] If a dataset is listed, its merkle root matches the on-chain root
- [ ] At least one sample proof verifies against the on-chain root
- [ ] `id` is unique within the registry
- [ ] `chain` exists in `chains.json`
- [ ] Addresses are 0x-prefixed 40-hex checksummed addresses
- [ ] No real private keys, seed phrases, or signatures are included

## Contributing

1. Copy `idlescan/data/airdrops.json` to a working file.
2. Add your entry under `airdrops`.
3. Run `idlescan registry import <file>` to validate.
4. Run `idlescan verify --registry <file>` to check against the chains.
5. Submit a pull request with the updated `idlescan/data/airdrops.json`.

Do not invent distributor addresses, token addresses, deadlines, or dataset
contents. If you cannot verify an entry from a primary source, do not add it.

## Schema

The registry JSON Schema lives at `idlescan/data/registry.schema.json`. Use it
with any JSON Schema validator to catch structural problems before submission.
