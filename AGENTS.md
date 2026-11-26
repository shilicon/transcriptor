# Project Rules

## Transcript paragraph invariant

The transcript paragraph length is a product requirement, not a cosmetic detail.

- Target about **10 sentences per paragraph**.
- Prefer **AI semantic segmentation** for long same-speaker turns: use
  `app/segmentation.py` so breaks follow topics and arguments, not a rigid
  sentence count.
- AI plans must never cross speakers and must stay under the 12-sentence
  safety ceiling.
- If AI segmentation is unavailable or invalid, fall back to deterministic
  `build_paragraphs()` without failing transcription.
- Never merge a whole same-speaker turn into one giant paragraph.
- Paragraphs never mix speakers.
- Whisper segments that end mid-sentence must be passed through
  `split_segments_at_sentences()` before `build_paragraphs()`.
- Keep `TARGET_SENTENCES_PER_PARAGRAPH = 10` in `app/formatting.py`.
- Any change to segmentation must keep `tests/test_formatting.py`,
  `tests/test_segmentation.py`, and the pipeline regression tests passing.

See the “逐字稿分段原则” section in `README.md` for user-facing documentation.
