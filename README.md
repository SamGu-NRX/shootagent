# Clips to generate for shoot c46bbca496

10 grok Imagine requests (5 first attempts, the rest second attempts). Needs Python 3 and a signed-in
`grok` CLI with Grok's privacy mode opted in for video; nothing else.

    python3 run_grok.py --dry-run     # see what will be sent
    python3 run_grok.py --first-only  # first attempts: about 10% of the weekly allowance
    python3 run_grok.py               # everything still missing: about 20% in total

Calls run one at a time, 20 s apart, and each clip took 41-65 s in the lab. A call whose arguments grok
changed is recorded as failed, not kept. The runner stops if the allowance runs out; rerun it later and it
skips clips already made.

Send the folder back (clips/ and results.jsonl matter) and run on the PC:

    shootagent film-import-media <this folder>
