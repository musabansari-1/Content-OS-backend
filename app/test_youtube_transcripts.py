import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.youtube_transcripts import _extract_audio_from_video, transcribe_uploaded_video_with_artifacts


class ExtractAudioFromVideoTests(unittest.TestCase):
    def test_extract_audio_uses_ffmpeg_pcm_wav_for_transcription(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            video_path = Path(temp_dir) / "input.mp4"
            video_path.write_bytes(b"video")

            with patch("app.youtube_transcripts.subprocess.run") as mock_run:
                output_path = _extract_audio_from_video(video_path, Path(temp_dir))

        command = mock_run.call_args.args[0]
        self.assertEqual(command[0], "ffmpeg")
        self.assertIn("-vn", command)
        self.assertIn("pcm_s16le", command)
        self.assertIn("16000", command)
        self.assertIn("1", command)
        self.assertEqual(output_path.suffix, ".wav")

    def test_extract_audio_surfaces_ffmpeg_stderr_on_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            video_path = Path(temp_dir) / "input.mp4"
            video_path.write_bytes(b"video")

            with patch(
                "app.youtube_transcripts.subprocess.run",
                side_effect=subprocess.CalledProcessError(
                    1,
                    ["ffmpeg"],
                    stderr="decoder failure",
                ),
            ):
                with self.assertRaises(RuntimeError) as context:
                    _extract_audio_from_video(video_path, Path(temp_dir))

        self.assertIn("decoder failure", str(context.exception))


class TranscribeUploadedVideoTests(unittest.TestCase):
    def test_uploaded_video_prefers_audio_first_transcription(self) -> None:
        fake_upload = SimpleNamespace(
            filename="clip.mp4",
            content_type="video/mp4",
            file=SimpleNamespace(),
        )
        stored_path = Path("stored.mp4")
        audio_path = Path("audio.wav")
        saved_path = Path("bundle.json")
        bundle = {"text": "hello world", "segments": []}

        with patch("app.youtube_transcripts._store_uploaded_video", return_value=stored_path), patch(
            "app.youtube_transcripts._extract_audio_from_video", return_value=audio_path
        ) as mock_extract, patch(
            "app.youtube_transcripts._transcribe_audio_with_groq_bundle", return_value=dict(bundle)
        ) as mock_transcribe_audio, patch(
            "app.youtube_transcripts._transcribe_media_with_groq_bundle"
        ) as mock_transcribe_video, patch(
            "app.youtube_transcripts._save_groq_transcription_bundle", return_value=saved_path
        ):
            text, transcription_path, result_bundle, returned_stored_path = transcribe_uploaded_video_with_artifacts(
                fake_upload
            )

        mock_extract.assert_called_once_with(stored_path, stored_path.parent)
        mock_transcribe_audio.assert_called_once_with(audio_path)
        mock_transcribe_video.assert_not_called()
        self.assertEqual(text, "hello world")
        self.assertEqual(transcription_path, saved_path)
        self.assertEqual(returned_stored_path, stored_path)
        self.assertEqual(result_bundle["source"], "audio_first")

    def test_uploaded_video_falls_back_to_direct_video_if_audio_first_fails(self) -> None:
        fake_upload = SimpleNamespace(
            filename="clip.mp4",
            content_type="video/mp4",
            file=SimpleNamespace(),
        )
        stored_path = Path("stored.mp4")
        saved_path = Path("bundle.json")

        with patch("app.youtube_transcripts._store_uploaded_video", return_value=stored_path), patch(
            "app.youtube_transcripts._extract_audio_from_video",
            side_effect=RuntimeError("ffmpeg failed"),
        ), patch(
            "app.youtube_transcripts._transcribe_media_with_groq_bundle",
            return_value={"text": "fallback transcript", "segments": []},
        ) as mock_transcribe_video, patch(
            "app.youtube_transcripts._save_groq_transcription_bundle", return_value=saved_path
        ):
            text, transcription_path, result_bundle, returned_stored_path = transcribe_uploaded_video_with_artifacts(
                fake_upload
            )

        mock_transcribe_video.assert_called_once_with(stored_path)
        self.assertEqual(text, "fallback transcript")
        self.assertEqual(transcription_path, saved_path)
        self.assertEqual(returned_stored_path, stored_path)
        self.assertEqual(result_bundle["source"], "video_fallback")
