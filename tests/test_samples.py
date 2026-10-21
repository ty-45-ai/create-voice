import array
import math
import wave

from app.engines.samples import build_samples, cut_long, read_clip, split_sentences

RATE = 16000


def tone(seconds):
    return [int(8000 * math.sin(2 * math.pi * 220 * i / RATE)) for i in range(int(seconds * RATE))]


def silence(seconds):
    return [0] * int(seconds * RATE)


def write(path, samples):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(array.array("h", samples).tobytes())
    return path


def test_split_sentences():
    assert split_sentences("I like books. Do you?\nYes!") == ["I like books.", "Do you?", "Yes!"]
    assert split_sentences("きょうは はれ。あしたは あめ。") == ["きょうは はれ。", "あしたは あめ。"]


def test_long_passage_is_cut_at_a_sentence_pause(tmp_path):
    # Three sentences of similar length, ~12 s each, with pauses between: 37 s in all.
    text = "This is the first long sentence of the passage. This is the second long sentence of it. " \
           "And this is the third long sentence here."
    audio = tone(12) + silence(0.5) + tone(12) + silence(0.5) + tone(12)
    clip = read_clip(write(tmp_path / "long.wav", audio), text)
    cut = cut_long(clip)
    assert cut is not None
    assert 23.5 <= cut.seconds <= 25.0  # cut in the second pause
    assert cut.text == "This is the first long sentence of the passage. This is the second long sentence of it."


def test_build_samples_joins_short_and_cuts_long(tmp_path):
    short1 = write(tmp_path / "1.wav", tone(1.5))
    short2 = write(tmp_path / "2.wav", tone(1.8))
    normal = write(tmp_path / "3.wav", tone(6))
    long = write(tmp_path / "4.wav", tone(16) + silence(0.6) + tone(12) + silence(0.6) + tone(3))
    samples = build_samples([
        (short1, "Hi."), (short2, "I am Ken."), (normal, "I like reading books very much."),
        (long, "First part of a passage that is long. Second part of the same passage. End."),
    ], max_total=999)
    assert [s.text for s in samples] == [
        "Hi. I am Ken.",                          # two short readings joined
        "I like reading books very much.",        # fine as it is
        "First part of a passage that is long.",  # long passage cut after its first sentence
    ]
    assert all(3.0 <= s.seconds <= 25.0 for s in samples)


def test_build_samples_skips_unreadable_and_too_short(tmp_path):
    lonely = write(tmp_path / "1.wav", tone(1.0))
    junk = tmp_path / "2.wav"
    junk.write_bytes(b"nope")
    assert build_samples([(lonely, "Hi."), (junk, "x")]) == []
