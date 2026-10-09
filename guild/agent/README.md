# tripwire-release-bot

A Guild Native agent (`PROMPT.md` + `guild.yaml`) that runs routine release checks. Each action goes through the Tripwire checkpoint via the `bindubhargavareddy~tripwire` custom integration. Tripwire records the call and decides it before it runs, and the agent shows up in Tripwire as `guild:deploy-bot`. All tools are record-only.

Deploy steps: `../RUNBOOK.md`.
