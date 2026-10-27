# Support

**Email:** support@allank.dev

Support is handled by Allan Kiprop, the seller. Expect a first reply within
**48 hours**, usually sooner on weekdays. Support covers installation, the
documented commands, the local API, and defects in the product. It does not cover
trading decisions, airdrop eligibility disputes with third-party projects, or
wallet recovery.

## What to include

Send these three things:

1. The output of

   ```bash
   idlescan about --json
   ```

   It contains versions, platform, schema state, mode flags and the database
   filename. It contains no secrets, no wallet addresses and no RPC URLs.
2. The exact command you ran, copied from your terminal (including flags).
3. The exact error text, and what you expected instead. If the UI is involved,
   say which page or `#/route` and what the browser console shows.

Screenshots are welcome. Blur addresses if you prefer.

## What never to send

- Private keys, seed phrases, keystore files, or hardware-wallet recovery words.
- RPC URLs that contain an API key or token. Replace the key with `[redacted]`.
- `.env` files. Paste only the specific lines that matter, with values removed.
- Wallet addresses you do not want associated with your support request.

The seller will never ask for any of these. A message that asks you for a seed
phrase is not from the seller.

## Refunds

The refund window is **14 days from purchase**, handled through Gumroad under its
refund process. Email support@allank.dev with your Gumroad order reference and a
one-line reason. You do not need to justify a refund beyond that.

## Security reports

If you believe you found a security defect, email support@allank.dev with
`SECURITY` in the subject. Do not open a public post with reproduction details.
The security model and threat boundaries are described in `TRUST.md` and
`SECURITY.md`.

## Version support

This is the 1.2.0 release. Updates within the 1.x line are provided at the
seller's discretion; there is no fixed release schedule. Buyers of the
Founding Release receive 1.x updates when they are published. See
`docs/ROADMAP.md` for themes, which are plans and not promises.
