# Clips and narration to generate for shoot c46bbca496

10 grok Imagine requests (5 first attempts, the rest second attempts) and 2 Gemini TTS requests
(narration: en, es, one request per language). Needs Python 3, a signed-in `grok` CLI with Grok's privacy
mode opted in for video, and the Gemini key in the macOS Keychain (service gemini-api-key-spurs-film, account
$USER); nothing else. The runner reads the key in-process and never prints or writes it.

    python3 run_grok.py --dry-run     # see what will be sent (no key read, no calls)
    python3 run_grok.py --tts-only    # the narrations only
    python3 run_grok.py --first-only  # narrations and first clip attempts: about 10% of the weekly allowance
    python3 run_grok.py               # everything still missing: about 20% of the allowance in total

Calls run one at a time, 20 s apart, and each clip took 41-65 s in the lab. A call whose arguments grok
changed is recorded as failed, not kept. The runner stops if the allowance runs out; rerun it later and it
skips clips already made.

Send the folder back (clips/, audio/ and results.jsonl matter) and run on the PC:

    shootagent film-import-media <this folder>
