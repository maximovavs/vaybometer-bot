# PROJECT_STATE.md

Repository: `maximovavs/vaybometer-bot`

Purpose: production automation for Cyprus VayboMeter regional posts and supporting weather/environmental collectors.

## Operating contract
Permission, safety, fresh-guard, secret-handling, and context-loading rules are defined in `AGENTS.md`. This file grants no write or production authorization.

## Last verified baseline
Agent-ready cleanup was prepared from live `main` at:
`37cf53da93b4386144c0f9b7c081521b1dff2441`

This SHA is provenance only. Scheduled collector commits can move `main`; always fetch live state before acting.

## Current production state at verification
- Scheduled Cyprus publishing is active.
- Recent Daily VayboMeter, Schumann, radiation, and SafeCast runs were completing successfully.
- No open PR or open issue was present at the audit.
- No single engineering blocker was encoded in GitHub at the time of this snapshot.
- Tracked `.github/workflows/.env` was removed in this cleanup because current workflows did not load it.
- Current water-activity thresholds used by production have inline defaults in `post_common.py`; Cyprus shore/spot profiles are also present there.
- Daily WorldVibeMeter automation is retired; the retained World weekly/manual behavior is documented separately.

## Durable source pointers
- Editorial voice: `editorial_voice.py`
- Cyprus visual macro policy: `cyprus_visual_policy.py`
- Cross-region visual specification: `docs/visual_weather_matrix.md`
- World retirement decision: `docs/WORLDVIBEMETER_RETIREMENT.md`
- Main Cyprus publisher workflow: `.github/workflows/daily_post.yml`
- Safe test workflow: `.github/workflows/safe_test_post.yml`
- Shared content / water-activity logic: `post_common.py`

## Current-state rule
Do not turn this file into a chronological log. Replace stale current-state facts when they materially matter; keep detailed history in GitHub commits, PRs, workflow runs, and existing docs.

## Next step
Establish the next concrete task from fresh GitHub state plus the user's current handoff. Default to read-only until a specific write or production stage is authorized.
