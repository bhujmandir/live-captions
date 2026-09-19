# Live Captions — katha

Live English subtitles for a Gujarati katha, burnt into the vMix output so the hall screens and
the YouTube stream carry them from one signal.

The vocabulary below is not decoration. Most of these words appear **in the captions themselves**,
so a careless synonym here becomes a wrong word in front of the sabha. Where a term has a
preferred rendering, that rendering is the one the tool is briefed to produce.

## Language — the occasion

**sabha**:
The assembled congregation at the katha — the people the captions are for.
_Avoid_: sangh, congregation, audience, crowd

**katha**:
The recital and exposition of scripture given by the swami. It is the event and the discourse
at once. Never a "story" — that rendering was the first thing the briefed translator fixed.
_Avoid_: story, sermon, talk, lecture, reading

**swami**:
The speaker giving the katha.
_Avoid_: priest, monk, preacher

**sant**:
A holy person in the tradition; a term of respect, not a rank.
_Avoid_: saint, monk, holy man

**Bhagvan**:
God, as named in the katha. Manish's spelling, with a **v**.
_Avoid_: Bhagwan, God, the Lord, the deity

**mandir**:
The temple. Here specifically S.K.S.S. Temple Bolton.
_Avoid_: temple

**shastra**:
Scripture, as a body of authoritative text.
_Avoid_: scripture, holy book, text

**Patotsav**:
The festival occasion the katha is part of — the 53rd, at the time of writing.
_Avoid_: anniversary, festival, celebration

**the hall**:
The physical room and its screens, as distinct from the YouTube stream. Used when the point is
what people are reading in the room, which is the surface with no recovery when it goes wrong.
_Avoid_: venue, room, audience

## Language — the captions

**caption**:
One delivered unit of translated text, as it comes off the ladder. It is what the tool
*produces*. It is **not** what is on screen — since the rolling scroll, what the sabha reads is
two **lines** that may have come from different captions.
_Avoid_: subtitle (when the burnt-in overlay is meant), text, line

**line**:
One row of words as the sabha reads it — the unit Manish gives all his instructions in: *"2 on
the screen and 3rd 1 is on the queue and 4th one is being constructed"*. A line is a rendering
fact, not a language one: the same released sentence is one line at 60px and three at 140px.
Four states, and they are distinct:

- **visible** — on screen, being read.
- **queued** — complete, waiting its turn to scroll on.
- **building** — arriving, not yet a whole line, never shown.
- **shown** — already read and scrolled off.

_Avoid_: row, segment, block, caption (for a line)

**whole lines only**:
The rule that a line scrolls on complete or not at all. Its purpose is that the sabha never
watches a sentence assemble itself — the operator gets that cue instead, at the desk.
_Avoid_: atomic, buffered

**final**:
One completed unit of recognised speech as the speech service hands it over. A final is **not**
a sentence — a speaker drawing breath produces two finals from one sentence.
_Avoid_: utterance, segment, result

**fragment**:
A final that does not stand on its own. Translating one is where the English goes wrong, because
Gujarati puts the verb last and a fragment cut before the verb has no predicate in it.
_Avoid_: partial, chunk

**released sentence**:
What the assembler hands onward once it judges the fragments to have formed a whole sentence.
This, not the final, is the unit that gets translated and shown.
_Avoid_: sentence (unqualified), block

**overlay**:
The transparent surface vMix composites over the picture. It is what the sabha actually reads.
_Avoid_: widget, banner, lower-third, graphic

**the panel**:
The translucent slab behind the caption text, so the words stay readable over any picture.
Manish calls this *"the 25% capacity block background"* — and that is the collision to know
about: **his "block" is this, and the tool's "block" is the other thing below.** Say *panel*.
It is one fixed rectangle on purpose; a panel that hugged the text would change shape on every
line, and a moving background is harder to read past than a still one.
_Avoid_: block, background, backdrop, box

**the reserved block**:
A rectangle *inside* the caption area that the words must flow around — for a logo, a lower
third, anything already occupying that part of the picture. Nothing to do with **the panel**.
Where the two must appear together, say *reserved block* in full.
_Avoid_: block (unqualified), mask, exclusion zone

**program** / **preview**:
The two vMix outputs. **Program** is on air; **preview** is the pane the operator checks first.
A caption fault found in preview costs nothing; the same fault in program is seen by everyone.
_Avoid_: live/staging, output/monitor

**the ladder**:
The ordered set of translators, best first, where every step still produces words. Each step is
a **rung**. The bottom rung returns the source text, because a blank caption in a full hall is
the one outcome with no recovery.
_Avoid_: chain, fallback stack, pipeline

**the brief**:
The plain-English description of the occasion given to the translator on every line — what a
katha is, who is speaking, what register to use.
_Avoid_: prompt, system message, instructions

**the reference**:
The day's passage in published English, given to the translator alongside the brief. It exists
to repair *mishearings* rather than word choices: told what passage is being read, the right
words become the expected ones.
_Avoid_: source text, transcript, ground truth

**the glossary**:
The file of Gujarati terms and their agreed English renderings. It is the whole quality margin —
the same model asked cold makes the cheap translator's mistakes.
_Avoid_: dictionary, term list, mapping

**replay**:
Streaming an existing recording through the real pipeline to judge a change. A katha happens
once; a replay can be run as often as needed, so it is the instrument of record.
_Avoid_: playback, simulation, test run

## Language — the surfaces, and what "working" means

**surface**:
One browser rendering the captions and reporting back what it believes is on screen. There is
never only one — the hall overlay, the operator's preview, a spare screen — and they can
disagree. The word matters because a fault is nearly always *a* surface's fault, not the tool's.
_Avoid_: client, screen, instance, page

**the desk**:
Where the operator sits and what they see: the operator page, the vMix preview, the status
board. The counterpart to **the hall**. The distinction is the whole basis of what gets shown
where — an operator must be told about a fault as soon as it is real, and the sabha must not be
told about one that fixes itself.
_Avoid_: the booth, the control room, admin, the dashboard

**on air**:
Composited onto the programme output and therefore actually in front of the sabha. Distinct
from **capturing** and from a surface being healthy: the tool can be producing captions that
nothing is showing, and that failure looks identical to success on the status board.

⚠️ **Nothing inside this tool can tell you whether captions are on air.** The status board sees
surfaces; the vMix API can be asked which input sits on which overlay channel. Neither is the
answer. On 4 September the overlay channels read as though captions were off and they were on —
Manish settled it in seconds by opening the YouTube stream. **Look at the output.** An inference
from overlay numbers is a guess, and reporting one as a fault costs somebody a scare mid-katha.
_Avoid_: live (ambiguous with "the live katha"), visible, active

**the server link** / **the speech link**:
Two different connections, and nearly every wrong decision in this tool has come from treating
them as one. **The server link** is a browser's own connection to the captions server; when it
is down that browser knows nothing else, including whether anything is capturing. **The speech
link** is the server's connection to the speech service; it only means anything once the server
can be heard, and only counts as a fault while a session is capturing.
_Avoid_: the connection, connectivity, "the link" unqualified

**capturing**:
A session is open and audio is being read from the device. The honest opposite of "running" —
the server can be up, reachable and doing nothing. What the sabha sees is unaffected either way,
which is why the tool has to say which it is out loud.
_Avoid_: running, live, active, on

## Language — dropping things

Three different things get dropped and they are not interchangeable. Never write *drops*
unqualified.

**dropped audio**:
Sound discarded before it ever reaches the speech service, because the pipe backed up — usually
across a reconnect gap. The newest audio is kept and the oldest thrown, on the grounds that
stale audio is worth less than current audio. **Nothing that was ever transcribed is lost here**,
because none of it was transcribed. This is the one most often misread.
_Avoid_: lost captions, dropped captions, session drops

**dropped by the device**:
Audio the capture device itself could not hand over. A hardware or driver symptom, not a
pipeline one.
_Avoid_: input drops

**dropped lines**:
Completed lines the hall **never read** — the only one of the three that is lost meaning. A
non-zero count here is a caption the sabha was owed and did not get.
_Avoid_: skipped, overflow

## Language — the machines

**the mandir PC**:
The Windows machine at S.K.S.S. Bolton that runs vMix and the captions server. The only machine
where any of this is real. It is spoken to through issues and comments on the tracker; it is not
reachable any other way.
_Avoid_: the server, the box, prod, the control room PC

**the outage**:
Time during which the hall had no captions, measured from the sabha's side and nothing else. It
is not "the server crashed" — the 98 minutes of 3 September were a server that had never been
started at all, and the two later ones were a window somebody closed. Counting from the hall is
what keeps all three the same kind of event.
_Avoid_: downtime, the incident, the crash
