# idlescan Deferred Work

## WP-14: Frontend ES Module Split + UI Surfaces

**Status:** Deferred to v1.3.0

**Reason:** Splitting `web/app.js` (2,783 lines) into native ES modules under `web/js/`
is a large mechanical refactoring that requires careful dependency analysis,
import/export wiring, and thorough browser testing. The work package also
includes new UI surfaces (gas-quality badges, preflight dialog, auth prompt,
provenance panels, health/RPC panel, notification settings, custom-token
management, cancel button, phase timings, first-run checklist, light/dark
theme, accessibility pass) and Node-based JS unit tests.

**What can be done without WP-14:** All backend functionality (WP-01 through
WP-13) is complete and tested. The existing single-file `web/app.js` continues
to work unchanged under the strict CSP.

**Plan for v1.3.0:** Split `app.js` into `web/js/router.js`, `web/js/api.js`,
`web/js/store.js`, `web/js/views/*.js`, `web/js/components/*.js`, loaded via
`<script type="module">`. Add Node `--test` unit tests for formatting, router
parsing, and sorting. Run accessibility pass.

**Estimated effort:** Large (1-2 weeks of focused frontend work).
