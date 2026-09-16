"""The Gemini Live session: configuration, the audio and tool loops, reconnects.

``build_live_config`` is pure and snapshot-tested. ``Session`` (Task 10) owns
the connection.
"""

from __future__ import annotations

from google.genai import types

from .cli import Config
from .prompt_de import SYSTEM_INSTRUCTION_DE

COMPRESSION_TRIGGER_TOKENS = 100_000


def build_live_config(
    config: Config,
    declarations: list[types.FunctionDeclaration],
    resumption_handle: str | None = None,
    text_only: bool = False,
) -> types.LiveConnectConfig:
    """Live config for the configured listening mode (see the spec, Listening modes)."""
    if config.mode == "open":
        realtime = types.RealtimeInputConfig(
            automatic_activity_detection=types.AutomaticActivityDetection(
                disabled=False,
                start_of_speech_sensitivity=types.StartSensitivity.START_SENSITIVITY_LOW,
                end_of_speech_sensitivity=types.EndSensitivity.END_SENSITIVITY_LOW,
                prefix_padding_ms=100,
                silence_duration_ms=config.silence_ms,
            )
        )
        proactivity = types.ProactivityConfig(proactive_audio=True)
    else:
        realtime = types.RealtimeInputConfig(
            automatic_activity_detection=types.AutomaticActivityDetection(disabled=True)
        )
        proactivity = None
    return types.LiveConnectConfig(
        response_modalities=[types.Modality.TEXT if text_only else types.Modality.AUDIO],
        system_instruction=types.Content(parts=[types.Part(text=SYSTEM_INSTRUCTION_DE)], role="user"),
        speech_config=None
        if text_only
        else types.SpeechConfig(
            voice_config=types.VoiceConfig(prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=config.voice))
        ),
        tools=[types.Tool(function_declarations=declarations)] if declarations else None,
        realtime_input_config=realtime,
        proactivity=proactivity,
        input_audio_transcription=types.AudioTranscriptionConfig(),
        output_audio_transcription=None if text_only else types.AudioTranscriptionConfig(),
        context_window_compression=types.ContextWindowCompressionConfig(
            sliding_window=types.SlidingWindow(), trigger_tokens=COMPRESSION_TRIGGER_TOKENS
        ),
        session_resumption=types.SessionResumptionConfig(handle=resumption_handle),
    )
