"""The built-in bad-word mask.

A viewer saw a swear word in a translated katha caption (6 Oct). The
operator's rules can be edited or emptied, and a new install starts with none,
so the mask is built in and runs after the operator's rules on every caption.
"""
import pytest

import live_captions as lc


def shown(text, operator_rules=()):
    out, _ = lc.apply_caption_rules(text, list(operator_rules))
    return out


@pytest.mark.parametrize("text, masked", [
    ("That bastard said", "That … said"),
    ("What the FUCK", "What the …"),
    ("motherfucker", "…"),
    ("you are an asshole", "you are an …"),
    ("shit happens", "… happens"),
    ("son of a bitch", "son of a …"),
    ("Bastards!", "…!"),
])
def test_a_bad_word_is_masked_and_the_line_kept(text, masked):
    assert shown(text) == masked


@pytest.mark.parametrize("text", [
    "the class of saints", "an assassin", "assume", "assembly", "passage of the scriptures",
    "Scunthorpe", "Dickens wrote", "the Pakistan border", "shitake", "retardant",
    # A donkey in a katha story can come out as "ass"; only the compounds are masked.
    "the ass carried the load",
])
def test_ordinary_words_that_contain_one_are_left_alone(text):
    assert shown(text) == text


def test_it_runs_with_no_operator_rules_and_after_theirs():
    r = lc.Rule(id="mine", pattern="villain", replacement="bastard")
    r.compile_()
    assert shown("the villain", [r]) == "the …"
