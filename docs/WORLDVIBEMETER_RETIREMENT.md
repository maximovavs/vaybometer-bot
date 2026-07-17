# WorldVibeMeter publishing status

Daily WorldVibeMeter automation is retired. The English daily, astro, and X
workflows no longer have scheduled triggers, so they cannot publish
automatically to Telegram, Facebook, X, or any other World destination.

The weekly English report remains active every Sunday at 16:00 UTC. It is
collected and rendered by `world_en/world_weekly_collect.py`, delivered to the
main WorldVibeMeter Telegram channel, and saved as a GitHub Actions artifact.
The artifact includes the source JSON, the Telegram HTML message, and a plain
UTF-8 `weekly_youtube.txt` file for manually copying into a YouTube Community
post. No YouTube browser automation is used.

WorldVibeMeter Facebook execution has been removed from the World workflows;
the page can be repurposed without automated World posts. Scheduled X
publishing is also disabled. The X workflow remains available only through an
explicit manual dispatch for archival/manual testing.

Shared Cyprus and Kaliningrad collectors and reusable modules remain active.
This retirement does not change weather, radiation, Safecast, Schumann,
astronomy, FX, provider-health, or image-generation collectors used elsewhere.

## Manual daily workflow runs

In GitHub Actions, open the required workflow and choose **Run workflow**:

- `world-daily-en` — manually render and send to the main or test Telegram
  destination selected in the dispatch form;
- `world-astro-en` — manually render and send to the main or test Telegram
  destination selected in the dispatch form;
- `world-daily-x` — explicit archival/manual X test only.

Manual dispatches are never started by cron. Facebook is not an available
destination in any World workflow.
