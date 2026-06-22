import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.youtube_transcripts import _extract_audio_from_video


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
