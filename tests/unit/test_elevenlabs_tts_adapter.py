import unittest

from elevenlabs.errors.bad_request_error import BadRequestError
from elevenlabs.errors.forbidden_error import ForbiddenError
from elevenlabs.errors.not_found_error import NotFoundError
from elevenlabs.errors.unauthorized_error import UnauthorizedError
from elevenlabs.errors.unprocessable_entity_error import UnprocessableEntityError

from app.exceptions import (
    TTSAuthenticationError,
    TTSConfigurationError,
    TTSProviderUnavailableError,
    TTSSynthesisError,
)
from app.tts.adapters.elevenlabs_tts import ElevenLabsTextToSpeechSynthesizer
from app.tts.config import TextToSpeechSettings
from app.tts.gateway import SpeechSynthesis


class FakeTextToSpeechEndpoint:
    def __init__(self, chunks=None, failure=None):
        self.chunks = chunks
        self.failure = failure
        self.calls = []

    def convert(self, voice_id, *, text, output_format, voice_settings, **kwargs):
        self.calls.append((voice_id, text, output_format, voice_settings, kwargs))
        if self.failure is not None:
            raise self.failure
        return iter(self.chunks)


class FakeElevenLabsClient:
    def __init__(self, chunks=None, failure=None):
        self.text_to_speech = FakeTextToSpeechEndpoint(chunks=chunks, failure=failure)


def _settings(**overrides):
    values = {
        "provider": "elevenlabs",
        "voice_name": "JBFqnCBsd6RMkjVDRZzb",
        "language_code": "en-GB",
        "audio_encoding": "MP3",
        "speaking_rate": 1.0,
    }
    values.update(overrides)
    return TextToSpeechSettings(**values)


class ElevenLabsTextToSpeechSynthesizerTests(unittest.TestCase):
    def test_synthesizes_speech_and_wraps_result(self):
        client = FakeElevenLabsClient(chunks=[b"real-", b"audio-bytes"])
        synthesizer = ElevenLabsTextToSpeechSynthesizer(
            _settings(), client_factory=lambda: client
        )

        synthesis = synthesizer.synthesize(text="Analysis complete.")

        self.assertIsInstance(synthesis, SpeechSynthesis)
        self.assertEqual(synthesis.audio, b"real-audio-bytes")
        self.assertEqual(synthesis.provider, "elevenlabs")
        self.assertEqual(synthesis.voice, "JBFqnCBsd6RMkjVDRZzb")
        self.assertEqual(synthesis.audio_encoding, "MP3")
        self.assertEqual(synthesis.media_type, "audio/mpeg")
        self.assertEqual(len(client.text_to_speech.calls), 1)
        voice_id, text, output_format, voice_settings, kwargs = client.text_to_speech.calls[0]
        self.assertEqual(voice_id, "JBFqnCBsd6RMkjVDRZzb")
        self.assertEqual(text, "Analysis complete.")
        self.assertEqual(output_format, "mp3_44100_128")
        self.assertEqual(voice_settings.speed, 1.0)
        self.assertNotIn("model_id", kwargs)

    def test_maps_audio_encoding_to_provider_output_format(self):
        client = FakeElevenLabsClient(chunks=[b"pcm-bytes"])
        synthesizer = ElevenLabsTextToSpeechSynthesizer(
            _settings(audio_encoding="LINEAR16"), client_factory=lambda: client
        )

        synthesis = synthesizer.synthesize(text="Analysis complete.")

        self.assertEqual(synthesis.media_type, "audio/l16")
        _, _, output_format, _, _ = client.text_to_speech.calls[0]
        self.assertEqual(output_format, "pcm_44100")

    def test_passes_model_id_when_configured(self):
        client = FakeElevenLabsClient(chunks=[b"audio-bytes"])
        synthesizer = ElevenLabsTextToSpeechSynthesizer(
            _settings(model_id="eleven_v3"), client_factory=lambda: client
        )

        synthesizer.synthesize(text="Analysis complete.")

        _, _, _, _, kwargs = client.text_to_speech.calls[0]
        self.assertEqual(kwargs, {"model_id": "eleven_v3"})

    def test_empty_audio_content_raises_synthesis_error(self):
        client = FakeElevenLabsClient(chunks=[])
        synthesizer = ElevenLabsTextToSpeechSynthesizer(
            _settings(), client_factory=lambda: client
        )

        with self.assertRaises(TTSSynthesisError):
            synthesizer.synthesize(text="Analysis complete.")

    def test_rejects_blank_text(self):
        synthesizer = ElevenLabsTextToSpeechSynthesizer(
            _settings(), client_factory=lambda: FakeElevenLabsClient(chunks=[b"x"])
        )

        with self.assertRaises(ValueError):
            synthesizer.synthesize(text="   ")

    def test_rejects_missing_voice_id_at_construction(self):
        with self.assertRaises(TTSConfigurationError):
            ElevenLabsTextToSpeechSynthesizer(
                _settings(voice_name=None),
                client_factory=lambda: FakeElevenLabsClient(chunks=[b"x"]),
            )

    def test_rejects_unsupported_audio_encoding_at_construction(self):
        with self.assertRaises(TTSConfigurationError):
            ElevenLabsTextToSpeechSynthesizer(
                _settings(audio_encoding="NOT_A_REAL_ENCODING"),
                client_factory=lambda: FakeElevenLabsClient(chunks=[b"x"]),
            )

    def test_translates_unauthorized_failure(self):
        client = FakeElevenLabsClient(
            failure=UnauthorizedError(body="bad api key")
        )
        synthesizer = ElevenLabsTextToSpeechSynthesizer(
            _settings(), client_factory=lambda: client
        )

        with self.assertRaises(TTSAuthenticationError):
            synthesizer.synthesize(text="Analysis complete.")

    def test_translates_forbidden_as_authentication_failure(self):
        client = FakeElevenLabsClient(
            failure=ForbiddenError(body="not allowed")
        )
        synthesizer = ElevenLabsTextToSpeechSynthesizer(
            _settings(), client_factory=lambda: client
        )

        with self.assertRaises(TTSAuthenticationError):
            synthesizer.synthesize(text="Analysis complete.")

    def test_translates_bad_request_as_configuration_failure(self):
        client = FakeElevenLabsClient(
            failure=BadRequestError(body="bad voice settings")
        )
        synthesizer = ElevenLabsTextToSpeechSynthesizer(
            _settings(), client_factory=lambda: client
        )

        with self.assertRaises(TTSConfigurationError):
            synthesizer.synthesize(text="Analysis complete.")

    def test_translates_not_found_as_configuration_failure(self):
        client = FakeElevenLabsClient(
            failure=NotFoundError(body="unknown voice")
        )
        synthesizer = ElevenLabsTextToSpeechSynthesizer(
            _settings(), client_factory=lambda: client
        )

        with self.assertRaises(TTSConfigurationError):
            synthesizer.synthesize(text="Analysis complete.")

    def test_translates_unprocessable_entity_as_configuration_failure(self):
        client = FakeElevenLabsClient(
            failure=UnprocessableEntityError(body="bad payload")
        )
        synthesizer = ElevenLabsTextToSpeechSynthesizer(
            _settings(), client_factory=lambda: client
        )

        with self.assertRaises(TTSConfigurationError):
            synthesizer.synthesize(text="Analysis complete.")

    def test_translates_unknown_failure_as_provider_unavailable(self):
        client = FakeElevenLabsClient(failure=RuntimeError("something odd"))
        synthesizer = ElevenLabsTextToSpeechSynthesizer(
            _settings(), client_factory=lambda: client
        )

        with self.assertRaises(TTSProviderUnavailableError):
            synthesizer.synthesize(text="Analysis complete.")

    def test_configuration_fingerprint_is_stable(self):
        synthesizer = ElevenLabsTextToSpeechSynthesizer(
            _settings(), client_factory=lambda: FakeElevenLabsClient(chunks=[b"x"])
        )

        first = synthesizer.configuration_fingerprint
        second = synthesizer.configuration_fingerprint

        self.assertEqual(first, second)
        self.assertEqual(len(first), 64)

    def test_rejects_invalid_dependencies(self):
        with self.assertRaises(ValueError):
            ElevenLabsTextToSpeechSynthesizer("invalid")
        with self.assertRaises(ValueError):
            ElevenLabsTextToSpeechSynthesizer(_settings(), client_factory="invalid")


if __name__ == "__main__":
    unittest.main()
