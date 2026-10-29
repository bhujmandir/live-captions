# Setting up a new hall PC: a runbook you can follow alone

This takes a clean Windows PC to working captions, and checks every step before the next. You
need a PowerShell on the hall PC. You do not need anyone at the keyboard except where a step
says **needs a person**. Read [`WINDOWS.md`](WINDOWS.md) for *why* anything is the way it is;
this file only says what to do and what you must see before going on.

**How to use it.** Do the steps in order. Each ends with a **Done when:** line: the exact log
line, answer or file contents you must see. If you do not see it, stop, fix that step, and do not
move on. When a step says **[UNVERIFIED]**, nobody has seen that work on a real Windows PC or a
real vMix yet. Try it, and if it fails use the by-hand fallback given, then say so in your report
(Step 10).

**Rules that hold throughout**

- Never print, paste or commit a key's value. Check that a key is set by its *name* only.
- Every problem goes onto a GitHub issue **the moment it happens** (Step 0), not at the end.
- Do not run `git pull`, update or re-register anything on a day of katha. Do this setup on a day
  with no katha, with vMix not on air or streaming.
- Run every command from the checkout folder (`live-captions\`), in an ordinary (not elevated)
  PowerShell, as the Windows user who will run katha. Both background jobs belong to that user.
- Use full names, never numbers, for the audio input and the vMix input. Both numbers move.

**What the PC needs:** Windows 10 or 11 with PowerShell 5.1 (it is already there), internet
access, `git` (or the folder copied over by other means), and `curl.exe` (part of Windows 10
and later). `deploy.ps1` installs the rest: `uv`, Python, and Node with pnpm through `winget`.

You need a Sarvam API key and a Gemini API key on the paid tier (Step 2 walks a person through
making them), the name of the hall's mixer feed into this PC (for example "the Line In"), a Gujarati recording
to play into it. Windows must allow desktop apps to use the
microphone (Settings, Privacy & security, Microphone): `deploy.ps1` reminds you, and without it
the level stays at zero.

## Step 0: Be able to report a problem

Every problem you meet, in setup or in the dry run, goes onto a GitHub issue in
`bhujmandir/live-captions` the moment it happens, one issue per problem. Do not wait to batch
them: by the end you will have forgotten the exact output. The repository is public, so an issue
must **never** contain an API key, the contents of `.env`, or caption transcripts. Paste log
lines, never the `.env` file.

Each issue says:

- **the step** it happened in (for example "Step 5, register");
- **the exact command** you ran;
- **the exact output or log lines**, pasted, not summarised;
- **what you expected** to see instead.

Check that `gh` (the GitHub command line) is installed and logged in:

```powershell
gh auth status
```

If `gh` is not found, install it with `winget install --id GitHub.cli`, open a new PowerShell,
and run `gh auth login` (choose GitHub.com and a browser or token login). **This needs a person**
who has a GitHub account that may open issues on a public repository. Do not go on without it.
If it still cannot be done, the helper in Step 1 writes each issue to a file under `logs\`
instead, so nothing is lost, and you tell the person to file them by hand.

**Done when:** `gh auth status` prints `Logged in to github.com`, or you have written down that it
could not be done and why (and every later issue goes to a file).

## Step 1: Get the code

```powershell
git clone https://github.com/bhujmandir/live-captions
cd live-captions
git rev-parse --short HEAD
```

Write the short commit down: it goes in your report. If the PC has no git, copy the folder over
instead (see [`WINDOWS.md`](WINDOWS.md), "One-time setup").

Then define the helper that files a problem. It saves the text under `logs\` first, so if `gh`
is missing or fails the text is still there to file by hand:

```powershell
function New-SetupIssue([string]$title, [string]$body) {
    New-Item -ItemType Directory -Force logs | Out-Null
    $file = "logs\issue-$(Get-Date -Format 'yyyyMMdd-HHmmss').md"
    $body | Set-Content $file -Encoding UTF8
    $filed = $false
    try { gh issue create -R bhujmandir/live-captions --title $title --body-file $file; $filed = ($LASTEXITCODE -eq 0) } catch { }
    if (-not $filed) { Write-Host "NOT FILED. The text is kept in $file: file it by hand." -ForegroundColor Red }
}
```

Use it as `New-SetupIssue 'Setup step 5: register failed' $text`, where `$text` holds the four
things above. It prints the new issue's address: keep every address for the closing issue in
Step 10. (A new PowerShell forgets the function: define it again.)

**Done when:** `Test-Path deploy.ps1, register-startup-task.ps1, overlay-gate.example.json` prints
`True` three times, `git rev-parse --short HEAD` printed a commit, and `Get-Command New-SetupIssue`
finds the helper.

## Step 2: Make the two API keys and put them in `.env`

This is a human step: it needs a person with an account, a payment method and a browser. Setup
**pauses here until both keys exist and are in `.env`**. Tell the person what to do, and do not
make an account for them.

**Sarvam (required: it does the listening).** Make an account and an API key at
<https://dashboard.sarvam.ai>. That address redirects to Sarvam's current sign-in page; the
page could not be opened from here, so the exact button names are unconfirmed: look for the
section that creates **API keys**. Around £8 buys a 28-hour event (README). Look in the dashboard
for any free credit; none is promised here. Without this key nothing is heard.

**Gemini (the translator, on the paid tier).** Make an API key at
<https://aistudio.google.com/apikey> (it asks for a Google sign-in). Then turn billing on: Google's
guide (<https://ai.google.dev/gemini-api/docs/billing>) says a project on the free tier shows a
**Set up billing** button in Google AI Studio, which links a Cloud Billing account. The button may
be named differently. The reason is that the free tier cannot run this live: a speaker produces
roughly 15 captions a minute, which is the free tier's per-minute limit, and a six-minute test hit
a rate-limit error (README, "What you need before you start"). Around £2.50 covers a 28-hour
event. The paid tier (called Tier 1 in the README) is what the captions need.

**Putting the keys in.** In the checkout:

```powershell
Copy-Item .env.template .env
notepad .env
```

- Find `SARVAM_API_KEY=` and paste the key straight after the `=`.
- `.env.template` has the Gemini line commented out and with a note after it:
  `# GEMINI_API_KEY=   # required for gemini; never commit it`. Replace that whole line with
  `GEMINI_API_KEY=` followed by the key, nothing after it. `deploy.ps1` only asks for the Sarvam
  key, so it will not remind you of this one.
- Save the file. Never paste a key into an issue, a chat or a commit.

**Done when:**

```powershell
Select-String -Path .env -Pattern '^(SARVAM_API_KEY|GEMINI_API_KEY)=\s*[^\s#]' | ForEach-Object { $_.Matches[0].Groups[1].Value + ' is set' }
```

prints `SARVAM_API_KEY is set` and `GEMINI_API_KEY is set`. That the keys *work* is proved at the
first test start in Step 6: the log must name Gemini as the translator, and must not say that
every line will come from Mayura.

## Step 3: Install

Install (the keys are already in `.env`, so it does not wait for typing):

```powershell
powershell -ExecutionPolicy Bypass -File deploy.ps1
```

It installs `uv`, Python packages, Node and pnpm, builds the web bundle, then **starts the server
in this window** and stays there. A Windows Firewall prompt may appear on the first start: Allow
it on Private networks. A browser tab with the operator page opens by itself; leave it. Leave the
install window alone, and open a second PowerShell in the same folder for the rest
(`cd` to the `live-captions` folder; a new PowerShell starts in your home folder).

**Done when:** in the second PowerShell,

```powershell
(Invoke-WebRequest -UseBasicParsing http://localhost:8765/api/config).StatusCode
Test-Path web\dist\index.html
Test-Path .venv\Scripts\pythonw.exe
```

prints `200`, `True` and `True`. The installer's
last block says in red that the logon tasks are missing: expected until Step 5.
(`pythonw.exe` is what runs the gate without a window; **[UNVERIFIED]** that `uv` puts it in
`.venv` on Windows. If it is `False`, stop: nothing after this works.)

## Step 4: Stop the server the installer left running

Registering the tasks while this server is up would leave it unsupervised, dying with its
window. Stop it the way `stop-captions.bat` does, without the pause that waits for a key:

```powershell
curl.exe -f -s -S -X POST -H "X-Captions-Control: shutdown" http://127.0.0.1:8765/api/shutdown
```

**Done when:** a few seconds later `Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue`
prints nothing, and the install window says the server stopped. Close that window.

## Step 5: Register the two background jobs

```powershell
powershell -ExecutionPolicy Bypass -File register-startup-task.ps1 -StartNow
$LASTEXITCODE
```

This registers the server job (`LiveCaptions`) and the gate job (`LiveCaptionsOverlayGate`) to run
at logon with no window. What each does, how they restart, and how to remove them (`-Remove`) is
in [`WINDOWS.md`](WINDOWS.md), "Boot behaviour".

If this PC was set up by hand before (a task named otherwise that runs an old copy of the gate,
or `LiveCaptionsAdhoc`), follow "A PC that already has hand-made tasks" in
[`WINDOWS.md`](WINDOWS.md) now, or two gates will both start captures.

**Done when:** the script printed two green lines `OK Task ... registered and read back` and
`$LASTEXITCODE` is `0`, and

```powershell
Get-ScheduledTask -TaskName LiveCaptions,LiveCaptionsOverlayGate | ForEach-Object { "$($_.TaskName) $($_.State) restarts=$($_.Settings.RestartCount)" }
(Invoke-WebRequest -UseBasicParsing http://localhost:8765/api/config).StatusCode
Get-ScheduledTask -TaskName LiveCaptionsAdhoc -ErrorAction SilentlyContinue
```

shows both tasks (state `Running`, `restarts=999` for each) and `200`, and the last command
prints nothing. If
the script said *Access is denied*, re-run it from an elevated PowerShell and put that in your
report: [`WINDOWS.md`](WINDOWS.md) says an ordinary PowerShell is enough, and that is then wrong.

## Step 6: Find the hall's audio input by name

```powershell
(Invoke-RestMethod http://localhost:8765/api/devices).devices | Format-Table id, deviceName, hostApi, default, worksAt16k -AutoSize
```

Windows lists one physical input several times, once per audio API, all under the same name. Pick
the entry like this:

1. Its `deviceName` is the hall's mixer feed (the name you were given, for example one that
   starts `Line In`). If two inputs could be it, ask rather than guess. Never `Stereo Mix`, never
   a webcam microphone.
2. Its `hostApi` is `MME` or `Windows DirectSound`, and `worksAt16k` is `True`. The WASAPI and
   WDM-KS entries do not work at the rate captions are captured at.
3. Never choose by `id`. It is the number that moved in katha week.

Write down the exact `deviceName` and `hostApi` of your choice. Keep the characters exactly as
listed (brackets, `(R)`, spaces).

**A first test start.** This proves the audio arrives, the keys work, and English comes out.
**Needs a person** to play or speak Gujarati into the mixer feed for about 30 seconds. Put the two
values you wrote down in `$name` and `$api`:

```powershell
$name = 'Line In (Realtek(R) Audio)'; $api = 'MME'     # the Bolton hall's; use your own
$body = @{ source = 'device'; device_name = $name; device_host_api = $api } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://localhost:8765/api/start -ContentType 'application/json' -Body $body
Start-Sleep -Seconds 40
Select-String -Path logs\live-captions.log -Pattern 'Translator:' -Encoding UTF8 | Select-Object -Last 2 | ForEach-Object Line
Select-String -Path logs\live-captions.log -Pattern 'FINAL' -Encoding UTF8 | Select-Object -Last 3 | ForEach-Object Line
Invoke-RestMethod -Method Post -Uri http://localhost:8765/api/stop
```

The start uses the server's own default languages (`DEFAULT_SOURCE_LANG` and `DEFAULT_TARGET_LANG`
in `.env`, or the template's); set them there if the hall's direction is different, before this
test. The `Translator:` line is written when a capture starts, not when the server starts.

**Done when:** you have one row with the hall's name, `hostApi` of `MME` or `Windows DirectSound`
and `worksAt16k` `True`, a person confirmed the name is the mixer feed, and the test start gave

- a line `Translator: Gemini <model>, Mayura underneath, N glossary terms` (the model name is
  whatever `.env` sets), and **no** warning that says the Gemini key is missing and that *every
  line will come from Mayura*. If you see that warning, the key is not being read: re-check
  Step 2 and restart the `LiveCaptions` task;
- at least one `FINAL` line whose text is English;
- the stop answered, and `Invoke-RestMethod http://localhost:8765/api/overlay-status` shows
  `capture.running` `False` before you go on.

If the level stayed at zero or no `FINAL` line came, file an issue (Step 0) before changing
anything: it is the first sign of a wrong device, Windows blocking the microphone, or a bad key.

## Step 7: Tell the gate which input to use

Write `overlay-gate.json` from the example, changing only the two device keys. Replace the two
values with what you wrote down in Step 6:

```powershell
$g = Get-Content overlay-gate.example.json -Raw | ConvertFrom-Json
$g.device_name     = 'Line In (Realtek(R) Audio)'
$g.device_host_api = 'MME'
$g | ConvertTo-Json -Depth 5 | Set-Content overlay-gate.json -Encoding UTF8   # Windows PowerShell adds a BOM; the gate reads it
Stop-ScheduledTask  -TaskName LiveCaptionsOverlayGate
Start-ScheduledTask -TaskName LiveCaptionsOverlayGate
```

(`'Line In (Realtek(R) Audio)'` is the Bolton hall's. Use your own.) `overlay-gate.json` is not
tracked, so an update never overwrites it. The other keys and what they do are in
[`WINDOWS.md`](WINDOWS.md), "The overlay gate and its settings".

**Done when:** after a few seconds,

```powershell
Get-Content logs\overlay-gate.log -Tail 6
```

shows a line like `overlay gate armed: follows overlay 2 carrying 'Captions Overlay', ... device 'Line In (Realtek(R) Audio)' (MME), ...` naming **your** device and host API, and with no `no device_name set` line among the lines written
since the restart (that line means the file was not read, or the name is empty). Lines saying
`vMix unreadable` are expected until Step 8.

## Step 8: Set up vMix through its web API

vMix must be running, with its web controller on (see 8.1). The address below is the default and
is also the gate's default (`vmix_api`). Everything here is a plain HTTP request to it; the
functions come from vMix's own reference (see the note at the end of this step).

Put these helpers in your PowerShell first:

```powershell
$vmix = 'http://127.0.0.1:8088/api/'
function Get-Vmix { [xml](Invoke-WebRequest -UseBasicParsing $vmix).Content }
function Send-Vmix($function, $params = @{}) {
    $q = New-Object System.Collections.Generic.List[string]
    $q.Add("Function=$function")
    foreach ($k in $params.Keys) { $q.Add("$k=" + [uri]::EscapeDataString([string]$params[$k])) }
    Invoke-WebRequest -UseBasicParsing ($vmix + '?' + ($q -join '&')) | Out-Null
}
$url = (Get-Content overlay-gate.example.json -Raw | ConvertFrom-Json).overlay_url
$title = 'Captions Overlay'
```

`$url` is the gate's own default overlay URL, so what you put in vMix is what the gate would
navigate to. It has no comma (vMix refuses a comma in a browser URL). If a hall's
`overlay_url` ever needs one, write it as `%2C` inside the URL before sending, which is what the
gate does.

**8.1 The web API answers.**

```powershell
(Get-Vmix).vmix.version
```

**Done when:** that prints vMix's version (write it down), and the request did not fail. If
nothing answers, enable vMix's web controller by hand: vMix Settings, the *Web Controller* tab
(called *Web* in vMix's docs), tick enable, port `8088`. **[UNVERIFIED]** the menu wording on
vMix 26.

**8.2 Add the browser input.** Skip to 8.3 if an input titled `Captions Overlay` already exists
(`(Get-Vmix).vmix.inputs.input | Where-Object title -eq $title`).

```powershell
$before = @((Get-Vmix).vmix.inputs.input | ForEach-Object key)
Send-Vmix 'AddInput' @{ Value = "Browser|$url" }
Start-Sleep -Seconds 2
$new = (Get-Vmix).vmix.inputs.input | Where-Object { $before -notcontains $_.key }
$new | Format-List key, number, type, title
```

**Done when:** exactly one input is new, and its `type` says it is a browser input. **[UNVERIFIED]**
`AddInput` with `Browser|<url>` is not in vMix's published function table (see the note below).
It has worked on the Bolton vMix 26 with a `file:///` page, and is shown on vMix's forum with an
`https://` URL; nobody has seen it with this URL. If it fails (a 500, no new input, or an input
of another type) use the by-hand fallback: [`WINDOWS.md`](WINDOWS.md), "Setting up vMix", steps 2
and 3, then continue at 8.3.

**8.3 Name it `Captions Overlay`.** The gate finds the input by this title, with those capitals
and the space.

```powershell
Send-Vmix 'SetInputName' @{ Input = $new.key; Value = $title }
$cap = (Get-Vmix).vmix.inputs.input | Where-Object title -eq $title
$cap | Format-List key, number, type, title
```

**Done when:** exactly one input has `title` `Captions Overlay`, and you have its `key`, `type`
and `number` on screen. Write down the `type` in your report (it is not documented what vMix 26
prints for a browser input). If two inputs carry the title, remove the extra by hand: the gate
will use the first.

**8.4 Point it at the agreed URL, and check the look.** A new input can be sized and styled
differently from the Bolton one. Navigate it explicitly, as the gate would, then look at it:

```powershell
Send-Vmix 'BrowserNavigate' @{ Input = $cap.key; Value = $url }
```

`BrowserNavigate` and its `Input` and `Value` parameters are in vMix's reference, and `Input` may
be a number, a title or the key. **[UNVERIFIED]** on vMix 26 with `Input=<key>`: [`WINDOWS.md`](WINDOWS.md),
dry run C2, is the test.

**Done when:** within about 20 seconds,
`(Invoke-RestMethod http://localhost:8765/api/overlay-status).surfaces | Format-Table surface, stale`
lists a surface whose name starts `overlay-` and whose `stale` is `False`. That proves vMix
loaded the page from the server. (**Needs a person** to *see* two lines on a 25% panel with the
label bottom right, once, with sound, in Step 9; vMix's browser input is an old Chromium that can
look different from Chrome.)

**8.5 Put it on overlay 2, and take it off.** Overlay 2 is the desk's captions button, and the
gate follows it. This puts the captions on the program output (and on anything streaming or
recording), so only do it off air.

```powershell
Send-Vmix 'OverlayInput2In' @{ Input = $cap.key }
Start-Sleep -Seconds 3
$ov = (Get-Vmix).vmix.overlays.overlay | Where-Object number -eq '2'
"overlay 2 carries input number: " + $ov.'#text'   # must equal $cap.number
Get-Content logs\overlay-gate.log -Tail 4
Send-Vmix 'OverlayInput2Out'
```

**Done when:** the printed number equals `$cap.number`, and the gate log shows `overlay 2 ON`
followed by a `capture start: device '<your device>'` line naming the Step 6 device (the server
needs the audio input to start, so this also proves the name resolves). About 10 seconds after the
`OverlayInput2Out`, the log shows `overlay 2 OFF` and `capture stop`.
`OverlayInput2In`, `OverlayInput2Out` and the `Input` parameter are in vMix's reference.
vMix's API page shows the overlay's input number as the text of `<overlays><overlay number="2">`,
which is how the gate reads it, but nobody has seen that on vMix 26. **[UNVERIFIED]** too: that
the gate treats an API toggle exactly like the button. If the number does not appear, or no `overlay 2 ON` line
comes, **needs a person**: have them press overlay 2 on `Captions Overlay` in vMix, then off,
and record that the API route did not work.

**8.6 Save the preset, so vMix brings the input back at its next start.** vMix restores the URL
saved in its preset, not one changed later by navigating. So save now, with the input
carrying the agreed URL from 8.4.

Ask whoever asked for the setup which preset file the hall opens at the start of katha. If it is
an existing one, **make a copy of it first**, and only then save over it. If there is none yet,
choose a new name.

```powershell
$preset = 'C:\path\to\the-hall-preset.vmix'     # the hall's own preset file
if (Test-Path $preset) { Copy-Item $preset "$preset.before-captions"; Test-Path "$preset.before-captions" }   # must print True
Send-Vmix 'SavePreset' @{ Value = $preset }
Start-Sleep -Seconds 2
(Get-Item $preset).LastWriteTime
[bool](Select-String -Path $preset -Pattern 'Captions Overlay' -SimpleMatch -Quiet)
[bool](Select-String -Path $preset -Pattern 'tbg=25' -SimpleMatch -Quiet)
```

`SavePreset` with a file name in `Value` is in vMix's reference.

**Done when:** the backup printed `True` (or there was no old preset), `LastWriteTime` is the last
minute, and both checks print `True` (the file contains `Captions Overlay` and `tbg=25`). That the *next* vMix start opens this preset by itself is not in the reference (it
lists `OpenPreset` and `LastPreset` but no setting for a start-up preset): **[UNVERIFIED]**, and
proved only by the dry run, Part E1, which **needs a person** to close and reopen vMix. If vMix does
not reopen it by itself, say so: the desk has to open this preset after starting vMix. By hand:
[`WINDOWS.md`](WINDOWS.md), "Setting up vMix", step 5.

**Where these functions come from.** vMix's own function reference,
<https://www.vmix.com/help26/ShortcutFunctionReference.html>, and the API page,
<https://www.vmix.com/help26/DeveloperAPI.html>, were read for this file. Confirmed there:
`SetInputName` (Input, Value=name), `BrowserNavigate` (Input, Value=URL), `OverlayInput2In`
(Input), `OverlayInput2Out`, `SavePreset` (Value=file name), `OpenPreset`, `RemoveInput`; the API
address `http://127.0.0.1:8088/api/`; `Input` as number, case-sensitive title or key; the
state XML with each input's `key`, `number`, `type`, `title` and an `<overlays><overlay number="…">`
holding the input number; success as HTTP 200 and errors as HTTP 500. **Not confirmed there:**
`AddInput` with `Browser|<url>` (the table lists Video, Image, Photos, Title, VideoList, Colour,
AudioFile, Flash and PowerPoint only), how to URL-encode `Value` (the page is silent; this file
encodes it), what `type` a browser input shows, a preset path in the XML (there is none), and
any start-up preset setting.

## Step 9: Prove it with the dry run

Run [`WINDOWS.md`](WINDOWS.md), "Dry run", Parts A to G. Steps 1 to 8 above have already done
the install and vMix parts of A and C; the dry run's A items re-check them. For each item write
`passed`, `failed` (what you saw) or `not done` (why).

What you can drive from a shell: A1 to A4, B2, C1 and C2 (Step 8 gave you the helpers), D1 and
D4 by `OverlayInput2In` and `OverlayInput2Out`, E3 and E3b (killing the server and the
supervisor), E4 (`stop-captions.bat`), E5 (`Stop-ScheduledTask`), F1 and F2 (F2 only on a PC with
a local `rules.json` edit), G1. A5 only applies on a PC that had hand-made tasks; say `not done`
with that reason on a clean one.

What **needs a person**, and cannot be done from a shell:

- **Audio.** D2 (ten minutes of Gujarati played into the mixer feed), and the check that the
  captions on the hall screen look right: two lines, a 25% panel, the label bottom right.
- **Signing out of Windows and back in (B1).** Your shell ends with the session. Run the checks
  after the person is back in.
- **Closing and reopening vMix** (E1, E2), and pressing overlay 2 on the vMix screen if the API
  route in 8.5 was not accepted (D1 to D4, E1, E2). D3, a flick of the button inside 10 seconds,
  is best done by a hand too.
- **Disabling or unplugging the audio input** (E6; disabling in Windows Sound settings is easier
  than pulling a cable, and sometimes needs administrator rights).
- **Leaving the PC on past midnight** to see the logs roll over (E7).
- **B3**, the operator page remembering the input, which needs a browser.

Do the dry run on a day with no katha, and do not skip an item because the one before passed.

**Done when:** every dry-run item has a result written next to it, and the ones that failed are
named. A failed item is not a finished setup: the fix is a change to the code or the docs, made by
the people who maintain this, not a note saying it was fine. File the failures as issues (Step 0).

## Step 10: File the closing issue

Every problem should already be on its own issue (Step 0). Now file **one closing issue** in
`bhujmandir/live-captions` for the whole setup. Use the helper from Step 1, or
`gh issue create -R bhujmandir/live-captions --title "Setup report" --body-file <file>`. Its body:

- the commit (`git rev-parse --short HEAD`), the Windows version (`winver`) and the vMix version
  (from 8.1);
- the device name and host API you chose in Step 6, and who confirmed it;
- the `type` vMix showed for the browser input, and the path of the preset you saved;
- **the dry-run results, Parts A to G, tick by tick**: every item with `passed`, `failed` or
  `not done` (why), and the address of the problem issue for each one that failed;
- the address of every problem issue you filed;
- every **[UNVERIFIED]** line above, and what happened when you tried it.

Keep keys, `.env` contents and caption transcripts out of it, as out of every issue.

**Done when:** `gh issue list -R bhujmandir/live-captions --search "Setup report" --limit 3` shows
the closing issue (or its text is in a file under `logs\` with the reason `gh` could not file it),
and it contains no key, password or personal path.

## Report what you did not prove

End the closing issue with a plain list of what you did not see work: each item skipped or only half
done, each **[UNVERIFIED]** that you could not try (and why), anything that passed only because
you assumed it would, and anything a person had to do by hand that the runbook said a shell
could. "Should work" is not done. A hall that learns what was not proven can check it before
katha; a hall that was told it all worked finds out on air.
