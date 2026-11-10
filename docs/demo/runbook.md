# Demo recording runbook

Use the [timed script](script.md) and [YouTube copy](youtube-description.md). The full take requires verified sandbox authorization, capture, webhook, and void events, a measured benchmark, and the actual SDK call. The current `ritika` dashboard can rehearse the flow in stub mode.

## Before recording

- [ ] Record on the device running the project. Open the dashboard, agent or mock merchant view, and recording app on that device.
- [ ] For the live take, reset only the dedicated demo database through Vivek's approved reset or restore procedure. Apply migrations, start the services, verify `/health`, and run the [sandbox seed](../deployment-checklist.md#manual-seed-trigger) once. Confirm the seed has allow, step-up, and block rows. For a stub rehearsal, open a new private window to reset the in-memory state.
- [ ] Open a fresh recording browser profile or private window. Clear prior site data or use a new profile, then complete any sandbox sign-in outside the recording. Check that principal enrollment is ready for that profile before a live take.
- [ ] Set the recording canvas and export to **1920×1080**. Start browser zoom at **100%** and open `/ledger?demo=1`. Verify the ledger columns, inspector, and benchmark legend are readable. The mode stays on through dashboard navigation in that tab. Use `?demo=0` to leave it.
- [ ] Close unrelated tabs and apps. Turn off notifications and messaging previews. Remove personal bookmarks, email addresses, tokens, and sandbox login details from view.
- [ ] Disable background music. Keep third-party logos out of the frame, including during the PayPal popup. Rehearse a crop or mask that keeps the buyer action and order result legible. Check that the final edit has no copyrighted music.
- [ ] Warm the live services and pre-open the mock merchant reveal, a verified webhook timeline, and the measured Benchmark tab. Copy the confirmed SDK one-liner into a clean editor tab. Write down the actual run ID, block rate, false-positive rate, and both ablation rates.
- [ ] Check the live SDK, mocked merchant page, and all payment IDs before narration. Match each evidence clip to the same scenario and recording run.

## Button order

1. Open **Live Ledger** in recording mode. Open an example row for the introduction, then close the inspector.
2. Click **Mandates** → fill task, limits, merchant, and category → **24h** → **Continue to PayPal** → PayPal sandbox approval → **Sign and save mandate**. For rehearsal use **Simulate PayPal approval**.
3. Click **Live Ledger** → select the saved mandate → **Happy path** → newest **Allow** row → **Open full timeline**. Show passing checks, authorization, capture, and webhook.
4. Reveal the instruction on the staged merchant page. Return to **Live Ledger** → **Poisoned page** → newest **Block** row → **Open full timeline**. Show the flagged snippet and void.
5. Click **Back to Live Ledger** → reselect the same mandate → **Budget splitting** → blocked row → **Open full timeline**. Show the risk factor and return to the sidebar.
6. Click **Benchmark** → show **Last run**, headline metrics, and **Ablation comparison** → filter **Class** in **Scenario results**. Switch to the SDK editor tab, then return to the title or closing slate.

## If a step fails

| Failure | Recovery |
| --- | --- |
| Gateway is cold or the stream disconnects | Wait for **Waking up the server** to clear, or use **Retry connection**. Restart the segment from the same mandate and take a cut around the wait. |
| Buyer approval, capture, webhook, or void is missing | Retry in the sandbox outside the take. If a verified clip exists from that run, use it with matching IDs. Otherwise label the segment a stub rehearsal and say “This is simulated checkout and audit data.” Omit live outcome claims. |
| Mock merchant reveal or budget block is unavailable | Show the dashboard's highlighted sample snippet or the stub budget-splitting rationale and keep the **Demo mode** disclosure visible. Say “This replay illustrates the intended check.” |
| Measured report is missing | Keep the **Sample data** banner visible. Say “These sample rows show how we report block rate, false positives, and ablation. The measured run is pending.” Remove numeric performance claims from the description. |
| Agent SDK signature is unconfirmed | Show the confirmed interface instead. In a stub rehearsal, use the dashboard's actual call `await client.runDemoScenario('happy_path', mandateId)` and describe it as the demo trigger. |
| The take runs long | Cut dead time, sign-in, and scrolling. Keep the four verdicts, both payment outcomes, and benchmark disclosure on screen long enough to read. |

## After recording

- [ ] Trim the cut to **2:58** and confirm the exported duration is under **3:00**. Check transitions, narration, captions, and masks at 1080p.
- [ ] Fill the description's URL and evidence placeholders with verified links. Check that the chapter labels still match the final cut and that the disclosure matches the recorded mode.
- [ ] Upload the final video publicly to YouTube. Wait for processing and open the watch link in a private window without signing in. Confirm playback, resolution, captions, and links.
- [ ] Add the verified watch link to the submission materials.
