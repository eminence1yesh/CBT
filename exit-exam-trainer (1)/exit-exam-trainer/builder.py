"""
builder.py
----------
Generates and caches the four options for every question that does not have
them yet. Runs once after an import; after that, quiz screens read straight
from SQLite and never touch the generator again.

To plug in a local LLM later, pass any object with the same
`build_options(question, answer, topic) -> (options, correct_index)` signature.
"""

from __future__ import annotations

import sys

import database as db
from distractors import DistractorEngine


def build(engine=None, batch: int = 500, verbose: bool = True) -> int:
    """Fill in missing options. Returns how many questions were built."""
    pending = db.questions_missing_options()
    if not pending:
        return 0

    if engine is None:
        # The generator mines the whole bank, not just the pending rows,
        # so distractors can borrow wording from questions already imported.
        corpus = [dict(r) for r in db.all_questions_min()]
        engine = DistractorEngine(corpus)

    done, buf = 0, []
    for row in pending:
        try:
            options, correct = engine.build_options(
                row["question"], row["answer"], row["topic"])
        except Exception as exc:                       # never abort the batch
            print(f"[builder] q{row['id']} failed: {exc}", file=sys.stderr)
            options, correct = [row["answer"]], 0

        if not options:
            options, correct = [row["answer"]], 0
        buf.append((row["id"], options, correct))

        if len(buf) >= batch:
            db.save_options(buf)
            done += len(buf)
            buf.clear()
            if verbose:
                print(f"\r[builder] {done}/{len(pending)} questions prepared",
                      end="", flush=True)

    if buf:
        db.save_options(buf)
        done += len(buf)

    if verbose:
        print(f"\r[builder] {done}/{len(pending)} questions prepared")
    return done


def rebuild_all(verbose: bool = True) -> int:
    """Throw away cached options and regenerate everything."""
    db.clear_options()
    return build(verbose=verbose)


def rebuild_all_with(engine, verbose: bool = True) -> int:
    """
    Regenerate every question's options using a different engine - for example
    a local LLM wrapper. `engine` only needs one method:

        build_options(question, answer, topic) -> (options, correct_index)
    """
    db.clear_options()
    return build(engine=engine, verbose=verbose)
