# OpenAI natural-language planner

The local speech recognizer still transcribes audio. **Interpret natural language** sends
the editable text, current command list, local endpoint coordinates and insertion state
to OpenAI. Geographic home coordinates and microphone audio are not included in that
request. The Responses API uses structured outputs, `store: false`, and defaults to
`gpt-5.4-mini`; set `OPENAI_PLANNER_MODEL` to override it.

## Enable locally

Create an API key in your OpenAI API account with API billing/access configured.
Do not paste the key into chat, browser code, or Git. Stop the existing planner server,
then run this in PowerShell from the repository:

```powershell
$plannerKey = Read-Host 'OpenAI API key' -AsSecureString
$env:OPENAI_API_KEY = [System.Net.NetworkCredential]::new('', $plannerKey).Password
.venv/Scripts/python.exe -m integrations.flight_bridge --port 8766
```

The key is held in this shell's environment and inherited by the backend. It is not
written to a file. Close the shell when finished. Reload http://127.0.0.1:8766/planner/.

## Use

1. Type or speak naturally, select where to insert commands, and click **Interpret natural language**.
2. If asked a question, expand the instruction with the missing details and interpret again.
3. Review the proposed command list and route. Click **Add reviewed commands** to accept.

Example: “Head ten meters north of waypoint seven, wait five seconds, then come home.”
References are the current numbered command endpoints. Reordering changes numeric references.
The AI proposes insertions only, not deletion or replacement of existing commands.
Instructions that change while a request is running invalidate the proposal.
The proposal does not update the shared visualizer feed until it is accepted and revalidated.
Export is disabled while reviewing a proposal. No AI response arms or operates an aircraft.

Missing distances, orbit radius/direction/laps, or unclear references should produce a
clarification question. This behavior is model-dependent: deterministic validation checks
syntax, state and configured geometry limits, but cannot prove that the model understood
the user's intent. Review is required. No live model evaluation was possible without a key.

Official references:
- https://developers.openai.com/api/docs/guides/structured-outputs
- https://developers.openai.com/api/docs/models/gpt-5.4-mini
