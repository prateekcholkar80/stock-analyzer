import unittest

from google.api_core import exceptions as google_exceptions

from app.exceptions import (
    STTAuthenticationError,
    STTConfigurationError,
    STTProviderUnavailableError,
    STTTranscriptionError,
)
from app.stt.adapters.google_stt import GoogleSpeechToTextTranscriber
from app.stt.config import SpeechToTextSettings
from app.stt.gateway import Transcription


class FakeAlternative:
    def __init__(self, transcript, confidence):
        self.transcript = transcript
        self.confidence = confidence


class FakeResult:
    def __init__(self, alternatives):
        self.alternatives = alternatives


class FakeRecognizeResponse:
    def __init__(self, results):
        self.results = results


class FakeGoogleClient:
    def __init__(self, response=None, failure=None):
        self.response = response
        self.failure = failure
        self.calls = []

    def recognize(self, *, config, audio):
        self.calls.append((config, audio))
        if self.failure is not None:
            raise self.failure
        return self.response


def _settings(**overrides):
    values = {
        "provider": "google",
        "language_code": "en-IN",
        "audio_encoding": "WEBM_OPUS",
    }
    values.update(overrides)
    return SpeechToTextSettings(**values)


class GoogleSpeechToTextTranscriberTests(unittest.TestCase):
    def test_transcribes_audio_and_wraps_result(self):
        client = FakeGoogleClient(
            response=FakeRecognizeResponse(
                [FakeResult([FakeAlternative("Analyze Reliance", 0.92)])]
            )
        )
        transcriber = GoogleSpeechToTextTranscriber(
            _settings(), client_factory=lambda: client
        )

        transcription = transcriber.transcribe(
            audio=b"real-audio-bytes", media_type="audio/webm"
        )

        self.assertIsInstance(transcription, Transcription)
        self.assertEqual(transcription.transcript, "Analyze Reliance")
        self.assertEqual(transcription.provider, "google")
        self.assertEqual(transcription.language_code, "en-IN")
        self.assertEqual(transcription.confidence, 0.92)
        self.assertEqual(len(client.calls), 1)

    def test_no_results_returns_empty_transcription_not_an_error(self):
        client = FakeGoogleClient(response=FakeRecognizeResponse([]))
        transcriber = GoogleSpeechToTextTranscriber(
            _settings(), client_factory=lambda: client
        )

        transcription = transcriber.transcribe(
            audio=b"silence-bytes", media_type="audio/webm"
        )

        self.assertEqual(transcription.transcript, "")
        self.assertIsNone(transcription.confidence)

    def test_result_without_alternatives_raises_transcription_error(self):
        client = FakeGoogleClient(
            response=FakeRecognizeResponse([FakeResult([])])
        )
        transcriber = GoogleSpeechToTextTranscriber(
            _settings(), client_factory=lambda: client
        )

        with self.assertRaises(STTTranscriptionError):
            transcriber.transcribe(audio=b"real-audio-bytes", media_type="audio/webm")

    def test_rejects_blank_audio(self):
        transcriber = GoogleSpeechToTextTranscriber(
            _settings(), client_factory=lambda: FakeGoogleClient()
        )

        with self.assertRaises(ValueError):
            transcriber.transcribe(audio=b"", media_type="audio/webm")

    def test_rejects_unsupported_audio_encoding_at_construction(self):
        with self.assertRaises(STTConfigurationError):
            GoogleSpeechToTextTranscriber(
                _settings(audio_encoding="NOT_A_REAL_ENCODING"),
                client_factory=lambda: FakeGoogleClient(),
            )

    def test_translates_authentication_failure(self):
        client = FakeGoogleClient(
            failure=google_exceptions.Unauthenticated("bad credentials")
        )
        transcriber = GoogleSpeechToTextTranscriber(
            _settings(), client_factory=lambda: client
        )

        with self.assertRaises(STTAuthenticationError):
            transcriber.transcribe(audio=b"real-audio-bytes", media_type="audio/webm")

    def test_translates_permission_denied_as_authentication_failure(self):
        client = FakeGoogleClient(
            failure=google_exceptions.PermissionDenied("not allowed")
        )
        transcriber = GoogleSpeechToTextTranscriber(
            _settings(), client_factory=lambda: client
        )

        with self.assertRaises(STTAuthenticationError):
            transcriber.transcribe(audio=b"real-audio-bytes", media_type="audio/webm")

    def test_translates_service_unavailable_failure(self):
        client = FakeGoogleClient(
            failure=google_exceptions.ServiceUnavailable("down")
        )
        transcriber = GoogleSpeechToTextTranscriber(
            _settings(), client_factory=lambda: client
        )

        with self.assertRaises(STTProviderUnavailableError):
            transcriber.transcribe(audio=b"real-audio-bytes", media_type="audio/webm")

    def test_translates_invalid_argument_as_configuration_failure(self):
        client = FakeGoogleClient(
            failure=google_exceptions.InvalidArgument("bad language code")
        )
        transcriber = GoogleSpeechToTextTranscriber(
            _settings(), client_factory=lambda: client
        )

        with self.assertRaises(STTConfigurationError):
            transcriber.transcribe(audio=b"real-audio-bytes", media_type="audio/webm")

    def test_translates_unknown_failure_as_provider_unavailable(self):
        client = FakeGoogleClient(failure=RuntimeError("something odd"))
        transcriber = GoogleSpeechToTextTranscriber(
            _settings(), client_factory=lambda: client
        )

        with self.assertRaises(STTProviderUnavailableError):
            transcriber.transcribe(audio=b"real-audio-bytes", media_type="audio/webm")

    def test_configuration_fingerprint_is_stable(self):
        transcriber = GoogleSpeechToTextTranscriber(
            _settings(), client_factory=lambda: FakeGoogleClient()
        )

        first = transcriber.configuration_fingerprint
        second = transcriber.configuration_fingerprint

        self.assertEqual(first, second)
        self.assertEqual(len(first), 64)

    def test_rejects_invalid_dependencies(self):
        with self.assertRaises(ValueError):
            GoogleSpeechToTextTranscriber("invalid")
        with self.assertRaises(ValueError):
            GoogleSpeechToTextTranscriber(_settings(), client_factory="invalid")


if __name__ == "__main__":
    unittest.main()
