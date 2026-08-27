import unittest

from google.api_core import exceptions as google_exceptions

from app.exceptions import (
    TTSAuthenticationError,
    TTSConfigurationError,
    TTSProviderUnavailableError,
    TTSSynthesisError,
)
from app.tts.adapters.google_tts import GoogleTextToSpeechSynthesizer
from app.tts.config import TextToSpeechSettings
from app.tts.gateway import SpeechSynthesis


class FakeSynthesizeResponse:
    def __init__(self, audio_content):
        self.audio_content = audio_content


class FakeGoogleClient:
    def __init__(self, response=None, failure=None):
        self.response = response
        self.failure = failure
        self.calls = []

    def synthesize_speech(self, *, input, voice, audio_config):
        self.calls.append((input, voice, audio_config))
        if self.failure is not None:
            raise self.failure
        return self.response


def _settings(**overrides):
    values = {
        "provider": "google",
        "voice_name": "en-GB-Neural2-B",
        "language_code": "en-GB",
        "audio_encoding": "MP3",
        "speaking_rate": 1.0,
    }
    values.update(overrides)
    return TextToSpeechSettings(**values)


class GoogleTextToSpeechSynthesizerTests(unittest.TestCase):
    def test_synthesizes_speech_and_wraps_result(self):
        client = FakeGoogleClient(
            response=FakeSynthesizeResponse(b"real-audio-bytes")
        )
        synthesizer = GoogleTextToSpeechSynthesizer(
            _settings(), client_factory=lambda: client
        )

        synthesis = synthesizer.synthesize(text="Analysis complete.")

        self.assertIsInstance(synthesis, SpeechSynthesis)
        self.assertEqual(synthesis.audio, b"real-audio-bytes")
        self.assertEqual(synthesis.provider, "google")
        self.assertEqual(synthesis.voice, "en-GB-Neural2-B")
        self.assertEqual(synthesis.audio_encoding, "MP3")
        self.assertEqual(synthesis.media_type, "audio/mpeg")
        self.assertEqual(len(client.calls), 1)

    def test_falls_back_to_language_code_when_no_voice_name(self):
        client = FakeGoogleClient(
            response=FakeSynthesizeResponse(b"real-audio-bytes")
        )
        synthesizer = GoogleTextToSpeechSynthesizer(
            _settings(voice_name=None), client_factory=lambda: client
        )

        synthesis = synthesizer.synthesize(text="Analysis complete.")

        self.assertEqual(synthesis.voice, "en-GB")

    def test_empty_audio_content_raises_synthesis_error(self):
        client = FakeGoogleClient(response=FakeSynthesizeResponse(b""))
        synthesizer = GoogleTextToSpeechSynthesizer(
            _settings(), client_factory=lambda: client
        )

        with self.assertRaises(TTSSynthesisError):
            synthesizer.synthesize(text="Analysis complete.")

    def test_rejects_blank_text(self):
        synthesizer = GoogleTextToSpeechSynthesizer(
            _settings(), client_factory=lambda: FakeGoogleClient()
        )

        with self.assertRaises(ValueError):
            synthesizer.synthesize(text="   ")

    def test_rejects_unsupported_audio_encoding_at_construction(self):
        with self.assertRaises(TTSConfigurationError):
            GoogleTextToSpeechSynthesizer(
                _settings(audio_encoding="NOT_A_REAL_ENCODING"),
                client_factory=lambda: FakeGoogleClient(),
            )

    def test_translates_authentication_failure(self):
        client = FakeGoogleClient(
            failure=google_exceptions.Unauthenticated("bad credentials")
        )
        synthesizer = GoogleTextToSpeechSynthesizer(
            _settings(), client_factory=lambda: client
        )

        with self.assertRaises(TTSAuthenticationError):
            synthesizer.synthesize(text="Analysis complete.")

    def test_translates_permission_denied_as_authentication_failure(self):
        client = FakeGoogleClient(
            failure=google_exceptions.PermissionDenied("not allowed")
        )
        synthesizer = GoogleTextToSpeechSynthesizer(
            _settings(), client_factory=lambda: client
        )

        with self.assertRaises(TTSAuthenticationError):
            synthesizer.synthesize(text="Analysis complete.")

    def test_translates_service_unavailable_failure(self):
        client = FakeGoogleClient(
            failure=google_exceptions.ServiceUnavailable("down")
        )
        synthesizer = GoogleTextToSpeechSynthesizer(
            _settings(), client_factory=lambda: client
        )

        with self.assertRaises(TTSProviderUnavailableError):
            synthesizer.synthesize(text="Analysis complete.")

    def test_translates_invalid_argument_as_configuration_failure(self):
        client = FakeGoogleClient(
            failure=google_exceptions.InvalidArgument("bad voice name")
        )
        synthesizer = GoogleTextToSpeechSynthesizer(
            _settings(), client_factory=lambda: client
        )

        with self.assertRaises(TTSConfigurationError):
            synthesizer.synthesize(text="Analysis complete.")

    def test_translates_unknown_failure_as_provider_unavailable(self):
        client = FakeGoogleClient(failure=RuntimeError("something odd"))
        synthesizer = GoogleTextToSpeechSynthesizer(
            _settings(), client_factory=lambda: client
        )

        with self.assertRaises(TTSProviderUnavailableError):
            synthesizer.synthesize(text="Analysis complete.")

    def test_configuration_fingerprint_is_stable(self):
        synthesizer = GoogleTextToSpeechSynthesizer(
            _settings(), client_factory=lambda: FakeGoogleClient()
        )

        first = synthesizer.configuration_fingerprint
        second = synthesizer.configuration_fingerprint

        self.assertEqual(first, second)
        self.assertEqual(len(first), 64)

    def test_rejects_invalid_dependencies(self):
        with self.assertRaises(ValueError):
            GoogleTextToSpeechSynthesizer("invalid")
        with self.assertRaises(ValueError):
            GoogleTextToSpeechSynthesizer(_settings(), client_factory="invalid")


if __name__ == "__main__":
    unittest.main()
