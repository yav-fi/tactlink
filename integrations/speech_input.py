"""Local push-to-talk transcription; no audio is sent to a cloud service."""

import io
import re
import threading
import wave
from pathlib import Path

SMALL = dict(zip('zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen'.split(), range(20)))
TENS = dict(zip('twenty thirty forty fifty sixty seventy eighty ninety'.split(), range(20, 100, 10)))
WORDS = '|'.join([*SMALL, *TENS, 'hundred', 'thousand'])


def normalize_instruction(text):
    """Conservative English number normalization, with original transcript retained."""
    text = text.strip().lower().replace('counter clockwise', 'counterclockwise')
    def number(match):
        raw = match.group(0)
        suffix = ' and' if raw.endswith(' and') else ''
        tokens = raw.removesuffix(suffix).replace('-', ' ').split()
        sign = -1 if tokens[0] == 'minus' else 1
        if sign == -1:
            tokens = tokens[1:]
        total = current = 0
        decimals = []
        fractional = False
        previous = None
        for token in tokens:
            if token == 'point':
                if fractional:
                    return raw
                fractional = True
            elif fractional:
                if token not in SMALL or SMALL[token] > 9:
                    return raw  # Do not guess malformed decimal numbers.
                decimals.append(str(SMALL[token]))
            elif token in SMALL:
                if previous in SMALL or (previous in TENS and not 0 < SMALL[token] < 10):
                    return raw
                current += SMALL[token]
            elif token in TENS:
                if previous in SMALL or previous in TENS:
                    return raw
                current += TENS[token]
            elif token == 'hundred':
                current = (current or 1) * 100
            elif token == 'thousand':
                total += (current or 1) * 1000
                current = 0
            elif token == 'and' and previous not in ('hundred', 'thousand'):
                return raw
            previous = token
        if fractional and not decimals:
            return raw
        value = ('-' if sign == -1 else '') + str(total + current)
        return value + ('.' + ''.join(decimals) if decimals else '') + suffix
    text = re.sub(rf'\b(?:minus )?(?:{WORDS})(?:(?:[ -])(?:{WORDS}|and|point))*\b', number, text)
    # ASR often ends each instruction with a sentence boundary.
    return re.sub(r'\.\s+(?=[a-z])', ', ', text).rstrip('.')


class LocalTranscriber:
    def __init__(self):
        self.model = None
        self.lock = threading.Lock()
        self.error = None
        self.cache = Path(__file__).resolve().parents[1] / 'models' / 'speech'

    def status(self):
        import importlib.util
        return dict(available=importlib.util.find_spec('faster_whisper') is not None,
                    loaded=self.model is not None, busy=self.lock.locked(),
                    model='base.en', processing='local CPU', error=self.error)

    def transcribe(self, data):
        if not self.lock.acquire(blocking=False):
            raise RuntimeError('Transcription is busy. Try again after it finishes.')
        try:
            import numpy as np
            from faster_whisper import WhisperModel
            try:
                with wave.open(io.BytesIO(data), 'rb') as audio:
                    frames, rate = audio.getnframes(), audio.getframerate()
                    if audio.getnchannels() != 1 or audio.getsampwidth() != 2 or rate != 16000:
                        raise ValueError('Use mono 16-bit PCM WAV at 16 kHz.')
                    if not 0.25 <= frames / rate <= 30:
                        raise ValueError('Record between 0.25 and 30 seconds.')
                    raw = audio.readframes(frames)
                    if len(raw) != frames * 2:
                        raise ValueError('The audio file is incomplete.')
            except (wave.Error, EOFError) as error:
                raise ValueError('Invalid WAV recording.') from error
            samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768
            if float(np.max(np.abs(samples))) < 0.002:
                raise ValueError('No audible speech detected. Check your microphone.')
            if self.model is None:
                self.model = WhisperModel('base.en', device='cpu', compute_type='int8',
                                          cpu_threads=4, download_root=str(self.cache))
            segments, _ = self.model.transcribe(samples, language='en', beam_size=3, vad_filter=True,
                                               condition_on_previous_text=False)
            text = ' '.join(segment.text.strip() for segment in segments).strip()
            if not text:
                raise ValueError('No speech detected. Try again closer to the microphone.')
            self.error = None
            return dict(transcript=text, normalized_text=normalize_instruction(text))
        except ImportError as error:
            self.error = 'Install requirements-speech.txt to enable local speech recognition.'
            raise RuntimeError(self.error) from error
        except Exception as error:
            self.error = str(error)
            raise
        finally:
            self.lock.release()
