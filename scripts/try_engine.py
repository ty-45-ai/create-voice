"""Check that a speech engine is installed and works, without starting the web app.

    python scripts/try_engine.py my_voice.wav "こんにちは。きょうは いい てんき です。"
    python scripts/try_engine.py data/recordings/P001/*.wav "I like reading books."
    python scripts/try_engine.py my_voice.wav "Hello, my name is Ken." --lang en
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import load_settings  # noqa: E402
from app.engines import ENGINE_NAMES, load_engine  # noqa: E402
from app.store import guess_lang  # noqa: E402


def main() -> None:
    settings = load_settings()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("reference", type=Path, nargs="+", help="声のサンプル (WAV。複数可。合計6秒以上推奨)")
    ap.add_argument("text", help="読み上げる文章")
    ap.add_argument("--lang", choices=["ja", "en"], help="省略時は文章から自動判定")
    ap.add_argument("--engine", choices=ENGINE_NAMES, default=settings.engine)
    ap.add_argument("--out", type=Path, default=Path("try_engine_out.wav"))
    ap.add_argument("--ref-text", action="append",
                    help="見本の録音で読んでいる文（Voicebox で必要。見本ごとに指定。省略時は読み上げる文と同じとみなす）")
    ap.add_argument("--expected", type=float,
                    help="この文を読むのにかかる秒数（余分な音声を切る目安）。"
                         "省略時、見本が1ファイルならその長さ（同じ文を読んだ録音とみなす）")
    args = ap.parse_args()

    missing = [str(p) for p in args.reference if not p.is_file()]
    if missing:
        sys.exit(f"声のサンプルが見つかりません: {', '.join(missing)}\n"
                 "アプリで録音した data/recordings/<ID>/<番号>.wav などを指定してください。")

    t0 = time.time()
    engine = load_engine(args.engine, settings.device)
    print(f"engine '{engine.name}' loaded in {time.time() - t0:.1f}s")
    t1 = time.time()
    expected = args.expected
    if expected is None and len(args.reference) == 1:
        import wave

        with wave.open(str(args.reference[0]), "rb") as w:
            expected = w.getnframes() / float(w.getframerate())
    ref_texts = args.ref_text or [args.text] * len(args.reference)
    engine.synthesize(args.text, args.lang or guess_lang(args.text), args.reference, args.out,
                      expected_seconds=expected, reference_texts=ref_texts)
    print(f"wrote {args.out} in {time.time() - t1:.1f}s")


if __name__ == "__main__":
    main()
