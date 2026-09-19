// What the hall reads, over time.
//
// Every case here corresponds to something that actually went wrong on the
// stream during the 53rd Patotsav, or to a property of the display the owner
// settled after watching it. The overlay had no tests at all before this file.
//
// The display is a ROLLING SCROLL OF WHOLE LINES. The owner settled it on
// Day 3: "2 on the screen and 3rd 1 is on the queue and 4th one is being
// constructed ... I want the text to scroll up in a queue as the next line is
// generated. Only full line should be pushed upwards."

import { describe, it, expect } from "vitest";
import { overlayUnderTest, final, VirtualClock } from "./testing";
import {
  CAPTION_CLEAR_AFTER_MS, LINE_MIN_DWELL_MS, LINE_ORPHAN_MS, MAX_QUEUED_LINES,
} from "./store";

// The measured caption arrival interval on this speaker: ~15/min.
const ARRIVAL_MS = 4000;

// A sentence long enough to be cut into several lines at a small budget. Used
// wherever the question is about lines rather than about captions.
const LONG = "Bhagvan Swaminarayan wrote the Shikshapatri with his own hand, "
  + "and set down in it the conduct expected of every satsangi, "
  + "across two hundred and twelve verses.";

/** Tick the clock and record the screen each time it changes. */
function watch(o: ReturnType<typeof overlayUnderTest>, ms: number, opts = {}) {
  const seen: string[] = [];
  for (let t = 0; t < ms; t += 250) {
    o.advance(250, opts);
    const shown = o.onScreen();
    if (seen[seen.length - 1] !== shown) seen.push(shown);
  }
  return seen;
}

describe("the previous line is still on screen", () => {
  // The whole point of the rewrite. The block model replaced each caption
  // wholesale: the eye finished the last line, the screen blanked and refilled,
  // and the reader started again with no thread back to what they had just
  // read — in a discourse where every sentence leans on the one before it.
  it("keeps the line before last in front of the reader", () => {
    const o = overlayUnderTest();

    o.send(final("The Lord's discourse begins."));
    o.advance(LINE_MIN_DWELL_MS + 500);
    o.send(final("Shikshapatri was written by Maharaj."));
    o.advance(LINE_MIN_DWELL_MS + 500);

    expect(o.lines()).toEqual([
      "The Lord's discourse begins.",
      "Shikshapatri was written by Maharaj.",
    ]);
  });

  it("shows as many lines as the box holds and no more", () => {
    const o = overlayUnderTest();
    o.setVisibleLines(2);

    for (let i = 0; i < 6; i++) {
      o.send(final(`sentence ${i}.`));
      o.advance(LINE_MIN_DWELL_MS + 500);
    }

    expect(o.lines().length).toBe(2);
  });

  it("scrolls: the oldest line falls off the top as the newest enters", () => {
    const o = overlayUnderTest();
    o.setVisibleLines(2);

    o.send(final("one."));
    o.advance(LINE_MIN_DWELL_MS + 500);
    o.send(final("two."));
    o.advance(LINE_MIN_DWELL_MS + 500);
    expect(o.lines()).toEqual(["one.", "two."]);

    o.send(final("three."));
    o.advance(LINE_MIN_DWELL_MS + 500);
    expect(o.lines()).toEqual(["two.", "three."]);
  });

  it("moves by ONE line at a time, never two", () => {
    // "Only full line should be pushed upwards". A screen that jumps by two
    // lines is a screen that blanked and refilled — the model this replaced.
    const o = overlayUnderTest();
    o.setVisibleLines(2);
    o.setCharBudget(40);

    let prev: string[] = [];
    for (let i = 0; i < 12; i++) {
      o.send(final(LONG));
      for (let t = 0; t < ARRIVAL_MS; t += 250) {
        o.advance(250);
        const now = o.lines();
        if (now.join("|") !== prev.join("|") && prev.length > 0 && now.length > 0) {
          // Whatever was at the bottom before is still on screen after.
          const carried = now.indexOf(prev[prev.length - 1]) >= 0;
          expect(carried, `screen jumped: ${prev.join(" | ")} -> ${now.join(" | ")}`).toBe(true);
        }
        prev = now;
      }
    }
  });
});

describe("only whole lines ever go up", () => {
  // 🔴 The central rule of this display: the sabha reads whole lines
  // appearing, never words assembling themselves in front of them. The owner,
  // on the block model that preceded it: "no loading inside the subs on
  // screen".
  it("never puts a part-built line on the screen", () => {
    const o = overlayUnderTest();
    o.setCharBudget(30);

    // Released by the server's max-wait ceiling, mid-sentence: no full stop.
    o.send(final("Bhagvan Swaminarayan wrote the Shikshapatri with his"));
    o.advance(500);

    // Everything on screen is a line the splitter called complete; the tail is
    // still building and has not been shown.
    expect(o.building()).not.toBe("");
    expect(o.onScreen()).not.toContain(o.building());
  });

  it("never mutates a line that is already on screen", () => {
    const o = overlayUnderTest();
    o.setCharBudget(30);
    const seen: string[][] = [];

    for (let i = 0; i < 30; i++) {
      o.send(final(`${LONG} ${i}.`));
      for (let t = 0; t < ARRIVAL_MS; t += 250) {
        o.advance(250);
        seen.push(o.lines());
      }
    }

    for (let i = 1; i < seen.length; i++) {
      for (const line of seen[i]) {
        const grewFrom = seen[i - 1].find((p) => p !== line && line.startsWith(p) && p !== "");
        expect(grewFrom, `line grew in place: "${grewFrom}" -> "${line}"`).toBe(undefined);
      }
    }
  });

  it("continues the line a caption left half-finished with the next caption", () => {
    // A sentence boundary is not a screen boundary. The server releases text
    // on a max-wait ceiling as well as on a full stop, and text released that
    // way is the middle of a sentence.
    const o = overlayUnderTest();
    o.setCharBudget(60);

    o.send(final("Bhagvan Swaminarayan wrote"));      // no terminal punctuation
    o.send(final("the Shikshapatri with his own hand."));
    o.advance(10_000);

    expect(o.shown().join(" ")).toContain(
      "Bhagvan Swaminarayan wrote the Shikshapatri with his own hand.",
    );
  });

  it("pushes a short last line up rather than holding it back", () => {
    // The owner's decision, 2026-09-03: "Push the short 1 up". A line that
    // ends mid-width because the sentence ended is complete.
    const o = overlayUnderTest();
    o.setCharBudget(60);

    o.send(final("Jay Swaminarayan."));
    o.advance(500);

    expect(o.onScreen()).toBe("Jay Swaminarayan.");
    expect(o.building()).toBe("");
  });

  it("releases a line the speaker never finished, rather than swallowing it", () => {
    // Without this the display has the same shape of fault as the old silence
    // clear: a rule that could only fire once the thing it waited for had
    // already happened. The last thing said would never reach the sabha.
    const o = overlayUnderTest();
    o.setCharBudget(60);

    o.send(final("and then the swami said"));   // released mid-sentence
    expect(o.onScreen()).toBe("");

    o.advance(LINE_ORPHAN_MS + 500);

    expect(o.onScreen()).toContain("and then the swami said");
    expect(o.building()).toBe("");
  });
});

describe("a long sentence becomes several lines, in order", () => {
  // 🔴 The fault that shipped. A long sentence had its FRONT cut off and the
  // hall read it starting halfway through. Every test passed while it was
  // happening, because they all tested the logic and none looked at a screen.
  it("shows the beginning of the sentence first, not the end", () => {
    const o = overlayUnderTest();
    o.setCharBudget(50);

    o.send(final(LONG));
    o.advance(500);

    expect(o.lines()[0]).toBe(LONG.slice(0, o.lines()[0].length));
  });

  it("shows every word, across successive lines", () => {
    const o = overlayUnderTest();
    o.setCharBudget(50);

    o.send(final(LONG));
    o.advance(20_000);

    expect(o.shown().join(" ")).toBe(LONG);
  });

  it("keeps every line inside the width it was given", () => {
    const o = overlayUnderTest();
    o.setCharBudget(50);

    o.send(final(LONG));
    for (let t = 0; t < 20_000; t += 250) {
      o.advance(250);
      for (const line of o.lines()) expect(line.length).toBeLessThanOrEqual(50);
    }
  });

  it("advances the lines of one sentence on the dwell, not the orphan timeout", () => {
    // The lines of a sentence ARE each other's successors; there is nothing to
    // wait for.
    const o = overlayUnderTest();
    o.setCharBudget(50);

    o.send(final(LONG));
    o.advance(LINE_MIN_DWELL_MS + 500);

    expect(o.shown().length).toBeGreaterThanOrEqual(2);
  });
});

describe("the queue is bounded — it drops, it does not grow", () => {
  // ⚠️ A buffer allowed to grow is the Day 2 Morning fault rebuilt
  // deliberately: 45-60s behind the speaker and getting worse, which read
  // exactly like lag and was not lag.
  it("never holds more than the bound", () => {
    const o = overlayUnderTest();

    for (let i = 0; i < 50; i++) o.send(final(`sentence ${i}.`));

    expect(o.queued().length).toBeLessThanOrEqual(MAX_QUEUED_LINES);
  });

  it("counts every line the hall never saw", () => {
    const o = overlayUnderTest();

    for (let i = 0; i < 10; i++) o.send(final(`sentence ${i}.`));

    expect(o.dropped()).toBe(10 - MAX_QUEUED_LINES);
  });

  it("counts nothing when the display keeps up", () => {
    const o = overlayUnderTest();

    for (let i = 0; i < 20; i++) {
      o.send(final(`sentence ${i}.`));
      o.advance(ARRIVAL_MS);
    }

    expect(o.dropped()).toBe(0);
  });

  it("keeps the NEWEST speech when it has to choose", () => {
    // The sabha is better served by what is being said now than by catching up
    // on what was said six seconds ago.
    const o = overlayUnderTest();

    for (let i = 0; i < 10; i++) o.send(final(`sentence ${i}.`));
    o.advance(20_000);

    expect(o.shown()).toContain("sentence 9.");
    expect(o.shown()).not.toContain("sentence 0.");
  });

  it("does not let lateness grow over a long fast run", () => {
    // The property the whole design exists for.
    const o = overlayUnderTest();
    let worstLag = 0;

    for (let i = 0; i < 400; i++) {
      o.send(final(`${i}.`));
      o.advance(1000);
      const newest = o.lines()[o.lines().length - 1];
      if (newest) worstLag = Math.max(worstLag, i - Number(newest.replace(".", "")));
    }

    expect(worstLag).toBeLessThanOrEqual(MAX_QUEUED_LINES + 1);
  });

  it("keeps dropped lines in the session record even though the screen skipped them", () => {
    const o = overlayUnderTest();

    for (let i = 0; i < 10; i++) o.send(final(`sentence ${i}.`));

    expect(o.dropped()).toBeGreaterThan(0);
    expect(o.store.getState().transcript.map((t) => t.text)).toContain("sentence 0.");
  });
});

describe("a sentence is dropped whole, never beheaded", () => {
  // 🔴 The rule that stops the bound recreating the worst fault this project
  // has shipped. A caption is up to 180 characters and a line holds about
  // fifty, so trimming the front of the line queue would put a sentence on the
  // hall screen starting from its second line — fluent, confident, and
  // beginning in the middle, with nothing to tell the sabha so.
  it("never starts a sentence from its middle", () => {
    const o = overlayUnderTest();
    o.setCharBudget(40);

    // Four long sentences at once: far past the bound.
    for (let i = 0; i < 4; i++) o.send(final(LONG));
    const seen = watch(o, 30_000);

    // Every line that reached the screen either starts the sentence or follows
    // a line that was itself shown.
    const shown = o.shown();
    for (let i = 0; i < shown.length; i++) {
      const startsASentence = LONG.startsWith(shown[i]);
      const followsOne = i > 0 && LONG.includes(`${shown[i - 1]} ${shown[i]}`);
      expect(startsASentence || followsOne,
        `line appeared with no line before it: "${shown[i]}"`).toBe(true);
    }
    expect(seen.length).toBeGreaterThan(1);
  });

  it("finishes a sentence it has started, even when the swami has moved on", () => {
    // Abandoning the remainder of a sentence already on screen makes the hall
    // read a sentence that stops halfway — the fault this display exists to
    // prevent, arrived at from the other direction.
    const o = overlayUnderTest();
    o.setCharBudget(40);

    o.send(final(LONG));
    o.advance(LINE_MIN_DWELL_MS + 250);   // its first line is up
    for (let i = 0; i < 6; i++) o.send(final(`interrupting ${i}.`));
    o.advance(30_000);

    const shown = o.shown().join(" ");
    expect(shown).toContain(LONG);
  });
});

describe("the silence clear", () => {
  // Text left up after the speech has stopped is not a caption any more; it is
  // a mistake the audience is still reading — through the bhajan, the
  // announcements and everything after.
  it("takes the screen down after the speaker stops", () => {
    const o = overlayUnderTest();

    o.send(final("Jay Swaminarayan."));
    o.advance(1000);
    expect(o.onScreen()).not.toBe("");

    o.advance(CAPTION_CLEAR_AFTER_MS + 1000);

    expect(o.onScreen()).toBe("");
  });

  it("leaves the screen up through an ordinary pause for breath", () => {
    const o = overlayUnderTest();

    o.send(final("Jay Swaminarayan."));
    o.advance(CAPTION_CLEAR_AFTER_MS - 2000);

    expect(o.onScreen()).toBe("Jay Swaminarayan.");
  });

  it("empties the queue and the line under construction too", () => {
    // A line held back belongs to speech that has now finished. Scrolling it on
    // after a clear would put a stale sentence back on the hall screen by
    // itself, with nobody speaking.
    const o = overlayUnderTest();
    o.setCharBudget(40);

    o.send(final(`${LONG} and more words still building`));
    o.advance(CAPTION_CLEAR_AFTER_MS + 5000);

    expect(o.onScreen()).toBe("");
    expect(o.queued()).toEqual([]);
    expect(o.building()).toBe("");
  });

  it("measures the silence from the last ARRIVAL, not from the last line shown", () => {
    // A long sentence still scrolling is not silence.
    const o = overlayUnderTest();
    o.setCharBudget(40);

    o.send(final(LONG));
    o.advance(CAPTION_CLEAR_AFTER_MS - 1000);

    expect(o.shown().join(" ")).toBe(LONG);
  });
});

describe("blanking on a broken link", () => {
  // A frozen line is worse than an empty one: it reads as though the tool is
  // working when it is not, and nobody in the hall can tell.
  it("blanks the screen when the server sends `clear`", () => {
    const o = overlayUnderTest();

    o.send(final("one."));
    o.advance(LINE_MIN_DWELL_MS + 500);
    o.send(final("two."));
    expect(o.onScreen()).not.toBe("");

    o.send({ type: "clear" });

    expect(o.onScreen()).toBe("");
    expect(o.held()).toBe(null);
  });

  it("marks the session stopped", () => {
    const o = overlayUnderTest();

    o.send(final("Jay Swaminarayan."));
    o.send({ type: "stopped" });

    expect(o.store.getState().running).toBe(false);
  });
});

describe("the transcript", () => {
  // The session record is the evidence the Bolton machine reads back after a
  // katha, and it is how a wrong caption gets found at all. The display
  // dropping a line must never drop it from the record.
  it("keeps every caption, including ones the screen never showed", () => {
    const o = overlayUnderTest();

    for (let i = 0; i < 10; i++) o.send(final(`sentence ${i}.`));

    expect(o.dropped()).toBeGreaterThan(0);
    expect(o.store.getState().transcript.length).toBe(10);
  });

  it("keeps the Gujarati alongside the English", () => {
    // Recording both is what caught the worst finding of the day: a confident
    // English sentence built out of five broken Gujarati tokens. Without the
    // source beside it, nobody could have known.
    const o = overlayUnderTest();

    o.send(final("That is the nature of a cursed intellect.", "એ સા લૂણી ની ચ બી"));

    const entry = o.store.getState().transcript[0];
    expect(entry.text).toBe("That is the nature of a cursed intellect.");
    expect(entry.raw).toBe("એ સા લૂણી ની ચ બી");
  });
});

describe("the dwell floor", () => {
  // ⚠️ THE UNIT IS A LINE. The old 2.0s floor was a two-line BLOCK's dwell;
  // applied per line it would make a two-line sentence take 4.0s to display
  // against a 3.83s cadence, and the screen would slide behind by the
  // difference on every sentence — Day 2 Morning rebuilt by arithmetic.
  it("stays below the per-line arrival interval", () => {
    // One caption is commonly two lines, so the interval that feeds the scroll
    // is about half the caption interval.
    expect(LINE_MIN_DWELL_MS).toBeLessThan(ARRIVAL_MS / 2);
  });

  it("stays at or above the broadcast minimum, so a line is never flashed away", () => {
    expect(LINE_MIN_DWELL_MS).toBeGreaterThanOrEqual(1500);
  });

  it("holds a line for its floor before the next may scroll on", () => {
    const o = overlayUnderTest();

    o.send(final("one."));
    o.send(final("two."));
    o.advance(LINE_MIN_DWELL_MS - 500);

    expect(o.lines()).toEqual(["one."]);
    expect(o.held()).toBe("two.");
  });
});

describe("test isolation", () => {
  it("gives each case its own store", () => {
    const a = overlayUnderTest();
    const b = overlayUnderTest();

    a.send(final("only in a."));

    expect(a.held()).toBe("only in a.");
    expect(b.held()).toBe(null);
  });
});

describe("the virtual clock", () => {
  it("never reads the real time", () => {
    const clock = new VirtualClock(0);

    expect(clock.now()).toBe(0);
    clock.advance(5000);
    expect(clock.now()).toBe(5000);
  });
});

describe("the timing knobs are settings, not constants", () => {
  // The overlay is a SEPARATE browser instance from the operator UI, configured
  // entirely through the minted URL. A timing knob that is not a setting cannot
  // be changed on the day without editing code and rebuilding — under a live
  // browser input, which is the blank-screen failure.
  it("honours a dwell floor set by the operator", () => {
    const o = overlayUnderTest();
    o.configure({ lineMinDwellSec: 0.5 });

    o.send(final("one."));
    o.send(final("two."));
    o.advance(750, { minDwellMs: 500 });

    expect(o.lines()).toEqual(["one.", "two."]);
  });

  it("honours a silence clear set by the operator", () => {
    const o = overlayUnderTest();
    o.configure({ captionMaxDwellSec: 3 });

    o.send(final("Jay Swaminarayan."));
    o.advance(4000, { clearAfterMs: 3000 });

    expect(o.onScreen()).toBe("");
  });
});

describe("measuring how fast LINES actually arrive", () => {
  // 🔴 Per LINE, not per caption. It is lines that have to fit through the
  // dwell floor now, and measuring captions would overstate the interval by
  // however many lines they carry — which is the dangerous direction, because
  // it makes an unsafe floor look safe.
  it("records the gap between arrivals", () => {
    const o = overlayUnderTest();

    o.send(final("a."));
    o.advance(4000);
    o.send(final("b."));
    o.advance(4000);
    o.send(final("c."));

    expect(o.gaps()).toEqual([4000, 4000]);
  });

  it("divides a caption's gap across the lines it produced", () => {
    const o = overlayUnderTest();
    o.setCharBudget(40);

    o.send(final("first."));
    o.advance(4000);
    o.send(final(LONG));

    // LONG is several lines, so each one accounts for a fraction of the gap.
    const gaps = o.gaps();
    expect(gaps[gaps.length - 1]).toBeLessThan(4000);
  });

  it("ignores a long gap even when the clock never ran to clear the caption", () => {
    // Browsers throttle setInterval in a backgrounded tab to about once a
    // minute, so the operator minimising the window gives arrivals with no
    // ticks between them. The guard on the gap has to do the work itself —
    // it cannot lean on the silence clear having reset the timestamp.
    const o = overlayUnderTest();

    o.send(final("a."));
    o.skip(4000);
    o.send(final("b."));
    o.skip(CAPTION_CLEAR_AFTER_MS + 30_000);   // no tick fires in here
    o.send(final("c."));

    expect(o.gaps()).toEqual([4000]);
  });

  it("ignores gaps that are pauses between passages, not the speaker's cadence", () => {
    const o = overlayUnderTest();

    o.send(final("a."));
    o.advance(4000);
    o.send(final("b."));
    o.advance(CAPTION_CLEAR_AFTER_MS + 30_000);   // the swami pauses
    o.send(final("c."));

    expect(o.gaps()).toEqual([4000]);
  });

  it("keeps a bounded window rather than growing all katha", () => {
    const o = overlayUnderTest();

    for (let i = 0; i < 500; i++) {
      o.send(final(`${i}.`));
      o.advance(4000);
    }

    expect(o.gaps().length).toBeLessThanOrEqual(20);
  });
});

describe("how a caption is cut into lines is injected", () => {
  // Character count is not a reliable unit for how much space text takes.
  // "Bhagvan Swaminarayan wrote the Shikshapatri" is far wider than the same
  // number of characters of short common words — measured on a real screen, a
  // 105-character block budgeted as "two lines" wrapped to three and had its
  // front cut off. The only correct answer lays the text out, which needs a
  // DOM the store has not got.
  it("uses the splitter the renderer installs", () => {
    const o = overlayUnderTest();
    o.store.getState().setLineSplitter(() => ["measured one", "measured two"]);

    o.send(final("anything at all."));
    o.advance(250);

    expect(o.lines()).toEqual(["measured one"]);
    expect(o.held()).toBe("measured two");
  });

  it("falls back to the character budget when no splitter is installed", () => {
    const o = overlayUnderTest();
    o.store.getState().setLineSplitter(null);
    o.setCharBudget(20);

    o.send(final("one two three four five six seven eight nine ten."));

    for (const line of o.queued()) expect(line.length).toBeLessThanOrEqual(20);
  });

  it("never loses a word, whichever splitter is in use", () => {
    const o = overlayUnderTest();
    o.setCharBudget(25);
    const text = "A cursed intellect turns even good counsel into a grievance.";

    o.send(final(text));
    o.advance(20_000);

    expect(o.shown().join(" ")).toBe(text);
  });
});

describe("a record of the lines actually displayed", () => {
  // 🔴 The status board reports every 5s and lines change every second or two,
  // so it cannot answer the one question the mandir PC has to answer after a
  // deploy: "did the whole sentence appear, in order?" A snapshot taken slower
  // than the thing it watches will always miss some of it — and I only noticed
  // because my own verification using it produced nonsense.
  //
  // This is the sequence, not the snapshot.
  it("records each line as it goes up, in order", () => {
    const o = overlayUnderTest();

    o.send(final("first."));
    o.advance(LINE_MIN_DWELL_MS + 500);
    o.send(final("second."));
    o.advance(LINE_MIN_DWELL_MS + 500);

    expect(o.shown()).toEqual(["first.", "second."]);
  });

  it("lets a whole split sentence be reassembled from what was shown", () => {
    const o = overlayUnderTest();
    o.setCharBudget(50);

    o.send(final(LONG));
    o.advance(20_000);

    expect(o.shown().join(" ")).toBe(LONG);
  });

  it("stays bounded across a katha-length run", () => {
    const o = overlayUnderTest();

    for (let i = 0; i < 500; i++) {
      o.send(final(`${i}.`));
      o.advance(4000);
    }

    expect(o.store.getState().shownLines.length).toBeLessThanOrEqual(12);
  });

  it("does not record a blanking as a line", () => {
    const o = overlayUnderTest();

    o.send(final("Jay Swaminarayan."));
    o.advance(LINE_MIN_DWELL_MS + 500);
    o.send({ type: "clear" });

    expect(o.shown()).toEqual(["Jay Swaminarayan."]);
  });
});

describe("reassembly across MORE THAN ONE caption", () => {
  // 🔴 The single-caption reassembly tests above prove the splitter loses no
  // words. They cannot prove the part that actually broke on the hall screen:
  // the server releases a caption on its max-wait ceiling MID-SENTENCE, its
  // tail stays in `building`, and the next caption has to continue it. If the
  // join were dropped, doubled or reordered, every single-caption test here
  // would still be green.
  const HEAD = "Bhagvan Swaminarayan wrote the Shikshapatri with his own hand, "
    + "and set down in it the conduct expected of every satsangi";
  const TAIL = "across two hundred and twelve verses.";

  it("shows every word of a sentence delivered in two captions", () => {
    const o = overlayUnderTest();
    o.setCharBudget(50);

    // No terminal punctuation: this is a mid-sentence release, so its last
    // part is NOT complete and must wait for the rest.
    o.send(final(HEAD));
    o.advance(2000);
    o.send(final(TAIL));
    o.advance(30_000);

    expect(o.shown().join(" ")).toBe(`${HEAD} ${TAIL}`);
    expect(o.building()).toBe("");
  });

  it("holds the join back while the rest may still be coming", () => {
    const o = overlayUnderTest();
    o.setCharBudget(50);

    o.send(final(HEAD));
    // Inside the orphan window: the remainder is a fragment of a sentence
    // that is still being spoken, and putting it up would make the next
    // caption read as a new thought.
    o.advance(LINE_ORPHAN_MS - 500);

    const so_far = o.shown().join(" ");
    expect(HEAD.startsWith(so_far)).toBe(true);
    expect(o.building().length).toBeGreaterThan(0);
    // Nothing is lost, nothing is duplicated — shown plus building IS the
    // caption, exactly.
    expect(`${so_far} ${o.building()}`.trim()).toBe(HEAD);
  });

  it("releases the remainder rather than swallowing it when nothing follows", () => {
    const o = overlayUnderTest();
    o.setCharBudget(50);

    o.send(final(HEAD));
    // Past the orphan window with no continuation: the speaker stopped
    // mid-sentence, or the server did. The hall must still read the words.
    o.advance(LINE_ORPHAN_MS + 20_000);

    expect(o.shown().join(" ")).toBe(HEAD);
    expect(o.building()).toBe("");
  });

  it("reassembles three captions where only the last ends the sentence", () => {
    const o = overlayUnderTest();
    o.setCharBudget(50);

    const parts = [
      "Bhagvan Swaminarayan wrote the Shikshapatri",
      "with his own hand and set down in it the conduct",
      "expected of every satsangi.",
    ];
    for (const p of parts) {
      o.send(final(p));
      o.advance(2000);
    }
    o.advance(30_000);

    expect(o.shown().join(" ")).toBe(parts.join(" "));
  });

  it("starts a fresh sentence rather than joining onto a finished one", () => {
    const o = overlayUnderTest();
    o.setCharBudget(50);

    o.send(final("Jay Swaminarayan."));
    o.advance(2000);
    o.send(final("Bhagvan is here."));
    o.advance(30_000);

    // Two finished captions: neither leaves a remainder, so neither can be
    // glued to the other, and both appear whole.
    expect(o.shown()).toEqual(["Jay Swaminarayan.", "Bhagvan is here."]);
    expect(o.building()).toBe("");
  });
});

describe("a katha-length run at the cadence Day 3 actually measured", () => {
  // 🔴 This is not a replay. The Day 3 recording lives on the mandir PC and
  // the audio path is untouched by the scroll, so what CAN be proven here is
  // the display against the numbers that run measured: 690 finals, a median
  // caption arrival gap of 3.83s, and — the figure that matters — about
  // 1.9s per LINE, i.e. roughly two lines to a caption. A soak at made-up
  // timings proves nothing about a hall.
  //
  // Two lines every 3.83s against a 1.5s line dwell is 2.55 lines of capacity
  // for 2 lines of speech. The headroom is thin, and these tests are what
  // says so out loud.
  const GAP_MS = 3830;

  /** A caption of the measured shape — two lines at a 51-character budget.
   *  The number appears in BOTH lines so no two lines of the katha are the
   *  same string; a test that counted distinct lines would otherwise be
   *  measuring the fixture rather than the screen. */
  function caption(n: number): string {
    return `In the ${n}th verse Bhagvan said a satsangi rises `
      + `before dawn, as verse ${n} sets down.`;
  }

  /** A caption at the SERVER'S CEILING — four lines. Deliberately the worst. */
  function longest(n: number): string {
    const body = `Bhagvan Swaminarayan said in the ${n}th verse that the satsangi `
      + "should rise before dawn and keep the conduct set down for him, "
      + "and should never abandon it whatever the circumstance may be.";
    return `${body.slice(0, 179)}.`;
  }

  it("never blanks the screen while speech is still arriving", () => {
    const o = overlayUnderTest();
    o.setCharBudget(51);          // measured: 1280px area at 56px
    o.setVisibleLines(2);

    o.send(final(caption(0)));
    o.advance(GAP_MS);

    let blanks = 0;
    for (let i = 1; i < 200; i++) {
      o.send(final(caption(i)));
      // Sample right across the gap, not only at its end — a screen that
      // empties for a second between captions is the fault this replaces.
      for (let t = 0; t < GAP_MS; t += 250) {
        o.advance(250);
        if (o.lines().length === 0) blanks++;
      }
    }

    expect(blanks).toBe(0);
  });

  it("gets through a whole katha at the measured shape without dropping", () => {
    const o = overlayUnderTest();
    o.setCharBudget(51);
    o.setVisibleLines(2);

    // 690 finals is the whole of Day 3.
    for (let i = 0; i < 690; i++) {
      o.send(final(caption(i)));
      o.advance(GAP_MS);
    }

    // Day 3 dropped 24 of 690 under the block model. At the measured shape
    // the scroll has capacity to spare, so the bar here is ZERO — and the
    // backlog must be flat at the end, not merely bounded, because a queue
    // that grew would drop more the longer the katha ran.
    expect(o.dropped()).toBe(0);
    expect(o.queued().length).toBe(0);
    expect(o.building()).toBe("");
  });

  it("sheds WHOLE captions, never part of one, when speech outruns the screen", () => {
    const o = overlayUnderTest();
    o.setCharBudget(51);
    o.setVisibleLines(2);

    // ⚠️ The documented limit. Captions at the server's 180-character ceiling
    // are four lines each, arriving every 3.83s against 2.55 lines of
    // capacity — the display CANNOT keep up, and no amount of queueing makes
    // it. What is being asserted is how it fails: whole sentences go missing
    // and the ones that survive are read from their first word.
    const sent = [];
    for (let i = 0; i < 120; i++) {
      const text = longest(i);
      sent.push(text);
      o.send(final(text));
      o.advance(GAP_MS);
    }

    expect(o.dropped()).toBeGreaterThan(0);
    expect(o.queued().length).toBeLessThanOrEqual(MAX_QUEUED_LINES);

    // Every line the hall read is the start of some caption, or a
    // continuation of the one directly before it — never a sentence
    // beginning halfway through with nothing saying so.
    const shown = o.shown();
    for (let i = 0; i < shown.length; i++) {
      const line = shown[i];
      const startsACaption = sent.some((c) => c.startsWith(line));
      const followsThePrevious = i > 0 &&
        sent.some((c) => c.includes(`${shown[i - 1]} ${line}`));
      expect(startsACaption || followsThePrevious).toBe(true);
    }
  });

  it("keeps the screen moving rather than parking on one line", () => {
    const o = overlayUnderTest();
    o.setCharBudget(51);
    o.setVisibleLines(2);

    const seen = new Set<string>();
    for (let i = 0; i < 100; i++) {
      o.send(final(caption(i)));
      for (let t = 0; t < GAP_MS; t += 250) {
        o.advance(250);
        const bottom = o.lines()[o.lines().length - 1];
        if (bottom) seen.add(bottom);
      }
    }

    // Two lines a caption, 100 captions: if the screen were parking on one
    // line the count of distinct bottom lines would be a fraction of that.
    expect(seen.size).toBeGreaterThan(150);
  });
});
