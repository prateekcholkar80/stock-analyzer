import math
import unittest

from elevenlabs.errors.bad_request_error import BadRequestError
from elevenlabs.errors.forbidden_error import ForbiddenError
from elevenlabs.errors.not_found_error import NotFoundError
from elevenlabs.errors.unauthorized_error import UnauthorizedError
from elevenlabs.errors.unprocessable_entity_error import UnprocessableEntityError

from app.exceptions import (
    STTAuthenticationError,
    STTConfigurationError,
    STTProviderUnavailableError,
)
from app.stt.adapters.elevenlabs_stt import ElevenLabsSpeechToTextTranscriber
from app.stt.config import SpeechToTextSettings
from app.stt.gateway import Transcription


class FakeWord:
    def __init__(self, text, *, type="word", logprob=None):
        self.text = text
        self.type = type
        self.logprob = logprob


class FakeConvertResponse:
    def __init__(self, text, *, language_code="en", words=None):
        self.text = text
        self.language_code = language_code
        self.words = words if words is not None else []


class FakeSpeechToTextEndpoint:
    def __init__(self, response=None, failure=None):
        self.response = response
        self.failure = failure
        self.calls = []

    def convert(self, *, model_id, file, language_code):
        self.calls.append((model_id, file, language_code))
        if self.failure is not None:
            raise self.failure
        return self.response


class FakeElevenLabsClient:
    def __init__(self, response=None, failure=None):
        self.speech_to_text = FakeSpeechToTextEndpoint(
            response=response, failure=failure
        )


def _settings(**overrides):
    values = {
        "provider": "elevenlabs",
        "language_code": "en-IN",
        "audio_encoding": "WEBM_OPUS",
    }
    values.update(overrides)
    return SpeechToTextSettings(**values)


class ElevenLabsSpeechToTextTranscriberTests(unittest.TestCase):
    def test_transcribes_audio_and_wraps_result(self):
        client = FakeElevenLabsClient(
            response=FakeConvertResponse(
                "Analyze Reliance",
                language_code="en",
                words=[
                    FakeWord("Analyze", logprob=-0.1),
                    FakeWord("Reliance", logprob=-0.2),
                ],
            )
        )
        transcriber = ElevenLabsSpeechToTextTranscriber(
            _settings(), client_factory=lambda: client
        )

        transcription = transcriber.transcribe(
            audio=b"real-audio-bytes", media_type="audio/webm"
        )

        self.assertIsInstance(transcription, Transcription)
        self.assertEqual(transcription.transcript, "Analyze Reliance")
        self.assertEqual(transcription.provider, "elevenlabs")
        self.assertEqual(transcription.language_code, "en")
        self.assertAlmostEqual(
            transcription.confidence, math.exp(-0.15), places=6
        )
        self.assertEqual(len(client.speech_to_text.calls), 1)

    def test_sends_default_model_and_primary_language_subtag(self):
        client = FakeElevenLabsClient(
            response=FakeConvertResponse("hello", language_code="en")
        )
        transcriber = ElevenLabsSpeechToTextTranscriber(
            _settings(language_code="en-IN"), client_factory=lambda: client
        )

        transcriber.transcribe(audio=b"real-audio-bytes", media_type="audio/webm")

        model_id, file, language_code = client.speech_to_text.calls[0]
        self.assertEqual(model_id, "scribe_v1")
        self.assertEqual(file, b"real-audio-bytes")
        self.assertEqual(language_code, "en")

    def test_uses_configured_model_id_when_set(self):
        client = FakeElevenLabsClient(response=FakeConvertResponse("hello"))
        transcriber = ElevenLabsSpeechToTextTranscriber(
            _settings(model_id="scribe_v2"), client_factory=lambda: client
        )

        transcriber.transcribe(audio=b"real-audio-bytes", media_type="audio/webm")

        model_id, _, _ = client.speech_to_text.calls[0]
        self.assertEqual(model_id, "scribe_v2")

    def test_no_speech_returns_empty_transcription_not_an_error(self):
        client = FakeElevenLabsClient(
            response=FakeConvertResponse("", language_code=None, words=[])
        )
        transcriber = ElevenLabsSpeechToTextTranscriber(
            _settings(), client_factory=lambda: client
        )

        transcription = transcriber.transcribe(
            audio=b"silence-bytes", media_type="audio/webm"
        )

        self.assertEqual(transcription.transcript, "")
        self.assertIsNone(transcription.confidence)
        # Falls back to the configured language when the provider
        # doesn't report one (e.g. nothing was detected).
        self.assertEqual(transcription.language_code, "en-IN")

    def test_ignores_non_word_entries_when_averaging_confidence(self):
        client = FakeElevenLabsClient(
            response=FakeConvertResponse(
                "Analyze Reliance",
                words=[
                    FakeWord("Analyze", type="word", logprob=-0.1),
                    FakeWord(" ", type="spacing", logprob=None),
                    FakeWord("Reliance", type="word", logprob=-0.1),
                ],
            )
        )
        transcriber = ElevenLabsSpeechToTextTranscriber(
            _settings(), client_factory=lambda: client
        )

        transcription = transcriber.transcribe(
            audio=b"real-audio-bytes", media_type="audio/webm"
        )

        self.assertAlmostEqual(
            transcription.confidence, math.exp(-0.1), places=6
        )

    def test_rejects_blank_audio(self):
        transcriber = ElevenLabsSpeechToTextTranscriber(
            _settings(), client_factory=lambda: FakeElevenLabsClient()
        )

        with self.assertRaises(ValueError):
            transcriber.transcribe(audio=b"", media_type="audio/webm")

    def test_translates_unauthorized_failure(self):
        client = FakeElevenLabsClient(
            failure=UnauthorizedError(body="bad api key")
        )
        transcriber = ElevenLabsSpeechToTextTranscriber(
            _settings(), client_factory=lambda: client
        )

        with self.assertRaises(STTAuthenticationError):
            transcriber.transcribe(audio=b"real-audio-bytes", media_type="audio/webm")

    def test_translates_forbidden_as_authentication_failure(self):
        client = FakeElevenLabsClient(failure=ForbiddenError(body="not allowed"))
        transcriber = ElevenLabsSpeechToTextTranscriber(
            _settings(), client_factory=lambda: client
        )

        with self.assertRaises(STTAuthenticationError):
            transcriber.transcribe(audio=b"real-audio-bytes", media_type="audio/webm")

    def test_translates_bad_request_as_configuration_failure(self):
        client = FakeElevenLabsClient(
            failure=BadRequestError(body="bad language code")
        )
        transcriber = ElevenLabsSpeechToTextTranscriber(
            _settings(), client_factory=lambda: client
        )

        with self.assertRaises(STTConfigurationError):
            transcriber.transcribe(audio=b"real-audio-bytes", media_type="audio/webm")

    def test_translates_not_found_as_configuration_failure(self):
        client = FakeElevenLabsClient(failure=NotFoundError(body="unknown model"))
        transcriber = ElevenLabsSpeechToTextTranscriber(
            _settings(), client_factory=lambda: client
        )

        with self.assertRaises(STTConfigurationError):
            transcriber.transcribe(audio=b"real-audio-bytes", media_type="audio/webm")

    def test_translates_unprocessable_entity_as_configuration_failure(self):
        client = FakeElevenLabsClient(
            failure=UnprocessableEntityError(body="bad payload")
        )
        transcriber = ElevenLabsSpeechToTextTranscriber(
            _settings(), client_factory=lambda: client
        )

        with self.assertRaises(STTConfigurationError):
            transcriber.transcribe(audio=b"real-audio-bytes", media_type="audio/webm")

    def test_translates_unknown_failure_as_provider_unavailable(self):
        client = FakeElevenLabsClient(failure=RuntimeError("something odd"))
        transcriber = ElevenLabsSpeechToTextTranscriber(
            _settings(), client_factory=lambda: client
        )

        with self.assertRaises(STTProviderUnavailableError):
            transcriber.transcribe(audio=b"real-audio-bytes", media_type="audio/webm")

    def test_configuration_fingerprint_is_stable(self):
        transcriber = ElevenLabsSpeechToTextTranscriber(
            _settings(), client_factory=lambda: FakeElevenLabsClient()
        )

        first = transcriber.configuration_fingerprint
        second = transcriber.configuration_fingerprint

        self.assertEqual(first, second)
        self.assertEqual(len(first), 64)

    def test_rejects_invalid_dependencies(self):
        with self.assertRaises(ValueError):
            ElevenLabsSpeechToTextTranscriber("invalid")
        with self.assertRaises(ValueError):
            ElevenLabsSpeechToTextTranscriber(_settings(), client_factory="invalid")


if __name__ == "__main__":
    unittest.main()
