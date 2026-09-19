"""Run the translator benchmark. See benchmark_translators.py for the method.

    uv run python tools/run_benchmark.py --capture <segments.json> \
        --reference <published-english.txt> [--models gemini-3.1-flash-lite]

`--capture` is a JSON list of {"t": <seconds>, "gu": <gujarati>} objects, as
recorded by a session with SARVAM_RECORD_SOURCE=on.
"""
import argparse, asyncio, json, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import aiohttp
import live_captions as lc
from tools.benchmark_translators import (TermCheck, assemble, comparable_subset,
                                         coverage_warning, similarity,
                                         terminology_score)

# What the published translation calls the terms this passage turns on.
# Deliberately a short, checkable list rather than the whole glossary: these
# are the ones a reader would notice going wrong.
TERMS = [
    TermCheck("શાપિત", ("cursed",)),
    TermCheck("કથા",   ("katha", "discourse")),
    TermCheck("સંત",   ("sant", "saint")),
    TermCheck("ભક્ત",  ("devotee", "bhakta")),
    TermCheck("સત્સંગ", ("satsang",)),
    TermCheck("બુદ્ધિ", ("intellect", "understanding")),
    TermCheck("અવગુણ", ("avgun", "flaw", "fault")),
    TermCheck("ધર્મ",  ("dharma",)),
]


async def translate_all(session, sentences, *, backend, model, brief, glossary,
                        sarvam_key, gemini_key, rpm=0):
    outs, lat = [], []
    context: list[str] = []
    # Stay under the per-minute request limit. The first full run looked like
    # a quality result and was not: half the calls came back 429, because the
    # harness fired faster than the tier allows. Pacing is the difference
    # between a benchmark and a rate-limit test.
    min_gap = 60.0 / rpm if rpm else 0.0
    last = 0.0
    for s in sentences:
        wait = min_gap - (time.time() - last)
        if wait > 0:
            await asyncio.sleep(wait)
        last = time.time()
        t0 = time.time()
        if backend == "sarvam":
            out = await lc._mayura_translate(session, sarvam_key, s,
                    source_lang="gu-IN", target_lang="en-IN", model=None,
                    timeout_sec=20)
        else:
            out = await lc._gemini_translate(session, gemini_key, s,
                    source_lang="gu-IN", target_lang="en-IN", brief=brief,
                    glossary=glossary, context=list(context), model=model,
                    timeout_sec=30)
        lat.append((time.time() - t0) * 1000)
        outs.append(out)
        if out:
            context = (context + [out])[-3:]
    return outs, lat


async def main(argv: "list[str] | None" = None):
    # argv is a parameter so an importer can drive this without
    # sys.argv. The __main__ guard alone only made the module
    # importable; a main() welded to argv is still unusable from a
    # test or a scoring harness, which is the thing this is for.
    ap = argparse.ArgumentParser()
    ap.add_argument("--capture", required=True)
    ap.add_argument("--reference", required=True)
    ap.add_argument("--models", default="gemini-3.1-flash-lite")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--rpm", type=int, default=13,
                    help="cap requests per minute for the model backends. "
                         "The free tier allows 15/min on Flash Lite; going "
                         "over turns the benchmark into a rate-limit test.")
    ap.add_argument("--no-brief", action="store_true",
                    help="run the model with no glossary or passage — the "
                         "control that shows whether the briefing, rather "
                         "than the model, is doing the work")
    args = ap.parse_args(argv)

    segments = json.load(open(args.capture, encoding="utf-8"))
    reference = open(args.reference, encoding="utf-8").read()
    sentences = assemble(segments, quiet_sec=lc.SENTENCE_QUIET_SEC,
                         max_wait_sec=lc.SENTENCE_MAX_WAIT_SEC,
                         max_chars=lc.SENTENCE_MAX_CHARS,
                         min_words=lc.SENTENCE_MIN_WORDS)
    if args.limit:
        sentences = sentences[:args.limit]

    brief, glossary = lc.load_glossary()
    if args.no_brief:
        brief, glossary = "", {}

    print(f"{len(segments)} captured fragments -> {len(sentences)} assembled "
          f"sentences (quiet {lc.SENTENCE_QUIET_SEC}s, wait "
          f"{lc.SENTENCE_MAX_WAIT_SEC}s, floor {lc.SENTENCE_MIN_WORDS} words)")
    print(f"glossary: {len(glossary)} terms · brief: {len(brief)} chars\n")

    runs = [("sarvam", None)] + [("gemini", m) for m in args.models.split(",") if m]
    results = {}
    async with aiohttp.ClientSession() as s:
        for backend, model in runs:
            label = model or "sarvam/mayura"
            outs, lat = await translate_all(
                s, sentences, backend=backend, model=model, brief=brief,
                glossary=glossary,
                sarvam_key=os.environ.get("SARVAM_API_KEY", ""),
                gemini_key=os.environ.get("GEMINI_API_KEY", ""),
                rpm=(args.rpm if backend != "sarvam" else 0))
            term = terminology_score(list(zip(sentences, outs)), TERMS)
            sim = similarity(" ".join(o or "" for o in outs), reference)
            answered = sum(1 for o in outs if o)
            results[label] = (term, sim, lat, outs, answered)
            med = sorted(lat)[len(lat)//2] if lat else 0
            print(f"{label:<26} terms {term.hits:>3}/{term.applicable:<3} "
                  f"({term.ratio:5.0%})   similarity {sim:5.1%}   "
                  f"median {med:5.0f}ms   answered {answered}/{len(sentences)}")
            warn = coverage_warning(answered=answered, total=len(sentences),
                                    label=label)
            if warn:
                print(f"    !! {warn}")

    # If any backend was short, the headline numbers compare two different
    # exams. Re-score on the lines every backend actually answered.
    by_label = {lbl: results[lbl][3] for lbl in results}
    both = comparable_subset(by_label)
    if any(coverage_warning(answered=results[l][4], total=len(sentences), label=l)
           for l in results):
        print(f"\n── like for like, on the {len(both)} sentences every backend "
              f"answered ──")
        for lbl in results:
            outs = [results[lbl][3][i] for i in both]
            subset = [sentences[i] for i in both]
            term = terminology_score(list(zip(subset, outs)), TERMS)
            sim = similarity(" ".join(o or "" for o in outs), reference)
            print(f"{lbl:<26} terms {term.hits:>3}/{term.applicable:<3} "
                  f"({term.ratio:5.0%})   similarity {sim:5.1%}")
        print("    (a small sample — indicative, not a verdict)")

    print("\n── where they differ on a term ──")
    shown = 0
    labels = list(results)
    for i, sent in enumerate(sentences):
        verdicts = {}
        for lbl in labels:
            t = results[lbl][0]
            hits = [m for m in t.misses_and_hits if m.source == sent]
            if hits:
                verdicts[lbl] = all(m.hit for m in hits)
        if len(set(verdicts.values())) > 1 and shown < 12:
            shown += 1
            print(f"\n  {sent[:90]}")
            for lbl in labels:
                mark = "OK " if verdicts.get(lbl) else "-- "
                print(f"    {mark}{lbl:<24} {(results[lbl][3][i] or '(no answer)')[:80]}")
    if not shown:
        print("  (none — they agreed on every scored term)")

if __name__ == "__main__":
    # Without this guard the module cannot be imported without RUNNING, which
    # is why the replay -- the instrument this project trusts over a live
    # katha -- could never be driven by a test or scored automatically.
    # reprocess_vod.py has always had it; these two were missed.
    asyncio.run(main())
