# Nexus events collector

Public GitHub repository for the nightly Nexus Churches events crawl. GitHub Actions minutes on a public repo are free, which is how every listed church website (currently 18,804 evangelical FR/US sites with a website) and every organisation site can be visited each night.

The Nexus app itself stays in the **private** repo [`noealargent/studiopainperdu-app`](https://github.com/noealargent/studiopainperdu-app). This collector holds only:

- the importer, coverage and merge scripts
- a slim list of public church websites (id, name, website, city, region, country, map coordinates — **no phone numbers or street addresses**)
- organisation sites
- the opt-out list
- the workflow

No secrets, API keys, `.env` files, or Studio Pain Perdu application code belong here.

## What the crawler reads

Structured data only: schema.org Event JSON-LD, iCalendar feeds the site itself links, and the WordPress The Events Calendar REST feed. It uses an identifiable User-Agent (`NexusEventsBot`, see [the robot page](https://nexuschurches.com/annuaire/evenements/robot)), honours **robots.txt**, text-and-data-mining reservations, and `data/optout.json`.

## Nightly workflow

The workflow definition is in [`scripts/ci/nightly-events.yml`](scripts/ci/nightly-events.yml). GitHub only runs it after it is copied to `.github/workflows/nightly-events.yml` (the token used to publish this repo cannot create workflow files). In the GitHub UI: Add file → Create new file → path `.github/workflows/nightly-events.yml` → paste the contents of `scripts/ci/nightly-events.yml` → commit to `main`. After that, Actions → Nightly events collection → Run workflow.

It runs at **03:00 and 11:00 UTC** (and on manual dispatch).

1. **16 parallel shards**, each under a 6-hour cap, split the church list (`index % 16`). There is no per-run site cap and no 14-day “no signal” skip: every listed site is queued each night. Shard 0 also visits every organisation site.
2. A **publish** job merges shard outputs, archives events whose last day is before today (America/Denver), opens (and, when enabled, merges) a data PR on the private live branch `cursor/nexus-firebase-static-demo-23e0`, then deploys Hosting to `nexus-app-9a9d0`.

Until that publish job has succeeded once, the existing workflow on `main` in the private repo (`.github/workflows/nexus-nightly-events.yml`) should keep running.

## Secrets the repo owner must add

Add these under
[Settings → Secrets and variables → Actions](https://github.com/noealargent/nexus-events-collector/settings/secrets/actions)
on **this** repository. Do not commit them, and do not paste their values into chat or issues.

| Name | Where it goes | What it is |
| --- | --- | --- |
| `NEXUS_APP_REPO_TOKEN` | Actions **secret** | A fine-grained personal access token (noealargent account) limited to the private repo `noealargent/studiopainperdu-app`, with **Contents: Read and write** and **Pull requests: Read and write**. Used to push the data branch, open the PR, and merge into the live branch. |
| `FIREBASE_SERVICE_ACCOUNT_NEXUS_APP_9A9D0` | Actions **secret** | Copy the existing secret of the same name from `noealargent/studiopainperdu-app`. Required only for the Hosting deploy. |

Optional repository **variable** (Settings → Secrets and variables → Actions → Variables):

| Name | Value | Effect |
| --- | --- | --- |
| `NEXUS_EVENTS_AUTO_PUBLISH` | `true` | After the data PR is opened, merge it into the live branch and deploy. Leave this unset until the first collection run has succeeded if you want to review the first PR. Same flag the private-repo workflow already uses. |

### Creating `NEXUS_APP_REPO_TOKEN`

1. GitHub → Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token.
2. Resource owner: `noealargent`. Repository access: **Only select repositories** → `studiopainperdu-app` only (do not grant this token to other repos you want to keep tightly scoped).
3. Permissions: Contents (Read and write), Pull requests (Read and write).
4. Generate, then paste the token into this collector repo’s `NEXUS_APP_REPO_TOKEN` secret.

A classic `repo`-scoped PAT also works; a fine-grained token limited to the one private app repo is preferable.

## Local run

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python3 scripts/events/import_events.py --countries FR --limit 5 --out /tmp/fr.json --log /tmp/fr-log.json
```

Tests: `python3 -m unittest scripts.events.collect_test scripts.events.coverage_test`.
